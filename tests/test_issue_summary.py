"""Per-quarter issue summaries: quarters, LLM input without titles, answer validation, review -> table rows."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from src.analysis import issue_summary as isum
from src.analysis.llm_classifier import LLMReply

CATS = {"regulation_policy": "규제 및 정책", "technology_trend": "기술 및 트렌드"}
FIRMS = {"pwc": "삼일PwC"}


def _news() -> pd.DataFrame:
    rows = [{"doc_id": f"n{i}", "title": f"제목{i}", "url": f"https://x/{i}", "published_at": f"2025-02-{1 + i:02d}",
             "dup_group_id": "g" if i < 3 else None, "sentiment": "positive", "category": "technology_trend",
             "keywords": ["AI"], "issue_summary": f"요약{i}", "kr_company_ids": ["pwc"] if i < 3 else []}
            for i in range(5)]
    rows.append({**rows[0], "doc_id": "late", "published_at": "2025-04-02", "dup_group_id": None})
    df = pd.DataFrame(rows)
    df["published_at"] = pd.to_datetime(df["published_at"])
    return df


def test_quarters_are_clipped_to_the_period():
    q = isum.quarters(date(2023, 11, 1), date(2024, 5, 15))
    assert [x[0] for x in q] == ["2023-Q4", "2024-Q1", "2024-Q2"]
    assert q[0][1] == date(2023, 11, 1) and q[-1][2] == date(2024, 5, 15) and q[1][2] == date(2024, 3, 31)


def test_payload_has_events_without_titles_and_maps_evidence_locally():
    spikes = [{"scope": "all", "week": "2025-02-03", "count": 9, "threshold": 4.0, "spike_type": "인사",
               "background": "사람 기록"},
              {"scope": "pwc", "week": "2025-02-03", "count": 5, "threshold": 2.0, "background": "법인 범위"},
              {"scope": "all", "week": "2025-05-05", "count": 9, "threshold": 4.0, "background": "다른 분기"}]
    payload, evidence = isum.period_payload(_news(), spikes, "2025-Q1", date(2025, 1, 1), date(2025, 3, 31), FIRMS, CATS)
    assert payload["관련 기사 수"] == 5                         # the April article is outside Q1
    first = payload["주요 사건(기사 수 순)"][0]
    assert first["event_id"] == "E1" and first["기사 수"] == 3 and first["법인"] == ["삼일PwC"]
    assert evidence["E1"] == "n0"                                   # earliest article of the largest group
    assert payload["주요 사건(기사 수 순)"][1]["법인"] == ["업계·글로벌"]
    assert [b["배경(사람 기록)"] for b in payload["급증 주"]] == ["사람 기록"]
    sent = json.dumps(isum.build_messages(payload), ensure_ascii=False)
    assert "제목0" not in sent and "https://x" not in sent


def test_parse_summary_validates_shape_and_event_ids():
    ok = json.dumps({"headline": "요약", "issues": [{"text": "이슈", "event_ids": ["E1", "E1"]}]}, ensure_ascii=False)
    assert isum.parse_summary(ok, {"E1"})["issues"][0]["event_ids"] == ["E1"]
    for bad in ('{"headline": "", "issues": []}', ok.replace("E1", "E9"), "nope",
                json.dumps({"headline": "h", "issues": [{"text": "t", "event_ids": ["E1"]}] * 4})):
        with pytest.raises(isum.SummaryError):
            isum.parse_summary(bad, {"E1"})


def test_draft_period_retries_then_builds_sheet_row_with_number_check():
    payload, evidence = isum.period_payload(_news(), [], "2025-Q1", date(2025, 1, 1), date(2025, 3, 31), FIRMS, CATS)
    answers = iter(["bad", json.dumps({"headline": "AI 보도가 5건", "issues": [
        {"text": "삼일PwC의 AI 관련 보도가 3건, 7건 증가", "event_ids": ["E1"]}]}, ensure_ascii=False)])
    summary, model = isum.draft_period(lambda m: LLMReply(next(answers), "m1"), payload, set(evidence))
    row = isum.sheet_row("2025-Q1", date(2025, 1, 1), date(2025, 3, 31), payload, evidence, summary, model, "t",
                         {"n0": "n0 | [뉴스] 제목0 | u"})
    assert row["evidence_1"] == "n0" and row["draft_issue_1"] == row["issue_1"]
    assert row["number_check"] == "확인 필요: 7"                    # 3 and 5 are in the data, 7 is not
    assert "제목0" in row["evidence_preview"] and row["prompt_version"] == isum.PROMPT_VERSION


def test_reviewed_summaries_requires_both_approvals_and_known_evidence():
    base = {"period": "2025-Q1", "period_start": "2025-01-01", "period_end": "2025-03-31", "article_count": "5",
            "headline": "h", "issue_1": "i1", "evidence_1": "n0 n1", "issue_2": "", "evidence_2": "",
            "model_version": "m", "prompt_version": "p", "created_at": "t"}
    rows = [{**base, "reviewer_A": "O", "reviewer_B": "예"},
            {**base, "period": "2025-Q2", "reviewer_A": "O", "reviewer_B": "X"},
            {**base, "period": "2025-Q3", "reviewer_A": "O", "reviewer_B": "O", "evidence_1": "zz"},
            {**base, "period": "2025-Q4", "reviewer_A": "O", "reviewer_B": "O", "evidence_1": ""}]
    accepted, rejected = isum.reviewed_summaries(rows, {"n0", "n1"}, "가, 나")
    assert [a["period"] for a in accepted] == ["2025-Q1"]
    assert accepted[0]["issues"] == [{"text": "i1", "evidence_doc_ids": ["n0", "n1"]}] and accepted[0]["article_count"] == 5
    assert [r.split(":")[0] for r in rejected] == ["2025-Q2", "2025-Q3", "2025-Q4"]
