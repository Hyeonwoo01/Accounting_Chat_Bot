"""Per-period issue summaries for the dashboard (advanced extension). Batch only — never called by the dashboard.

Flow per quarter: the largest events (near-duplicate groups) of relevant news, described by their stored
LLM labels (category, sentiment, keywords, one-line summary) + the human-written spike backgrounds of
that quarter -> LLM writes a headline and 1-3 issues citing event ids -> two reviewers -> only approved
rows go to `issue_summaries`.

The LLM does not see article titles or URLs. Evidence links are attached locally from the cited event
ids (each event's earliest article), so a summary cannot cite a source the data does not hold.
The inputs are still derived from Naver news, which the team uses only during the project
(evaluation/gate_decision.md); drafts and stored summaries are on the end-of-project deletion list.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import pandas as pd

from src.analysis.conclusions import Fact, unmatched_numbers
from src.analysis.llm_classifier import ChatFn

PROMPT_VERSION = "issue-v1"
SENTIMENT_KO = {"positive": "긍정", "neutral": "중립", "negative": "부정"}
MAX_ISSUES = 3
APPROVE = {"o", "○", "y", "yes", "1", "예", "승인"}

SYSTEM_PROMPT = """당신은 회계·컨설팅(전문서비스) 산업 뉴스를 기간별로 정리하는 분석가다.
아래 [기간 자료]만 근거로 그 기간의 핵심 이슈를 요약한다. 자료에 없는 사건·수치·원인을 추가하지 않는다.

[규칙]
1. headline: 그 기간을 한 문장으로 요약한다(40자 안팎).
2. issues: 핵심 이슈 1~3개. 각 이슈는 한국어 1문장이고, 근거로 쓴 사건의 event_id를 1개 이상 적는다.
   기사 수가 많은 사건을 우선하되, 같은 성격의 사건은 하나로 묶는다.
3. 법인명은 자료에 적힌 그대로 쓴다. 법인이 '업계·글로벌'인 사건을 특정 한국 법인의 일로 쓰지 않는다.
4. 기사 수는 보도 노출량이다. 경쟁력·순위로 쓰지 않는다. 숫자는 자료에 있는 값만 쓴다.
5. 급증 주 배경은 사람이 기사를 읽고 적은 기록이다. 쓸 때 사건의 내용으로만 반영한다.
6. 원인을 단정하지 않는다("~ 때문이다" 대신 사실을 전한다).

[출력] 아래 JSON 객체 하나만 출력한다.
{"headline": "한 문장", "issues": [{"text": "이슈 문장", "event_ids": ["E1"]}]}
"""


class SummaryError(ValueError):
    """The model's answer is not a valid summary object."""


def quarters(start: date, end: date) -> list[tuple[str, date, date]]:
    """Calendar quarters overlapping [start, end], clipped to it: [("2025-Q1", 2025-01-01, 2025-03-31), ...]."""
    out = []
    y, q = start.year, (start.month - 1) // 3 + 1
    while True:
        q_start = date(y, 3 * q - 2, 1)
        if q_start > end:
            break
        q_end = (pd.Timestamp(q_start) + pd.offsets.QuarterEnd(0)).date()
        out.append((f"{y}-Q{q}", max(q_start, start), min(q_end, end)))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def period_payload(news: pd.DataFrame, spikes: list[dict], label: str, start: date, end: date,
                   firm_labels: dict[str, str], cat_labels: dict[str, str], top_events: int = 8
                   ) -> tuple[dict[str, Any], dict[str, str]]:
    """(data sent to the LLM, {event_id: evidence doc_id}). `news` = relevant articles with LLM labels."""
    lo, hi = pd.Timestamp(start), pd.Timestamp(end) + pd.Timedelta(days=1)
    sub = news[(news["published_at"] >= lo) & (news["published_at"] < hi) & news["category"].notna()]
    if sub.empty:
        return {}, {}
    df = sub.assign(group=sub["dup_group_id"].fillna(sub["doc_id"]))
    sizes = df.groupby("group")["doc_id"].size().sort_values(ascending=False, kind="stable")
    reps = df.sort_values(["published_at", "doc_id"]).groupby("group").first()
    events, evidence = [], {}
    for n, group in enumerate(sizes.index[:top_events], 1):
        r = reps.loc[group]
        firms = [firm_labels.get(f, f) for f in (r["kr_company_ids"] or [])]
        eid = f"E{n}"
        evidence[eid] = r["doc_id"]
        events.append({
            "event_id": eid, "기사 수": int(sizes[group]), "날짜": f"{r['published_at']:%Y-%m-%d}",
            "카테고리": cat_labels.get(r["category"], r["category"]),
            "감성": SENTIMENT_KO.get(r["sentiment"], r["sentiment"]),
            "키워드": list(r["keywords"] or []), "요약": r["issue_summary"] or "",
            "법인": firms or ["업계·글로벌"],
        })
    counts = sub["category"].value_counts()
    backgrounds = [{"주 시작일": s["week"], "기사 수": int(s["count"]), "기준": round(float(s["threshold"]), 1),
                    "유형": s.get("spike_type"), "배경(사람 기록)": s["background"]}
                   for s in spikes
                   if s.get("background") and s["scope"] == "all" and lo <= pd.Timestamp(s["week"]) < hi]
    payload = {"기간": f"{label} ({start:%Y-%m-%d} ~ {end:%Y-%m-%d})", "관련 기사 수": int(len(sub)),
               "카테고리별 기사 수": {cat_labels.get(c, c): int(counts.get(c, 0)) for c in cat_labels},
               "주요 사건(기사 수 순)": events, "급증 주": backgrounds}
    return payload, evidence


def build_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "[기간 자료]\n" + json.dumps(payload, ensure_ascii=False, indent=1)}]


def parse_summary(text: str, event_ids: set[str]) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise SummaryError("no JSON object in answer")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise SummaryError(f"invalid JSON: {e.msg}") from e
    headline, issues = data.get("headline"), data.get("issues")
    if not isinstance(headline, str) or not headline.strip():
        raise SummaryError("empty headline")
    if not isinstance(issues, list) or not 1 <= len(issues) <= MAX_ISSUES:
        raise SummaryError(f"issues must be 1-{MAX_ISSUES} items")
    out = []
    for i, it in enumerate(issues, 1):
        t, ids = (it or {}).get("text"), (it or {}).get("event_ids")
        if not isinstance(t, str) or not t.strip():
            raise SummaryError(f"issue {i}: empty text")
        if not isinstance(ids, list) or not ids or any(x not in event_ids for x in ids):
            raise SummaryError(f"issue {i}: event_ids must cite known events, got {ids!r}")
        out.append({"text": t.strip(), "event_ids": list(dict.fromkeys(ids))})
    return {"headline": headline.strip(), "issues": out}


def draft_period(chat: ChatFn, payload: dict[str, Any], event_ids: set[str], retries: int = 1) -> tuple[dict, str]:
    messages = build_messages(payload)
    last = ""
    for _ in range(retries + 1):
        reply = chat(messages)
        try:
            return parse_summary(reply.text, event_ids), reply.model
        except SummaryError as e:
            last = str(e)
    raise SummaryError(last)


SHEET_COLUMNS = ("period", "period_start", "period_end", "article_count", "headline",
                 "issue_1", "evidence_1", "issue_2", "evidence_2", "issue_3", "evidence_3",
                 "evidence_preview", "number_check", "reviewer_A", "reviewer_B", "note",
                 "draft_headline", "draft_issue_1", "draft_issue_2", "draft_issue_3",
                 "model_version", "prompt_version", "created_at")


def sheet_row(label: str, start: date, end: date, payload: dict, evidence: dict[str, str], summary: dict,
              model: str, created_at: str, preview: dict[str, str]) -> dict[str, Any]:
    """Review-sheet row. Reviewers edit headline / issue_n / evidence_n in place; draft_* keep the original."""
    row: dict[str, Any] = {c: "" for c in SHEET_COLUMNS}
    row.update(period=label, period_start=f"{start:%Y-%m-%d}", period_end=f"{end:%Y-%m-%d}",
               article_count=payload["관련 기사 수"], headline=summary["headline"], draft_headline=summary["headline"],
               model_version=model, prompt_version=PROMPT_VERSION, created_at=created_at)
    fact = Fact("", "period", payload["기간"], payload)
    odd, docs = [], []
    for n, issue in enumerate(summary["issues"], 1):
        ids = [evidence[e] for e in issue["event_ids"]]
        row[f"issue_{n}"] = row[f"draft_issue_{n}"] = issue["text"]
        row[f"evidence_{n}"] = " ".join(ids)
        docs += ids
        odd += unmatched_numbers(issue["text"], [fact])
    odd += unmatched_numbers(summary["headline"], [fact])
    row["number_check"] = "OK" if not odd else "확인 필요: " + ", ".join(dict.fromkeys(odd))
    row["evidence_preview"] = "\n".join(preview.get(d, d) for d in dict.fromkeys(docs))
    return row


def _cell(row: dict, key: str) -> str:
    v = row.get(key)
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip()


def reviewed_summaries(rows: list[dict], known_doc_ids: set[str], reviewed_by: str) -> tuple[list[dict], list[str]]:
    """Rows both reviewers approved -> issue_summaries rows; everything else -> a reason per row."""
    accepted, rejected = [], []
    for row in rows:
        label = _cell(row, "period") or "?"
        a, b = _cell(row, "reviewer_A").lower(), _cell(row, "reviewer_B").lower()
        if a not in APPROVE or b not in APPROVE:
            rejected.append(f"{label}: 두 검수자가 모두 O가 아님 (A={a or '빈칸'}, B={b or '빈칸'})")
            continue
        issues, problem = [], ""
        for n in range(1, MAX_ISSUES + 1):
            text = _cell(row, f"issue_{n}")
            ids = [x for x in re.split(r"[\s,;]+", _cell(row, f"evidence_{n}")) if x]
            if not text:
                continue
            unknown = [d for d in ids if d not in known_doc_ids]
            if not ids:
                problem = f"이슈 {n}에 근거 문서가 없음"
            elif unknown:
                problem = f"이슈 {n}: DB에 없는 doc_id {unknown[:3]}"
            issues.append({"text": text, "evidence_doc_ids": list(dict.fromkeys(ids))})
        if not _cell(row, "headline") or not issues:
            problem = problem or "headline 또는 이슈가 비어 있음"
        if problem:
            rejected.append(f"{label}: {problem}")
            continue
        count = _cell(row, "article_count")
        accepted.append({"period": label, "period_start": _cell(row, "period_start"), "period_end": _cell(row, "period_end"),
                         "headline": _cell(row, "headline"), "issues": issues,
                         "article_count": int(float(count)) if count else None,
                         "model_version": _cell(row, "model_version"), "prompt_version": _cell(row, "prompt_version"),
                         "created_at": _cell(row, "created_at"), "reviewed_by": reviewed_by})
    return accepted, rejected
