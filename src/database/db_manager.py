"""SQLite storage for the news/DART unified schema (guide section 3).

The schema is the contract between the two workstreams. Change it only through a
dedicated PR that states the impact on every layer.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.common.config import get_database_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id          TEXT PRIMARY KEY,               -- NEWS_<hash12> | DART_<rcept_no>
    source_type     TEXT NOT NULL CHECK (source_type IN ('news', 'dart')),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    published_at    TEXT NOT NULL,                  -- YYYY-MM-DD
    url             TEXT NOT NULL,
    source_name     TEXT,
    company_ids     TEXT NOT NULL DEFAULT '[]',     -- JSON array
    search_keyword  TEXT,
    corp_code       TEXT,
    rcept_no        TEXT,
    report_nm       TEXT,
    rcept_dt        TEXT,
    pblntf_ty       TEXT,
    content_hash    TEXT NOT NULL,
    dup_group_id    TEXT,
    is_relevant     INTEGER NOT NULL DEFAULT 1,
    raw_path        TEXT,
    collected_at    TEXT NOT NULL
);
-- Exact duplicate news (same normalized title+content) is stored once.
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_news_hash
    ON documents(content_hash) WHERE source_type = 'news';
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_url ON documents(url);
CREATE INDEX IF NOT EXISTS ix_documents_source_date ON documents(source_type, published_at);

CREATE TABLE IF NOT EXISTS analysis_results (
    doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
    sentiment         TEXT CHECK (sentiment IN ('positive', 'neutral', 'negative')),  -- NULL for DART
    primary_category  TEXT CHECK (primary_category IN
        ('regulation_policy', 'technology_trend', 'market_competition', 'supply_chain_infrastructure')),
    issue_keywords    TEXT NOT NULL DEFAULT '[]',
    issue_summary     TEXT,
    model_version     TEXT NOT NULL,
    prompt_version    TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    PRIMARY KEY (doc_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id       TEXT PRIMARY KEY,               -- same ID used in the vector DB
    doc_id         TEXT NOT NULL REFERENCES documents(doc_id),
    chunk_index    INTEGER NOT NULL,
    section_title  TEXT,
    text           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_samples (
    doc_id            TEXT NOT NULL REFERENCES documents(doc_id),
    prompt_version    TEXT NOT NULL,
    reviewer          TEXT NOT NULL,
    human_sentiment   TEXT,
    human_category    TEXT,
    llm_sentiment     TEXT,
    llm_category      TEXT,
    summary_factual   INTEGER,                      -- 1 yes / 0 no
    note              TEXT,
    PRIMARY KEY (doc_id, prompt_version, reviewer)
);

CREATE TABLE IF NOT EXISTS insights (
    insight_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    period            TEXT NOT NULL,
    conclusion_text   TEXT NOT NULL,
    evidence_doc_ids  TEXT NOT NULL DEFAULT '[]',
    reviewed_by       TEXT,
    created_at        TEXT NOT NULL
);

-- Dashboard read models (filled in batch by scripts/load_dashboard_tables.py; the dashboard only reads them).
-- Firm scope of each news article (guide 4-2, principle 6). kr_company_ids is non-empty only when the
-- article is about a KOREAN member firm (company_scope = 'member_firm'); per-firm counts use it.
CREATE TABLE IF NOT EXISTS news_firm_scope (
    doc_id          TEXT PRIMARY KEY REFERENCES documents(doc_id),
    company_scope   TEXT NOT NULL,                  -- member_firm | global_network | industry | ... | untagged
    kr_company_ids  TEXT NOT NULL DEFAULT '[]'      -- JSON array of company_id
);
-- Weekly spike candidates of relevant news (guide 5-3) with the human-written background, if recorded.
CREATE TABLE IF NOT EXISTS news_spikes (
    scope              TEXT NOT NULL,               -- 'all' or a company_id
    week               TEXT NOT NULL,               -- week start (Monday), YYYY-MM-DD
    count              INTEGER NOT NULL,
    threshold          REAL NOT NULL,               -- previous-8-week mean + 2 sigma
    spike_type         TEXT,                        -- fixed list (scripts/summarize_spike_notes.py); NULL = not recorded
    leading_firm       TEXT,
    category           TEXT,
    background         TEXT,
    collection_impact  TEXT,
    PRIMARY KEY (scope, week)
);
-- Monthly audit-report filings by filer, ALL filers incl. non-Big4 (share denominators; counts, not revenue).
CREATE TABLE IF NOT EXISTS audit_filings_monthly (
    month      TEXT NOT NULL,                       -- YYYY-MM
    pblntf_ty  TEXT NOT NULL,
    filer      TEXT NOT NULL,                       -- Big4 company_id, or 'other' for every non-Big4 filer
    count      INTEGER NOT NULL,
    PRIMARY KEY (month, pblntf_ty, filer)
);
-- Company names by DART corp_code, from the market listings. Lets the dashboard show the AUDITED CLIENT
-- next to a Big4-filed audit report (documents.corp_code is the client; principle 8).
CREATE TABLE IF NOT EXISTS dart_corp_names (
    corp_code  TEXT PRIMARY KEY,
    corp_name  TEXT NOT NULL
);
-- Per-period issue summaries (advanced extension, 2026-11-18). LLM draft in batch -> two reviewers ->
-- only approved rows are stored (scripts/load_issue_summaries.py); the dashboard only reads them.
CREATE TABLE IF NOT EXISTS issue_summaries (
    period          TEXT PRIMARY KEY,               -- e.g. 2025-Q1
    period_start    TEXT NOT NULL,                  -- YYYY-MM-DD
    period_end      TEXT NOT NULL,
    headline        TEXT NOT NULL,
    issues          TEXT NOT NULL DEFAULT '[]',     -- JSON [{"text": ..., "evidence_doc_ids": [...]}]
    article_count   INTEGER,
    model_version   TEXT NOT NULL,
    prompt_version  TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    reviewed_by     TEXT NOT NULL
);
"""

DOCUMENT_COLUMNS = (
    "doc_id", "source_type", "title", "content", "published_at", "url", "source_name",
    "company_ids", "search_keyword", "corp_code", "rcept_no", "report_nm", "rcept_dt",
    "pblntf_ty", "content_hash", "dup_group_id", "is_relevant", "raw_path", "collected_at",
)
# DART rows are re-loaded when the original document is downloaded later, so these are refreshed.
_DART_UPDATABLE = ("title", "content", "company_ids", "content_hash", "raw_path", "collected_at")


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _row(doc: dict[str, Any]) -> tuple[Any, ...]:
    values = []
    for col in DOCUMENT_COLUMNS:
        value = doc.get(col)
        if col == "company_ids":
            value = json.dumps(sorted(value or []), ensure_ascii=False)
        elif col == "is_relevant":
            value = 1 if value is None else int(bool(value))
        values.append(value)
    return tuple(values)


def insert_news(conn: sqlite3.Connection, docs: Iterable[dict[str, Any]]) -> int:
    """Insert news; rows whose doc_id, URL or content_hash already exist are skipped. Returns inserted count."""
    placeholders = ", ".join("?" for _ in DOCUMENT_COLUMNS)
    sql = f"INSERT OR IGNORE INTO documents ({', '.join(DOCUMENT_COLUMNS)}) VALUES ({placeholders})"
    before = conn.total_changes
    conn.executemany(sql, (_row(d) for d in docs))
    return conn.total_changes - before


def upsert_dart(conn: sqlite3.Connection, docs: Iterable[dict[str, Any]]) -> int:
    """Insert DART filings, refreshing extracted content when a filing is re-processed."""
    placeholders = ", ".join("?" for _ in DOCUMENT_COLUMNS)
    updates = ", ".join(f"{c} = excluded.{c}" for c in _DART_UPDATABLE)
    sql = (
        f"INSERT INTO documents ({', '.join(DOCUMENT_COLUMNS)}) VALUES ({placeholders}) "
        f"ON CONFLICT(doc_id) DO UPDATE SET {updates}"
    )
    before = conn.total_changes
    conn.executemany(sql, (_row(d) for d in docs))
    return conn.total_changes - before


def update_dup_groups(conn: sqlite3.Connection, groups: dict[str, str]) -> None:
    conn.executemany(
        "UPDATE documents SET dup_group_id = ? WHERE doc_id = ?",
        ((group, doc_id) for doc_id, group in groups.items()),
    )


def insert_analysis_results(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Insert LLM results; (doc_id, prompt_version) already present is kept as is. Returns inserted count."""
    sql = (
        "INSERT OR IGNORE INTO analysis_results (doc_id, sentiment, primary_category, issue_keywords, "
        "issue_summary, model_version, prompt_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    before = conn.total_changes
    conn.executemany(sql, (
        (r["doc_id"], r.get("sentiment"), r["primary_category"], json.dumps(r["issue_keywords"], ensure_ascii=False),
         r["issue_summary"], r["model_version"], r["prompt_version"], r["created_at"]) for r in rows
    ))
    return conn.total_changes - before


def fetch_analysis_results(conn: sqlite3.Connection, prompt_version: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM analysis_results WHERE prompt_version = ? ORDER BY doc_id", (prompt_version,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["issue_keywords"] = json.loads(d["issue_keywords"])
        out.append(d)
    return out


def update_relevance(conn: sqlite3.Connection, relevance: dict[str, bool]) -> None:
    conn.executemany(
        "UPDATE documents SET is_relevant = ? WHERE doc_id = ?",
        ((int(flag), doc_id) for doc_id, flag in relevance.items()),
    )


def fetch_documents(conn: sqlite3.Connection, source_type: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM documents WHERE source_type = ? ORDER BY published_at, doc_id", (source_type,)
    ).fetchall()
    docs = []
    for r in rows:
        d = dict(r)
        d["company_ids"] = json.loads(d["company_ids"])
        d["is_relevant"] = bool(d["is_relevant"])
        docs.append(d)
    return docs


def fetch_chunk_texts(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    rows = conn.execute("SELECT text FROM chunks WHERE doc_id = ? ORDER BY chunk_index", (doc_id,)).fetchall()
    return [r[0] for r in rows]


def replace_chunks(conn: sqlite3.Connection, doc_id: str, chunks: Iterable[dict[str, Any]]) -> int:
    """Replace one document's chunks (re-chunking a refreshed filing must not leave stale rows)."""
    conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    before = conn.total_changes
    conn.executemany(
        "INSERT INTO chunks (chunk_id, doc_id, chunk_index, section_title, text) VALUES (?, ?, ?, ?, ?)",
        ((c["chunk_id"], doc_id, c["chunk_index"], c.get("section_title"), c["text"]) for c in chunks),
    )
    return conn.total_changes - before


# ---------------------------------------------------------------- dashboard read models
# Each loader replaces the whole table in one transaction: the source files are rebuilt in full by
# their scripts, so a re-run must not keep rows that disappeared from the source.

_SPIKE_COLUMNS = ("scope", "week", "count", "threshold", "spike_type", "leading_firm", "category",
                  "background", "collection_impact")


def replace_news_firm_scope(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM news_firm_scope")
    conn.executemany(
        "INSERT INTO news_firm_scope (doc_id, company_scope, kr_company_ids) VALUES (?, ?, ?)",
        ((r["doc_id"], r["company_scope"], json.dumps(sorted(r.get("kr_company_ids") or []))) for r in rows),
    )
    return conn.execute("SELECT COUNT(*) FROM news_firm_scope").fetchone()[0]


def fetch_news_firm_scope(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT doc_id, company_scope, kr_company_ids FROM news_firm_scope").fetchall()
    return {r[0]: {"company_scope": r[1], "kr_company_ids": json.loads(r[2])} for r in rows}


def replace_news_spikes(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM news_spikes")
    placeholders = ", ".join("?" for _ in _SPIKE_COLUMNS)
    conn.executemany(
        f"INSERT INTO news_spikes ({', '.join(_SPIKE_COLUMNS)}) VALUES ({placeholders})",
        (tuple(r.get(c) for c in _SPIKE_COLUMNS) for r in rows),
    )
    return conn.execute("SELECT COUNT(*) FROM news_spikes").fetchone()[0]


def fetch_news_spikes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {', '.join(_SPIKE_COLUMNS)} FROM news_spikes ORDER BY scope, week").fetchall()
    return [dict(zip(_SPIKE_COLUMNS, r)) for r in rows]


def replace_audit_filings_monthly(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    conn.execute("DELETE FROM audit_filings_monthly")
    conn.executemany(
        "INSERT INTO audit_filings_monthly (month, pblntf_ty, filer, count) VALUES (?, ?, ?, ?)",
        ((str(r["month"]), r["pblntf_ty"], r["filer"], int(r["count"])) for r in rows),
    )
    return conn.execute("SELECT COUNT(*) FROM audit_filings_monthly").fetchone()[0]


def fetch_audit_filings_monthly(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT month, pblntf_ty, filer, count FROM audit_filings_monthly ORDER BY month").fetchall()
    return [{"month": r[0], "pblntf_ty": r[1], "filer": r[2], "count": r[3]} for r in rows]


def replace_dart_corp_names(conn: sqlite3.Connection, names: dict[str, str]) -> int:
    conn.execute("DELETE FROM dart_corp_names")
    conn.executemany("INSERT INTO dart_corp_names (corp_code, corp_name) VALUES (?, ?)", sorted(names.items()))
    return conn.execute("SELECT COUNT(*) FROM dart_corp_names").fetchone()[0]


def fetch_dart_corp_names(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT corp_code, corp_name FROM dart_corp_names").fetchall())


def replace_insights(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]], created_at: str) -> int:
    """Replace all conclusions with the reviewed set (scripts/load_insights.py). Re-running never duplicates."""
    conn.execute("DELETE FROM insights")
    conn.executemany(
        "INSERT INTO insights (period, conclusion_text, evidence_doc_ids, reviewed_by, created_at) VALUES (?, ?, ?, ?, ?)",
        ((r["period"], r["conclusion_text"], json.dumps(list(r["evidence_doc_ids"]), ensure_ascii=False),
          r.get("reviewed_by"), created_at) for r in rows),
    )
    return conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]


_SUMMARY_COLUMNS = ("period", "period_start", "period_end", "headline", "issues", "article_count",
                    "model_version", "prompt_version", "created_at", "reviewed_by")


def replace_issue_summaries(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    """Replace all period summaries with the reviewed set (re-running never duplicates)."""
    conn.execute("DELETE FROM issue_summaries")
    placeholders = ", ".join("?" for _ in _SUMMARY_COLUMNS)
    conn.executemany(
        f"INSERT INTO issue_summaries ({', '.join(_SUMMARY_COLUMNS)}) VALUES ({placeholders})",
        (tuple(json.dumps(r["issues"], ensure_ascii=False) if c == "issues" else r.get(c) for c in _SUMMARY_COLUMNS)
         for r in rows),
    )
    return conn.execute("SELECT COUNT(*) FROM issue_summaries").fetchone()[0]


def fetch_issue_summaries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(f"SELECT {', '.join(_SUMMARY_COLUMNS)} FROM issue_summaries ORDER BY period_start").fetchall()
    out = [dict(zip(_SUMMARY_COLUMNS, r)) for r in rows]
    for r in out:
        r["issues"] = json.loads(r["issues"])
    return out


def fetch_insights(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT insight_id, period, conclusion_text, evidence_doc_ids, reviewed_by, created_at "
        "FROM insights ORDER BY insight_id"
    ).fetchall()
    cols = ("insight_id", "period", "conclusion_text", "evidence_doc_ids", "reviewed_by", "created_at")
    out = [dict(zip(cols, r)) for r in rows]
    for r in out:
        r["evidence_doc_ids"] = json.loads(r["evidence_doc_ids"])
    return out
