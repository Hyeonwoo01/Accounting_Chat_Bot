"""Day 3 EDA (guide 5-2, 5-3). Reads the DB and writes LOCAL-ONLY outputs.

Usage: python scripts/run_eda.py   (after tag_news_firms.py and process_data.py)

Relevance and firm attribution come from the reviewed refinement (scripts/tag_news_firms.py,
src/preprocessing/firm_scope.py): is_relevant is set by process_data.py, and per-firm counts use
only articles about the KOREAN member firm (company_scope == member_firm).

Outputs go to data/processed/eda/ (git-ignored) because they contain Naver news titles and
derived data, which the team keeps local (root CLAUDE.md checklist):
  eda_report.md       summary tables + spike weeks with titles (rewritten every run)
  spike_notes.md      human notes per spike week — created once, new weeks appended, never overwritten
  keyword_summary.csv, scope_summary.csv, company_monthly.csv, spikes.csv,
  audit_share_by_year.csv, news_vs_audit_yearly.csv
"""

from __future__ import annotations

import json
import sys
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from src.analysis import eda
from src.common.config import (
    PROCESSED_DIR,
    get_big4_corp_codes,
    get_period,
    load_company_mapping,
    load_settings,
)
from src.database import db_manager
from src.preprocessing.company_matcher import CompanyMatcher, filer_identification

OUT = PROCESSED_DIR / "eda"
FIRM_SCOPE_FILE = "news_firm_scope.jsonl"


def _md(df: pd.DataFrame) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    if df.empty:
        return "_없음_"
    flat = df.reset_index()
    header = "| " + " | ".join(map(str, flat.columns)) + " |"
    sep = "|" + "---|" * len(flat.columns)
    rows = ["| " + " | ".join(str(v) for v in row) + " |" for row in flat.itertuples(index=False)]
    return "\n".join([header, sep, *rows])


def load_scope_rows(path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


SPIKE_TYPES = "법인 행사·보도자료 / 규제·감독 사건 / 실적·수주 / 제재·사고 / 인사 / 수집 방식 영향 / 기타"


def update_spike_notes(path, groups) -> int:
    """Human notes on spike weeks, kept across runs: create once, then only append new weeks.

    Entries are keyed by the '### <label> <week>' header; existing text is never rewritten.
    Returns the number of entries added.
    """
    existing = path.read_text(encoding="utf-8") if path.exists() else (
        "# 이슈 급증 배경 기록 (로컬 전용 — 커밋·공유 금지)\n\n"
        "run_eda.py는 이 파일을 덮어쓰지 않고 새 급증 주만 뒤에 추가한다. 항목마다 아래를 채운다.\n"
        f"- 유형: {SPIKE_TYPES}\n"
        "- 주 법인: 한 법인 주도인지, 업계 전체인지\n"
        "- 배경: 사건을 한 줄로. 확인한 사실만 적는다\n\n"
    )
    added = []
    for label, sp in groups:
        for _, r in sp.iterrows():
            header = f"### {label} {r['week']}"
            if header in existing:
                continue
            added += [header, f"- {r['count']}건 (기준 {r['threshold']})",
                      *[f"  - {t}" for t in r["titles"]],
                      "- 유형: ", "- 주 법인: ", "- 관련 카테고리: ", "- 배경: ", "- 수집 영향: ", ""]
    if added or not path.exists():
        path.write_text(existing + "\n".join(added) + ("\n" if added else ""), encoding="utf-8")
    return sum(1 for line in added if line.startswith("### "))


def member_to_company(mapping: dict) -> dict[str, str]:
    return {m["member_firm_id"]: c["company_id"]
            for c in mapping["companies"] for m in c.get("member_firms", [])}


def main() -> int:
    settings = load_settings()
    period = get_period()
    spike_cfg = settings["spike_detection"]
    with db_manager.connect() as conn:
        news_docs = db_manager.fetch_documents(conn, "news")
        dart_docs = db_manager.fetch_documents(conn, "dart")
    monthly_path = PROCESSED_DIR / "audit_filings_monthly.csv"
    monthly = pd.read_csv(monthly_path, dtype={"month": str}) if monthly_path.exists() else pd.DataFrame(
        columns=["month", "pblntf_ty", "filer", "count"])

    mapping = load_company_mapping()
    scope_rows = load_scope_rows(PROCESSED_DIR / FIRM_SCOPE_FILE)
    news = eda.news_frame(news_docs)
    if not news.empty:
        news = eda.attach_firm_scope(news, scope_rows, member_to_company(mapping))
    OUT.mkdir(parents=True, exist_ok=True)
    start, end = period.start.isoformat(), period.end.isoformat()

    kw = eda.keyword_summary(news) if not news.empty else pd.DataFrame()
    cm = eda.company_monthly(news) if not news.empty else pd.DataFrame()
    dup = eda.dup_summary(news) if not news.empty else {}
    spikes = eda.spike_candidates(news, spike_cfg["baseline_weeks"], spike_cfg["sigma"], start=start, end=end)
    per_company = {
        c: eda.spike_candidates(news[news["kr_company_ids"].map(lambda x, c=c: c in x)],
                                spike_cfg["baseline_weeks"], spike_cfg["sigma"], start=start, end=end)
        for c in eda.FIRMS
    } if not news.empty else {}
    share = eda.audit_share_by_year(monthly) if not monthly.empty else pd.DataFrame()
    vs = eda.news_vs_audit_yearly(news, monthly) if not news.empty and not monthly.empty else pd.DataFrame()
    ident = filer_identification(dart_docs, CompanyMatcher(mapping), get_big4_corp_codes())
    scope = eda.scope_summary(news) if not news.empty else pd.DataFrame()

    kw.to_csv(OUT / "keyword_summary.csv", encoding="utf-8-sig")
    scope.to_csv(OUT / "scope_summary.csv", encoding="utf-8-sig")
    cm.to_csv(OUT / "company_monthly.csv", index=False, encoding="utf-8-sig")
    spikes.to_csv(OUT / "spikes.csv", index=False, encoding="utf-8-sig")
    share.to_csv(OUT / "audit_share_by_year.csv", encoding="utf-8-sig")
    vs.to_csv(OUT / "news_vs_audit_yearly.csv", index=False, encoding="utf-8-sig")

    n_rel = int(news["is_relevant"].sum()) if not news.empty else 0
    company_totals = cm.groupby("company_id")[["articles", "single_company"]].sum() if not cm.empty else pd.DataFrame()
    spike_lines = []
    for label, sp in [("전체", spikes), *per_company.items()]:
        spike_lines.append(f"### {label}: 후보 {len(sp)}주")
        for _, r in sp.iterrows():
            spike_lines.append(f"- **{r['week']}** 주 {r['count']}건 (기준 {r['threshold']})")
            spike_lines += [f"  - {t}" for t in r["titles"]]
    notes_added = update_spike_notes(OUT / "spike_notes.md", [("전체", spikes), *per_company.items()])
    lines = [
        "# Day 3 EDA 리포트 (로컬 전용 — 뉴스 제목·파생 데이터 포함, 커밋·공유 금지)",
        "",
        f"- 생성: {datetime.now():%Y-%m-%d %H:%M}, 기간 {start} ~ {end}",
        f"- 뉴스 {len(news):,}건 중 정제본(관련) {n_rel:,}건 ({n_rel / len(news):.1%}) — 기준: tag_news_firms.py 정제"
        if len(news) else "- 뉴스 없음",
        "" if scope_rows else "- ⚠️ news_firm_scope.jsonl 없음: tag_news_firms.py → process_data.py 를 먼저 실행",
        f"- 급증 기준: 직전 {spike_cfg['baseline_weeks']}주 평균 + {spike_cfg['sigma']}σ (관련 기사 기준)",
        "",
        "## 1. 키워드 묶음별 기사량과 정제에서 빠진 비중",
        "", _md(kw), "",
        "## 2. 정제본의 법인 범위 (member_firm=한국 회원 법인, global_network=글로벌, industry_wide=업계 전체)",
        "", _md(scope), "",
        "## 2-1. 한국 회원 법인별 기사 수 (전체 기간, 여러 법인 기사는 법인마다 1건)",
        "", _md(company_totals), "",
        "## 3. 중복·유사 기사",
        "", "```", json.dumps(dup, ensure_ascii=False, indent=1), "```", "",
        "## 4. 이슈 급증 후보 — 배경은 `spike_notes.md`에 기록 (이 파일은 실행마다 새로 써짐)",
        "", *spike_lines, "",
        "## 5. 감사보고서 제출 건수 — 연도별(1~10월, 건수 기준, 계절성 통제)",
        "", _md(share), "",
        "## 6. 한국 회원 법인 기사 vs 감사보고서 제출 (연도·법인별 1~10월, 단순 병렬 비교 — 인과 아님)",
        "", _md(vs.set_index(["year", "company_id"])) if not vs.empty else "_없음_", "",
        "## 7. DART 원문에서 제출 법인 식별률 (메타데이터 flr_nm 기준)",
        "", f"- 확인 {ident['checked']}건, 식별 {ident['found']}건, 비율 {ident['rate']}",
        *[f"- 미식별: {m['rcept_no']} {m['title']} (제출 {m['filer']})" for m in ident["misses"]], "",
        "## 8. 사람이 할 일",
        "",
        "- 정제·법인 판정 정확도는 `evaluation/news_review_guide.md` 절차로 검수 (`make_news_review_sample.py`)",
        "- 4절 급증 후보마다 배경 사건을 한 줄로 기록 (Day 3 종료 조건)",
    ]
    (OUT / "eda_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"spike notes: {notes_added} new week(s) added to {OUT / 'spike_notes.md'}")
    print(f"news={len(news):,} relevant={n_rel:,} spikes(all)={len(spikes)} "
          f"filer-id={ident['found']}/{ident['checked']} -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
