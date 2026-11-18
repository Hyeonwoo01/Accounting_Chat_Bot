"""Clean raw files and load them into SQLite (guide 12-2, step 2).

Usage: python scripts/process_data.py

Idempotent: news is inserted once per URL/content hash, DART rows are upserted by rcept_no.
"""

from __future__ import annotations

import csv
import json
import logging
import sys

import _bootstrap  # noqa: F401

from src.common.config import (
    NAVER_NEWS_DISABLED_REASON,
    PROCESSED_DIR,
    RAW_DART_DIR,
    RAW_NEWS_DIR,
    get_period,
    load_company_mapping,
    naver_news_enabled,
)
from src.database import db_manager
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.data_processor import (
    assign_dup_groups,
    build_audit_report_docs,
    build_dart_docs,
    build_news_docs,
)

logger = logging.getLogger("process_data")


FIRM_SCOPE_FILE = PROCESSED_DIR / "news_firm_scope.jsonl"


def resolve_duplicate(rows: dict[str, dict], doc_id: str) -> dict:
    """The tagging row that represents doc_id: itself, or the kept article it duplicates."""
    row = rows.get(doc_id, {})
    if row.get("exclude_reason") == "exact_duplicate" and row.get("duplicate_of") in rows:
        return rows[row["duplicate_of"]]
    return row


def refined_relevance(stored_news: list[dict]) -> dict[str, bool] | None:
    """is_relevant from scripts/tag_news_firms.py: kept in the refined set (exclude_reason is None).

    For exact duplicates the DB keeps the first copy in file order while tag_news_firms.py keeps the
    earliest published one, so the DB copy can be the one marked exact_duplicate; it then follows
    duplicate_of to the kept article.
    Returns None (DB left unchanged) if the tagging output is missing. Articles missing from the
    file are marked irrelevant and reported, which means tag_news_firms.py should be re-run.
    """
    if not FIRM_SCOPE_FILE.exists():
        logger.warning("%s not found: run scripts/tag_news_firms.py; is_relevant left unchanged", FIRM_SCOPE_FILE)
        return None
    rows: dict[str, dict] = {}
    with FIRM_SCOPE_FILE.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            rows[row["doc_id"]] = row
    kept = {doc_id: resolve_duplicate(rows, doc_id).get("exclude_reason") is None for doc_id in rows}
    missing = [d["doc_id"] for d in stored_news if d["doc_id"] not in kept]
    if missing:
        logger.warning("%d news rows are not in %s - re-run scripts/tag_news_firms.py (marked irrelevant)",
                       len(missing), FIRM_SCOPE_FILE.name)
    return {d["doc_id"]: kept.get(d["doc_id"], False) for d in stored_news}


def main() -> int:
    mapping = load_company_mapping()
    if naver_news_enabled():
        news_docs = build_news_docs(RAW_NEWS_DIR, get_period(), CompanyMatcher(mapping))
    else:
        news_docs = []
        logger.warning("skipping news: %s", NAVER_NEWS_DISABLED_REASON)
        leftover = [p for p in RAW_NEWS_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"]
        if leftover:
            logger.error("%d Naver news file(s) remain under %s - delete them (특약 2.3/2.4).",
                         len(leftover), RAW_NEWS_DIR)
    own_docs, dart_stats = build_dart_docs(RAW_DART_DIR, mapping)
    audit_docs, monthly = build_audit_report_docs(RAW_DART_DIR, mapping)
    # The firms' own filings also show up in the market listing; keep one row per rcept_no.
    dart_docs = list({d["doc_id"]: d for d in audit_docs + own_docs}.values())

    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        inserted = db_manager.insert_news(conn, news_docs)
        db_manager.upsert_dart(conn, dart_docs)
        stored_news = db_manager.fetch_documents(conn, "news")
        groups = assign_dup_groups(stored_news)
        db_manager.update_dup_groups(conn, groups)
        relevance = refined_relevance(stored_news)
        if relevance is not None:
            db_manager.update_relevance(conn, relevance)

    grouped = len(groups) - len(set(groups.values()))
    unmatched = sum(1 for d in news_docs if not d["company_ids"])
    logger.info("news: parsed=%d inserted=%d (exact duplicates/already loaded skipped=%d)",
                len(news_docs), inserted, len(news_docs) - inserted)
    logger.info("news: total in DB=%d, near-duplicates grouped=%d, no company identified=%d",
                len(stored_news), grouped, unmatched)
    if stored_news and relevance is not None:
        relevant = sum(relevance.values())
        logger.info("news relevance (firm_scope refined set): relevant=%d/%d (%.1f%%)",
                    relevant, len(relevance), relevant / len(relevance) * 100)
    logger.info("dart own filings: %s", dart_stats)
    logger.info("dart Big4-filed (market listing): %d rows, with text=%d",
                len(audit_docs), sum(bool(d["content"]) for d in audit_docs))
    if monthly:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        out = PROCESSED_DIR / "audit_filings_monthly.csv"
        with out.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["month", "pblntf_ty", "filer", "count"])
            writer.writeheader()
            writer.writerows(monthly)
        logger.info("wrote %s (%d rows)", out, len(monthly))
    return 0


if __name__ == "__main__":
    sys.exit(main())
