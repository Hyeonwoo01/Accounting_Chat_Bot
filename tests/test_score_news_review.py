"""Accuracy math for the news review sheet: '=' shorthand, set comparison, skips, population weighting."""

from __future__ import annotations

import pytest

from score_news_review import matches, score


def test_matches_equal_shorthand_and_sets():
    assert matches("company_scope", "member_firm", "=")
    assert not matches("company_scope", "member_firm", "global_network")
    assert matches("member_firm_ids", "pwc_korea,kpmg_korea", " kpmg_korea, pwc_korea ")
    assert matches("member_firm_ids", "", "")            # 둘 다 해당 법인 없음
    assert not matches("member_firm_ids", "pwc_korea", "")


def _row(kw, rel, scope="=", firms="=", role="="):
    return {"search_keyword": kw, "human_is_relevant": rel, "company_scope": "member_firm",
            "human_company_scope": scope, "member_firm_ids": "pwc_korea", "human_member_firm_ids": firms,
            "mention_role": "subject", "human_mention_role": role}


def test_score_skips_unreviewed_and_irrelevant_and_weights_by_population():
    rows = [
        _row("A", "1"), _row("A", "1", scope="global_network"), _row("A", "0"), _row("A", ""),
        _row("B", "1"), _row("B", "1"),
    ]
    r = score(rows, {"A": 300, "B": 100})
    a = r["categories"]["A"]
    assert (a["reviewed"], a["unreviewed"]) == (3, 1)
    assert a["relevance"] == pytest.approx(2 / 3)
    assert a["company_scope"] == pytest.approx(0.5)      # 관련 없음·미검수 행은 분모에서 빠진다
    assert r["categories"]["B"]["company_scope"] == 1.0
    assert r["overall"]["company_scope"] == pytest.approx((300 * 0.5 + 100 * 1.0) / 400)


def test_blank_answer_is_not_counted_as_wrong():
    r = score([_row("A", "1", role="")], {"A": 10})
    assert r["categories"]["A"]["mention_role"] is None
    assert r["categories"]["A"]["n"]["mention_role"] == 0
