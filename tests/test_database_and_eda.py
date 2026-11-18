from __future__ import annotations

from collections import Counter

import pandas as pd

from src.analysis.eda import detect_spikes, weekly_counts
from src.database import db_manager
from verify_dart_gate import EntityResult, evaluate


def _news(doc_id, url, h):
    return {"doc_id": doc_id, "source_type": "news", "title": "t", "content": "c", "published_at": "2024-01-01",
            "url": url, "company_ids": ["pwc"], "content_hash": h, "collected_at": "now"}


def _dart(content):
    return {"doc_id": "DART_1", "source_type": "dart", "title": "감사보고서", "content": content,
            "published_at": "2024-01-01", "url": "https://dart/1", "rcept_no": "1", "content_hash": content or "x",
            "collected_at": "now"}


def test_news_insert_is_idempotent_and_skips_exact_duplicates(tmp_path):
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        assert db_manager.insert_news(conn, [_news("N1", "u1", "h1"), _news("N2", "u2", "h1")]) == 1
        assert db_manager.insert_news(conn, [_news("N1", "u1", "h1")]) == 0
        docs = db_manager.fetch_documents(conn, "news")
    assert [d["doc_id"] for d in docs] == ["N1"] and docs[0]["company_ids"] == ["pwc"]


def test_dart_upsert_refreshes_content(tmp_path):
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        db_manager.upsert_dart(conn, [_dart("")])
        db_manager.upsert_dart(conn, [_dart("본문")])
        docs = db_manager.fetch_documents(conn, "dart")
    assert len(docs) == 1 and docs[0]["content"] == "본문"


def test_detect_spikes_uses_preceding_baseline_only():
    dates = []
    start = pd.Timestamp("2024-01-01")  # Monday
    for week in range(10):
        n = 30 if week == 9 else 5 + (week % 2)
        dates += [start + pd.Timedelta(weeks=week)] * n
    counts = weekly_counts(pd.Series(dates))
    result = detect_spikes(counts, baseline_weeks=8, sigma=2.0)
    assert len(counts) == 10
    assert result["is_spike"].tolist() == [False] * 9 + [True]


def test_gate2_evaluate():
    r1 = EntityResult("pwc", "삼일", "1", extract_ok=20, extractable_months={"202401", "202402", "202503"},
                      extractable_report_types={"감사보고서", "사업보고서"})
    r2 = EntityResult("kpmg", "삼정", "2", extract_ok=15, extractable_months={"202403"}, failures=Counter(),
                      extractable_report_types={"감사보고서"})
    criteria = {"min_extractable_docs": 30, "min_distinct_months": 3, "min_distinct_years": 3,
                "min_companies_with_docs": 3, "min_distinct_report_types": 3}
    verdict = evaluate([r1, r2], criteria)
    assert [(a, p) for _, a, _, p in verdict] == [(35, True), (4, True), (2, False), (2, False), (2, False)]


def test_recheck_gate2_judge():
    from recheck_gate2 import judge

    docs = [
        {"content": "x", "published_at": "2024-03-10", "company_ids": ["pwc"], "report_nm": "감사보고서 (2023.12)", "title": ""},
        {"content": "x", "published_at": "2025-07-01", "company_ids": ["kpmg"], "report_nm": "회계법인사업보고서", "title": ""},
        {"content": "", "published_at": "2026-01-01", "company_ids": ["ey"], "report_nm": "감사보고서", "title": ""},
    ]
    criteria = {"min_extractable_docs": 2, "min_distinct_months": 2, "min_distinct_years": 2,
                "min_companies_with_docs": 3, "min_distinct_report_types": 2}
    assert [(a, p) for _, a, _, p in judge(docs, criteria)] == [(2, True), (2, True), (2, True), (2, False), (2, True)]
