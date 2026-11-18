"""EDA helpers (guide 5-2, 5-3). Pure functions over DataFrames loaded from the DB."""

from __future__ import annotations

import pandas as pd


def weekly_counts(published_at: pd.Series, start: str | None = None, end: str | None = None) -> pd.Series:
    """Article count per week (weeks starting Monday), including zero weeks."""
    dates = pd.to_datetime(published_at)
    weeks = dates.dt.to_period("W-SUN").dt.start_time
    counts = weeks.value_counts().sort_index()
    lo = pd.Timestamp(start) if start else counts.index.min()
    hi = pd.Timestamp(end) if end else counts.index.max()
    full = pd.date_range(lo - pd.Timedelta(days=lo.weekday()), hi, freq="W-MON")
    return counts.reindex(full, fill_value=0).rename("count")


def detect_spikes(counts: pd.Series, baseline_weeks: int = 8, sigma: float = 2.0) -> pd.DataFrame:
    """Flag weeks whose count exceeds mean + sigma*std of the preceding `baseline_weeks` weeks.

    The baseline excludes the current week (shift(1)) so a spike does not raise its own threshold.
    Weeks without a full baseline are never flagged. Candidates still need a human to read
    the titles and rule out collection artifacts (e.g. Naver 1,000-item truncation).
    """
    prev = counts.shift(1).rolling(baseline_weeks, min_periods=baseline_weeks)
    out = pd.DataFrame({"count": counts, "baseline_mean": prev.mean(), "baseline_std": prev.std(ddof=0)})
    out["threshold"] = out["baseline_mean"] + sigma * out["baseline_std"]
    out["is_spike"] = out["threshold"].notna() & (out["count"] > out["threshold"]) & (out["count"] > 0)
    return out


# ---------------------------------------------------------------- news (guide 5-2)

def news_frame(docs: list[dict]) -> pd.DataFrame:
    """documents rows (source_type='news') -> DataFrame with month/week and company count."""
    df = pd.DataFrame(docs)
    if df.empty:
        return pd.DataFrame(columns=["doc_id", "title", "published_at", "company_ids", "search_keyword",
                                     "dup_group_id", "is_relevant", "month", "n_companies"])
    df["published_at"] = pd.to_datetime(df["published_at"])
    df["month"] = df["published_at"].dt.to_period("M").astype(str)
    df["n_companies"] = df["company_ids"].map(len)
    return df


def attach_firm_scope(df: pd.DataFrame, scope_rows: list[dict], member_to_company: dict[str, str]) -> pd.DataFrame:
    """Add company_scope and kr_company_ids from scripts/tag_news_firms.py output.

    kr_company_ids lists firms only when the article is about the KOREAN member firm
    (company_scope == member_firm), mapped from member_firm_ids (e.g. pwc_korea -> pwc).
    Global-network and industry-wide articles get an empty list (principle 6).
    """
    raw = {r["doc_id"]: r for r in scope_rows}

    def resolve(doc_id: str) -> dict | None:
        # The DB may hold the copy that tagging marked exact_duplicate; use the kept original's tags.
        r = raw.get(doc_id)
        if r and r.get("exclude_reason") == "exact_duplicate" and r.get("duplicate_of") in raw:
            return raw[r["duplicate_of"]]
        return r

    out = df.copy()
    out["company_scope"] = out["doc_id"].map(lambda d: (resolve(d) or {}).get("company_scope", "untagged"))

    def kr(doc_id: str) -> list[str]:
        r = resolve(doc_id)
        if not r or r.get("company_scope") != "member_firm":
            return []
        return sorted({member_to_company[m] for m in r.get("member_firm_ids", []) if m in member_to_company})

    out["kr_company_ids"] = out["doc_id"].map(kr)
    return out


def scope_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Relevant (refined) articles by company_scope."""
    rel = df[df["is_relevant"]]
    return rel.groupby("company_scope")["doc_id"].size().rename("articles").to_frame()


def keyword_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Articles per search keyword group and the share judged irrelevant."""
    g = df.groupby("search_keyword")["is_relevant"].agg(total="size", relevant="sum")
    g["irrelevant_share"] = (1 - g["relevant"] / g["total"]).round(3)
    return g.sort_values("total", ascending=False)


def company_monthly(df: pd.DataFrame, company_col: str = "kr_company_ids") -> pd.DataFrame:
    """Relevant articles per firm and month; multi-firm articles count once per firm.

    Uses Korean member-firm attribution by default (attach_firm_scope); falls back to brand
    matches (company_ids) if scope tagging is absent. single_company is the basis for per-firm
    sentiment later (guide 6-3).
    """
    col = company_col if company_col in df else "company_ids"
    rel = df[df["is_relevant"]].copy()
    rel["n_firms"] = rel[col].map(len)
    rel = rel.explode(col).dropna(subset=[col])
    out = rel.groupby(["month", col]).agg(
        articles=("doc_id", "size"), single_company=("n_firms", lambda s: int((s == 1).sum()))
    )
    return out.reset_index().rename(columns={col: "company_id"})


def dup_summary(df: pd.DataFrame) -> dict[str, float]:
    sizes = df.groupby("dup_group_id")["doc_id"].size()
    in_groups = int(sizes[sizes > 1].sum())
    return {
        "articles": len(df),
        "groups": int(len(sizes)),
        "articles_in_multi_article_groups": in_groups,
        "share_in_multi_article_groups": round(in_groups / len(df), 3) if len(df) else 0.0,
    }


def spike_candidates(
    df: pd.DataFrame, baseline_weeks: int = 8, sigma: float = 2.0, top_titles: int = 5,
    start: str | None = None, end: str | None = None,
) -> pd.DataFrame:
    """Spike weeks of relevant articles, each with representative titles (one per duplicate group,
    largest groups first) so a person can read and record the background (guide 5-3)."""
    rel = df[df["is_relevant"]]
    if rel.empty:
        return pd.DataFrame(columns=["week", "count", "threshold", "titles"])
    flagged = detect_spikes(weekly_counts(rel["published_at"], start, end), baseline_weeks, sigma)
    rows = []
    week_start = rel["published_at"].dt.to_period("W-SUN").dt.start_time
    for week, r in flagged[flagged["is_spike"]].iterrows():
        in_week = rel[week_start == week]
        groups = in_week.groupby("dup_group_id").agg(n=("doc_id", "size"), title=("title", "first"))
        titles = groups.sort_values("n", ascending=False)["title"].head(top_titles).tolist()
        rows.append({"week": week.date().isoformat(), "count": int(r["count"]),
                     "threshold": round(float(r["threshold"]), 1), "titles": titles})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- DART audit filings (B-1)

FIRMS = ["pwc", "kpmg", "ey", "deloitte"]


def audit_share_by_year(monthly: pd.DataFrame, months: range = range(1, 11)) -> pd.DataFrame:
    """Audit-report filings per filer and year over the same months each year (seasonality-safe).

    `monthly` is data/processed/audit_filings_monthly.csv (month, pblntf_ty, filer, count).
    Shares are by COUNT, not revenue.
    """
    m = monthly.copy()
    m["year"] = m["month"].str[:4]
    m = m[m["month"].str[5:7].astype(int).isin(list(months))]
    p = m.pivot_table(index="year", columns="filer", values="count", aggfunc="sum", fill_value=0)
    for f in FIRMS + ["other"]:
        if f not in p:
            p[f] = 0
    p["total"] = p[FIRMS + ["other"]].sum(axis=1)
    p["big4_share"] = (p[FIRMS].sum(axis=1) / p["total"]).round(4)
    big4 = p[FIRMS].sum(axis=1).replace(0, pd.NA)
    for f in FIRMS:
        p[f"{f}_share_within_big4"] = (p[f] / big4).astype(float).round(4)
    return p


def news_vs_audit_yearly(news: pd.DataFrame, monthly: pd.DataFrame, months: range = range(1, 11),
                         company_col: str = "kr_company_ids") -> pd.DataFrame:
    """Per firm and year (same months each year): relevant Korean member-firm articles vs audit filings.

    Audit filings are by the Korean firms, so global-network news is excluded (principle 6).
    A side-by-side comparison, not a causal link: the two series measure different things.
    """
    col = company_col if company_col in news else "company_ids"
    rel = news[news["is_relevant"]].explode(col).dropna(subset=[col])
    rel = rel[rel["published_at"].dt.month.isin(list(months))]
    n = rel.groupby([rel["published_at"].dt.year.astype(str), col]).size().rename("articles")
    a = monthly[monthly["filer"].isin(FIRMS) & monthly["month"].str[5:7].astype(int).isin(list(months))]
    f = a.groupby([a["month"].str[:4], "filer"])["count"].sum().rename("audit_filings")
    n.index.names = f.index.names = ["year", "company_id"]
    return pd.concat([n, f], axis=1).fillna(0).astype(int).reset_index()
