"""Conclusion drafts (guide 8): facts, answer validation, number check, review -> insights."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from src.analysis import conclusions as cc
from src.analysis.llm_classifier import LLMReply

CATS = {"regulation_policy": "규제 및 정책", "technology_trend": "기술 및 트렌드"}
FIRMS = {"pwc": "삼일PwC", "kpmg": "삼정KPMG"}


def _news() -> pd.DataFrame:
    rows = []
    for i in range(30):   # 2025: 30 articles about pwc only, mostly technology, one big group
        rows.append({"doc_id": f"n{i}", "title": f"t{i}", "url": "u", "published_at": f"2025-03-{1 + i % 20:02d}",
                     "dup_group_id": "g-big" if i < 5 else None,
                     "sentiment": "negative" if i < 5 else "positive",
                     "category": "regulation_policy" if i < 5 else "technology_trend", "kr_company_ids": ["pwc"]})
    rows.append({"doc_id": "x1", "title": "t", "url": "u", "published_at": "2025-11-03", "dup_group_id": None,
                 "sentiment": "neutral", "category": "technology_trend", "kr_company_ids": ["pwc", "kpmg"]})
    df = pd.DataFrame(rows)
    df["published_at"] = pd.to_datetime(df["published_at"])
    return df


def test_top_events_orders_by_group_size_and_returns_earliest_member():
    assert cc.top_events(_news(), 1) == ["n0"]


def test_category_and_sentiment_facts_use_same_months_and_single_firm_articles():
    news = _news()
    cat = cc.category_facts(news, [2025], range(1, 11), CATS)
    assert len(cat) == 1 and cat[0].period == "2025-01~2025-10"
    assert cat[0].data["분류된 기사 수"] == 30          # the November article is outside 1-10월
    assert cat[0].data["카테고리별 비중"]["규제 및 정책"] == round(5 / 30, 3)
    sent = cc.sentiment_facts(news, [2025], range(1, 11), FIRMS, min_articles=20)
    assert [f.data["법인"] for f in sent] == ["삼일PwC"]  # kpmg has no single-firm articles
    assert sent[0].data["부정 비중"] == round(5 / 30, 3)
    assert cc.sentiment_facts(news, [2025], range(1, 11), FIRMS, min_articles=31) == []


def test_facts_sent_to_the_llm_hold_no_titles_or_urls():
    news = _news()
    spikes = [{"scope": "all", "week": "2025-03-03", "count": 9, "threshold": 4.2, "spike_type": "인사",
               "leading_firm": "삼일PwC", "background": "사람이 적은 배경"},
              {"scope": "pwc", "week": "2025-03-10", "count": 5, "threshold": 2.0, "background": None}]
    facts = cc.number_facts(cc.category_facts(news, [2025], range(1, 11), CATS) + cc.spike_facts(spikes, news, FIRMS))
    assert [f.fact_id for f in facts] == ["F01", "F02"]   # the spike without a background is skipped
    sent = json.dumps([m["content"] for m in cc.build_messages(facts)], ensure_ascii=False)
    assert "t0" not in sent and "\"u\"" not in sent
    assert facts[1].evidence_doc_ids                       # evidence is attached locally


def test_audit_facts_share_of_all_filers():
    monthly = pd.DataFrame({"month": ["2025-03", "2025-03", "2025-11"], "pblntf_ty": "F",
                            "filer": ["pwc", "other", "pwc"], "count": [30, 70, 999]})
    dart = pd.DataFrame({"doc_id": ["d1"], "rcept_dt": pd.to_datetime(["2025-03-30"]), "company_id": ["pwc"],
                         "filing_role": ["audit_client"]})
    (f,) = cc.audit_facts(monthly, dart, [2025], range(1, 11), FIRMS)
    assert f.data["전체 제출 건수(비Big4 포함)"] == 100
    assert f.data["법인별 비중(전체 대비)"]["삼일PwC"] == 0.3
    assert f.evidence_doc_ids == ["d1"]


def test_parse_conclusions_rejects_unknown_fact_and_bad_kind():
    ok = json.dumps({"conclusions": [{"period": "2025", "kind": "사실", "text": "문장", "fact_ids": ["F01"]}]}, ensure_ascii=False)
    assert cc.parse_conclusions(ok, {"F01"})[0]["fact_ids"] == ["F01"]
    with pytest.raises(cc.DraftError):
        cc.parse_conclusions(ok.replace("F01", "F99"), {"F01"})
    with pytest.raises(cc.DraftError):
        cc.parse_conclusions(ok.replace("사실", "추측"), {"F01"})


def test_draft_retries_once_then_succeeds():
    answers = iter(["not json", json.dumps({"conclusions": [
        {"period": "2025", "kind": "해석", "text": "문장", "fact_ids": ["F01"]}]})])
    chat = lambda messages: LLMReply(text=next(answers), model="m1")  # noqa: E731
    fact = cc.number_facts([cc.Fact("", "x", "2025", {"n": 1})])
    out, model = cc.draft(chat, fact)
    assert model == "m1" and out[0]["kind"] == "해석"


def test_unmatched_numbers_accepts_percent_of_shares_and_flags_new_numbers():
    fact = cc.Fact("F01", "x", "2025-01~2025-10", {"비중": 0.123, "기사 수": 1205, "주 시작일": "2025-03-10"})
    assert cc.unmatched_numbers("2025년 1~10월 1,205건 중 12.3%, 3월 10일 주", [fact]) == []
    assert cc.unmatched_numbers("15.0% 증가", [fact]) == ["15"]


def test_reviewed_insights_needs_both_approvals_and_known_evidence():
    rows = [
        {"draft_id": "D01", "period": "2025", "kind": "사실", "conclusion_text": "A", "evidence_doc_ids": "n1 n2",
         "reviewer_A": "O", "reviewer_B": "o"},
        {"draft_id": "D02", "period": "2025", "kind": "해석", "conclusion_text": "B", "evidence_doc_ids": "n1",
         "reviewer_A": "O", "reviewer_B": "", "final_text": ""},
        {"draft_id": "D03", "period": "2025", "kind": "해석", "conclusion_text": "C", "evidence_doc_ids": "n1",
         "reviewer_A": "1", "reviewer_B": "예", "final_text": "고친 문장", "final_evidence_doc_ids": "n2; n2"},
        {"draft_id": "D04", "period": "2025", "kind": "사실", "conclusion_text": "D", "evidence_doc_ids": "zz",
         "reviewer_A": "O", "reviewer_B": "O"},
        {"draft_id": "", "source": "manual", "period": "2026", "kind": "사실", "conclusion_text": "E",
         "evidence_doc_ids": "", "reviewer_A": "O", "reviewer_B": "O"},
    ]
    accepted, rejected = cc.reviewed_insights(rows, {"n1", "n2"}, "가, 나")
    assert [a["conclusion_text"] for a in accepted] == ["A", "(해석) 고친 문장"]
    assert accepted[1]["evidence_doc_ids"] == ["n2"] and accepted[0]["reviewed_by"] == "가, 나"
    assert [r.split(":")[0] for r in rejected] == ["D02", "D04", "row 5"]
