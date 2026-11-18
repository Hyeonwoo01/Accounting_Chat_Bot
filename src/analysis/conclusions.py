"""Conclusion drafts for the dashboard header (guide 8). Batch only — never called by the dashboard.

Flow: stored aggregates -> numbered FACTS -> LLM drafts conclusions that cite fact ids -> two people
review the drafts against the evidence documents -> only rows both approved go to `insights`.

What the LLM sees: aggregate numbers and the human-written spike backgrounds. It never sees article
titles or summaries (Naver terms; team decision in evaluation/gate_decision.md). Evidence doc_ids are
attached locally from the cited facts, so the model cannot invent a source.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.analysis.llm_classifier import ChatFn

PROMPT_VERSION = "insight-v1"
KINDS = ("사실", "해석")
FIRMS = ("pwc", "kpmg", "ey", "deloitte")
# Words people type in Excel to approve a draft (case-insensitive).
APPROVE = {"o", "○", "y", "yes", "1", "예", "승인"}

SYSTEM_PROMPT = """당신은 회계·컨설팅(전문서비스) 산업 대시보드의 결론 문장 초안을 쓰는 분석가다.
아래 [사실 목록]만 근거로 쓴다. 목록에 없는 사건·수치·원인을 추가하지 않는다.

[규칙]
1. 결론은 4~8개. 각 결론은 한국어 1~2문장이고, 근거로 쓴 사실의 fact_id를 1개 이상 적는다.
2. 숫자는 사실 목록의 값을 그대로 쓴다. 비중은 소수(0.123)를 백분율(12.3%)로 바꿔 써도 된다. 새로 계산한 숫자는 쓰지 않는다.
3. kind는 "사실"(목록의 수치·기록을 그대로 전함) 또는 "해석"(사실에서 추론함)이다. 해석은 "~로 보인다"처럼 단정하지 않는다.
4. 기사 수는 보도 노출량이다. 경쟁력·순위·점유율로 쓰지 않는다.
5. 감사보고서 제출 건수는 건수 기준이며 매출 점유율이 아니다. 감사보고서는 감사받은 고객사의 공시이므로 Big4의 실적·행동으로 쓰지 않는다.
6. 법인별 수치는 한국 회원 법인 기사만 센 값이다. 글로벌 브랜드 발표를 한국 법인의 실적으로 쓰지 않는다.
7. 감성 비중은 LLM 분류 결과이며 보조 지표다. 감성만으로 법인의 평판·성과를 단정하지 않는다.
8. 급증 주의 background는 사람이 기사를 읽고 적은 요약이다. 인용할 때 "보도 기준"임을 드러낸다.
9. 연도 비교는 사실 목록에 적힌 같은 기간(예: 1~10월)끼리만 한다.

[출력] 아래 JSON 객체 하나만 출력한다.
{"conclusions": [{"period": "결론이 다루는 기간(예: 2024-01~2026-10, 2025-03)", "kind": "사실|해석",
  "text": "결론 문장", "fact_ids": ["F01"]}]}
"""


class DraftError(ValueError):
    """The model's answer is not a valid conclusions object."""


@dataclass
class Fact:
    fact_id: str
    kind: str                     # category_mix | firm_sentiment | spike | audit_filings
    period: str
    data: dict[str, Any]          # sent to the LLM: aggregates and human-written notes only
    evidence_doc_ids: list[str] = field(default_factory=list)   # local only, never sent

    def for_prompt(self) -> dict[str, Any]:
        return {"fact_id": self.fact_id, "kind": self.kind, "period": self.period, "data": self.data}


# ---------------------------------------------------------------- facts

def top_events(news: pd.DataFrame, n: int) -> list[str]:
    """doc_ids of the n largest near-duplicate groups (earliest article of each), largest first."""
    if news.empty or n <= 0:
        return []
    df = news.assign(group=news["dup_group_id"].fillna(news["doc_id"]))
    sizes = df.groupby("group")["doc_id"].size()
    first = df.sort_values(["published_at", "doc_id"]).groupby("group")["doc_id"].first()
    order = sizes.sort_values(ascending=False, kind="stable").index
    return [first[g] for g in order[:n]]


def _in_months(dates: pd.Series, year: int, months: Iterable[int]) -> pd.Series:
    return (dates.dt.year == year) & dates.dt.month.isin(list(months))


def _span(year: int, months: Iterable[int]) -> str:
    m = sorted(months)
    return f"{year}-{m[0]:02d}~{year}-{m[-1]:02d}"


def category_facts(news: pd.DataFrame, years: Iterable[int], months: Iterable[int],
                   labels: dict[str, str], evidence_per_category: int = 1) -> list[Fact]:
    """Category counts and shares per year over the same months (LLM labels of relevant articles)."""
    labeled = news[news["category"].notna()]
    facts = []
    for year in years:
        sub = labeled[_in_months(labeled["published_at"], year, months)]
        if sub.empty:
            continue
        counts = sub["category"].value_counts()
        total = int(counts.sum())
        evidence = [d for cat in labels for d in top_events(sub[sub["category"] == cat], evidence_per_category)]
        facts.append(Fact("", "category_mix", _span(year, months), {
            "설명": "관련 기사(정제본)의 LLM 카테고리 분포. 같은 사건의 반복 기사도 각각 1건으로 센다.",
            "분류된 기사 수": total,
            "카테고리별 기사 수": {labels[c]: int(counts.get(c, 0)) for c in labels},
            "카테고리별 비중": {labels[c]: round(int(counts.get(c, 0)) / total, 3) for c in labels},
        }, evidence))
    return facts


def sentiment_facts(news: pd.DataFrame, years: Iterable[int], months: Iterable[int], firm_labels: dict[str, str],
                    min_articles: int = 20) -> list[Fact]:
    """Per firm and year: sentiment shares of articles about ONE Korean member firm (guide 6-3)."""
    single = news[news["kr_company_ids"].map(len) == 1].assign(firm=lambda d: d["kr_company_ids"].map(lambda x: x[0]))
    single = single[single["sentiment"].notna()]
    facts = []
    for year in years:
        in_year = single[_in_months(single["published_at"], year, months)]
        for firm in FIRMS:
            sub = in_year[in_year["firm"] == firm]
            if len(sub) < min_articles:
                continue
            share = sub["sentiment"].value_counts(normalize=True)
            evidence = (top_events(sub[sub["sentiment"] == "negative"], 1)
                        + top_events(sub[sub["sentiment"] == "positive"], 1))
            facts.append(Fact("", "firm_sentiment", _span(year, months), {
                "설명": "한 법인만 다룬 한국 회원 법인 기사의 LLM 감성 비중(보조 지표, 표본 검수 정확도 0.75~0.80).",
                "법인": firm_labels.get(firm, firm),
                "기사 수": int(len(sub)),
                "긍정 비중": round(float(share.get("positive", 0.0)), 3),
                "중립 비중": round(float(share.get("neutral", 0.0)), 3),
                "부정 비중": round(float(share.get("negative", 0.0)), 3),
            }, evidence))
    return facts


def spike_facts(spikes: list[dict], news: pd.DataFrame, firm_labels: dict[str, str],
                evidence_per_spike: int = 3) -> list[Fact]:
    """Spike weeks that have a human-written background (weeks without one are not concluded on)."""
    week_start = news["published_at"].dt.to_period("W-SUN").dt.start_time
    facts = []
    for s in spikes:
        if not s.get("background"):
            continue
        in_week = news[week_start == pd.Timestamp(s["week"])]
        if s["scope"] != "all":
            in_week = in_week[in_week["kr_company_ids"].map(lambda ids, f=s["scope"]: f in ids)]
        facts.append(Fact("", "spike", s["week"], {
            "설명": "주간 기사 수가 직전 8주 평균 + 2σ(기준)를 넘은 주. background는 사람이 대표 기사를 읽고 적은 요약.",
            "범위": "전체" if s["scope"] == "all" else firm_labels.get(s["scope"], s["scope"]),
            "주 시작일": s["week"],
            "기사 수": int(s["count"]),
            "기준": round(float(s["threshold"]), 1),
            "유형": s.get("spike_type"),
            "주 법인": s.get("leading_firm"),
            "background": s["background"],
        }, top_events(in_week, evidence_per_spike)))
    return facts


def audit_facts(monthly: pd.DataFrame, dart: pd.DataFrame, years: Iterable[int], months: Iterable[int],
                firm_labels: dict[str, str]) -> list[Fact]:
    """Audit-report filings per Big4 firm and year (same months each year), as a share of ALL filings."""
    m = monthly.copy()
    m["year"] = m["month"].str[:4].astype(int)
    m = m[m["month"].str[5:7].astype(int).isin(list(months))]
    clients = dart[dart["filing_role"] == "audit_client"]
    facts = []
    for year in years:
        sub = m[m["year"] == year]
        total = int(sub["count"].sum())
        if total == 0:
            continue
        by_filer = sub.groupby("filer")["count"].sum()
        in_year = clients[_in_months(clients["rcept_dt"], year, months)]
        evidence = [in_year[in_year["company_id"] == f].sort_values("rcept_dt")["doc_id"].iloc[0]
                    for f in FIRMS if (in_year["company_id"] == f).any()]
        facts.append(Fact("", "audit_filings", _span(year, months), {
            "설명": "DART 감사보고서 제출 건수(제출 법인 기준). 건수 기준이며 매출 점유율이 아니다. "
                    "감사보고서는 감사받은 고객사의 공시다. 3~5월 집중은 결산 일정에 따른 계절성.",
            "전체 제출 건수(비Big4 포함)": total,
            "법인별 제출 건수": {firm_labels.get(f, f): int(by_filer.get(f, 0)) for f in FIRMS},
            "법인별 비중(전체 대비)": {firm_labels.get(f, f): round(int(by_filer.get(f, 0)) / total, 3) for f in FIRMS},
        }, evidence))
    return facts


def number_facts(facts: list[Fact]) -> list[Fact]:
    """Give facts stable ids F01, F02, ... in list order."""
    for i, f in enumerate(facts, 1):
        f.fact_id = f"F{i:02d}"
    return facts


# ---------------------------------------------------------------- LLM draft

def build_messages(facts: list[Fact]) -> list[dict[str, str]]:
    payload = json.dumps([f.for_prompt() for f in facts], ensure_ascii=False, indent=1)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"[사실 목록]\n{payload}"}]


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_conclusions(text: str, fact_ids: set[str], min_items: int = 1, max_items: int = 10) -> list[dict]:
    """Validate the model answer. Raises DraftError with the reason."""
    m = _JSON_OBJECT.search(text or "")
    if not m:
        raise DraftError("no JSON object in answer")
    try:
        items = json.loads(m.group(0)).get("conclusions")
    except (json.JSONDecodeError, AttributeError) as e:
        raise DraftError(f"invalid JSON: {e}") from e
    if not isinstance(items, list) or not min_items <= len(items) <= max_items:
        raise DraftError(f"conclusions must be a list of {min_items}-{max_items} items")
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            raise DraftError(f"item {i} is not an object")
        text_, period, kind, ids = it.get("text"), it.get("period"), it.get("kind"), it.get("fact_ids")
        if not isinstance(text_, str) or not text_.strip():
            raise DraftError(f"item {i}: empty text")
        if not isinstance(period, str) or not period.strip():
            raise DraftError(f"item {i}: empty period")
        if kind not in KINDS:
            raise DraftError(f"item {i}: bad kind {kind!r}")
        if not isinstance(ids, list) or not ids or any(x not in fact_ids for x in ids):
            raise DraftError(f"item {i}: fact_ids must cite known facts, got {ids!r}")
        out.append({"period": period.strip(), "kind": kind, "text": text_.strip(), "fact_ids": list(dict.fromkeys(ids))})
    return out


def draft(chat: ChatFn, facts: list[Fact], retries: int = 1) -> tuple[list[dict], str]:
    """(conclusions, model name). Retries a malformed answer `retries` times, then raises DraftError."""
    messages = build_messages(facts)
    ids = {f.fact_id for f in facts}
    last = ""
    for _ in range(retries + 1):
        reply = chat(messages)
        try:
            return parse_conclusions(reply.text, ids), reply.model
        except DraftError as e:
            last = str(e)
    raise DraftError(last)


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _norm(num: str) -> str:
    s = num.replace(",", "")
    return s[:-2] if s.endswith(".0") else s


def _fact_numbers(value: Any, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _fact_numbers(k, out)
            _fact_numbers(v, out)
    elif isinstance(value, list):
        for v in value:
            _fact_numbers(v, out)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, (int, float)):
        out.add(_norm(f"{value}"))
        if isinstance(value, float) and 0 <= value <= 1:   # shares may be written as percentages
            pct = value * 100
            out.update({_norm(f"{round(pct, 1)}"), _norm(f"{round(pct)}")})
    else:
        out.update(_norm(n) for n in _NUMBER.findall(str(value)))


def unmatched_numbers(text: str, facts: list[Fact]) -> list[str]:
    """Numbers in a draft that appear in none of its cited facts — flagged for the reviewers to check."""
    known: set[str] = set()
    for f in facts:
        _fact_numbers(f.period, known)
        _fact_numbers(f.data, known)
    known.update(n.lstrip("0") or "0" for n in list(known))   # "2025-03-10" -> "03" also allows "3"
    return [n for n in (_norm(x) for x in _NUMBER.findall(text)) if n not in known]


DRAFT_COLUMNS = ("draft_id", "source", "period", "kind", "conclusion_text", "fact_ids", "evidence_doc_ids",
                 "evidence_preview", "number_check", "reviewer_A", "reviewer_B", "final_text",
                 "final_evidence_doc_ids", "note", "model_version", "prompt_version", "created_at")


def draft_rows(conclusions: list[dict], facts: list[Fact], model: str, created_at: str,
               preview: dict[str, str]) -> list[dict]:
    """Review sheet rows. Evidence = union of the cited facts' doc_ids; preview shows what each doc is."""
    by_id = {f.fact_id: f for f in facts}
    rows = []
    for i, c in enumerate(conclusions, 1):
        cited = [by_id[x] for x in c["fact_ids"]]
        evidence = list(dict.fromkeys(d for f in cited for d in f.evidence_doc_ids))
        odd = unmatched_numbers(c["text"], cited)
        rows.append({
            "draft_id": f"D{i:02d}", "source": "llm", "period": c["period"], "kind": c["kind"],
            "conclusion_text": c["text"], "fact_ids": " ".join(c["fact_ids"]),
            "evidence_doc_ids": " ".join(evidence),
            "evidence_preview": "\n".join(preview.get(d, d) for d in evidence),
            "number_check": "OK" if not odd else "확인 필요: " + ", ".join(odd),
            "reviewer_A": "", "reviewer_B": "", "final_text": "", "final_evidence_doc_ids": "", "note": "",
            "model_version": model, "prompt_version": PROMPT_VERSION, "created_at": created_at,
        })
    return rows


# ---------------------------------------------------------------- review -> insights

def parse_ids(value: Any) -> list[str]:
    """doc_ids typed in a cell: separated by spaces, commas, semicolons or new lines (a JSON list also works)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if text.startswith("["):
        try:
            return [str(x).strip() for x in json.loads(text) if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [x for x in re.split(r"[\s,;]+", text) if x]


def _cell(row: dict, key: str) -> str:
    v = row.get(key)
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def reviewed_insights(rows: list[dict], known_doc_ids: set[str], reviewed_by: str) -> tuple[list[dict], list[str]]:
    """Rows both reviewers approved -> insights rows; everything else -> a reason per row.

    final_text / final_evidence_doc_ids override the draft when filled. Rows people add by hand
    (source = manual) go through the same checks. Interpretations are stored with a "(해석)" prefix
    so the dashboard shows which sentences are inference (the table has no kind column).
    """
    accepted, rejected = [], []
    for n, row in enumerate(rows, 1):
        label = _cell(row, "draft_id") or f"row {n}"
        a, b = _cell(row, "reviewer_A").lower(), _cell(row, "reviewer_B").lower()
        if a not in APPROVE or b not in APPROVE:
            rejected.append(f"{label}: 두 검수자가 모두 O가 아님 (A={a or '빈칸'}, B={b or '빈칸'})")
            continue
        text = _cell(row, "final_text") or _cell(row, "conclusion_text")
        period = _cell(row, "period")
        evidence = parse_ids(row.get("final_evidence_doc_ids")) or parse_ids(row.get("evidence_doc_ids"))
        unknown = [d for d in evidence if d not in known_doc_ids]
        if not text or not period:
            rejected.append(f"{label}: 문장 또는 기간이 비어 있음")
        elif not evidence:
            rejected.append(f"{label}: 근거 문서가 없음 (근거 없는 문장은 넣지 않는다)")
        elif unknown:
            rejected.append(f"{label}: DB에 없는 doc_id {unknown[:3]}")
        else:
            if _cell(row, "kind") == "해석" and not text.startswith("(해석)"):
                text = f"(해석) {text}"
            accepted.append({"period": period, "conclusion_text": text,
                             "evidence_doc_ids": list(dict.fromkeys(evidence)), "reviewed_by": reviewed_by})
    return accepted, rejected
