"""Dashboard read-model tables: idempotent replace loaders and the spike/annotation join."""

from __future__ import annotations

import pandas as pd

from src.database import db_manager


def _news(doc_id: str) -> dict:
    return {"doc_id": doc_id, "source_type": "news", "title": doc_id, "content": "", "published_at": "2024-01-01",
            "url": f"https://x/{doc_id}", "company_ids": [], "content_hash": doc_id, "collected_at": "x"}


def test_replace_loaders_are_idempotent(tmp_path):
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, [_news("N1"), _news("N2")])
        scope = [{"doc_id": "N1", "company_scope": "member_firm", "kr_company_ids": ["pwc", "ey"]},
                 {"doc_id": "N2", "company_scope": "industry", "kr_company_ids": []}]
        assert db_manager.replace_news_firm_scope(conn, scope) == 2
        assert db_manager.replace_news_firm_scope(conn, scope[:1]) == 1  # rows gone from the source are dropped
        assert db_manager.fetch_news_firm_scope(conn) == {
            "N1": {"company_scope": "member_firm", "kr_company_ids": ["ey", "pwc"]}}

        spikes = [{"scope": "all", "week": "2024-01-01", "count": 30, "threshold": 20.5, "spike_type": "인사"}]
        db_manager.replace_news_spikes(conn, spikes)
        assert db_manager.replace_news_spikes(conn, spikes) == 1
        got = db_manager.fetch_news_spikes(conn)[0]
        assert got["spike_type"] == "인사" and got["background"] is None

        monthly = [{"month": "2026-03", "pblntf_ty": "F", "filer": "삼일회계법인", "count": "12"}]
        db_manager.replace_audit_filings_monthly(conn, monthly)
        assert db_manager.replace_audit_filings_monthly(conn, monthly) == 1
        assert db_manager.fetch_audit_filings_monthly(conn) == [
            {"month": "2026-03", "pblntf_ty": "F", "filer": "삼일회계법인", "count": 12}]


def test_spike_rows_join_backgrounds_and_map_overall_scope():
    import load_dashboard_tables as ld

    by_scope = {"all": pd.DataFrame([{"week": "2024-01-01", "count": 30, "threshold": 20.0, "titles": ["t"]},
                                     {"week": "2024-02-05", "count": 25, "threshold": 19.0, "titles": []}]),
                "pwc": pd.DataFrame([{"week": "2024-01-01", "count": 9, "threshold": 5.0, "titles": []}])}
    notes = pd.DataFrame([{"scope": "전체", "week": "2024-01-01", "type": "인사", "leading_firm": "삼일PwC",
                           "category": "시장", "background": "정기 인사", "collection_impact": "없음"}])
    rows = {(r["scope"], r["week"]): r for r in ld.spike_rows(by_scope, notes)}
    assert rows[("all", "2024-01-01")]["spike_type"] == "인사"
    assert rows[("all", "2024-01-01")]["background"] == "정기 인사"
    assert rows[("all", "2024-02-05")]["spike_type"] is None
    assert rows[("pwc", "2024-01-01")]["spike_type"] is None  # firm scope has its own notes
    assert "titles" not in rows[("all", "2024-01-01")]  # titles are not copied into the table


def test_corp_names_and_insights(tmp_path):
    import json as _json

    import load_dashboard_tables as ld

    market = tmp_path / "market"
    market.mkdir()
    for i, name in enumerate(["가나 주식회사", "가나전자 주식회사"]):  # renamed later: last file wins
        (market / f"q{i}.json").write_text(_json.dumps({"request": {}, "response": {"list": [
            {"corp_code": "00111", "corp_name": name}, {"corp_code": "", "corp_name": "x"}]}}), encoding="utf-8")
    names = ld.corp_names(tmp_path)
    assert names == {"00111": "가나전자 주식회사"}
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        assert db_manager.replace_dart_corp_names(conn, names) == 1
        assert db_manager.fetch_dart_corp_names(conn) == names
        conn.execute("INSERT INTO insights (period, conclusion_text, evidence_doc_ids, created_at) VALUES (?,?,?,?)",
                     ("2026", "결론", _json.dumps(["DART_1"]), "x"))
        assert db_manager.fetch_insights(conn)[0]["evidence_doc_ids"] == ["DART_1"]


def test_issue_summaries_replace_and_fetch(tmp_path):
    row = {"period": "2025-Q1", "period_start": "2025-01-01", "period_end": "2025-03-31", "headline": "요약",
           "issues": [{"text": "이슈", "evidence_doc_ids": ["N1"]}], "article_count": 3, "model_version": "m",
           "prompt_version": "p", "created_at": "x", "reviewed_by": "A, B"}
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        assert db_manager.replace_issue_summaries(conn, [row]) == 1
        assert db_manager.replace_issue_summaries(conn, [row, {**row, "period": "2024-Q4", "period_start": "2024-10-01"}]) == 2
        got = db_manager.fetch_issue_summaries(conn)
        assert [r["period"] for r in got] == ["2024-Q4", "2025-Q1"]
        assert got[1]["issues"][0]["evidence_doc_ids"] == ["N1"]
