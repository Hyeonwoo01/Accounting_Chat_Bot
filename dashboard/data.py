"""Read-only data access for the dashboard (guide 9). Everything comes from SQLite via src.database.

No API calls, no LLM, no batch computation here: tables are filled by the batch scripts
(process_data, run_llm_classification, load_dashboard_tables). Loaders are cached; the cache is
dropped after `TTL` seconds so a re-run batch shows up without restarting Streamlit.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from src.common.config import get_big4_corp_codes, get_database_path, load_company_mapping, load_settings
from src.database import db_manager

TTL = 600


def settings() -> dict:
    return load_settings()


def firm_labels() -> dict[str, str]:
    """company_id -> Korean member-firm brand (e.g. pwc -> 삼일PwC)."""
    out = {}
    for c in load_company_mapping()["companies"]:
        members = c.get("member_firms") or [{}]
        out[c["company_id"]] = members[0].get("display_name") or c.get("display_name") or c["company_id"]
    return out


def category_labels() -> dict[str, str]:
    return dict(settings()["categories"])


def db_ready() -> bool:
    return get_database_path().exists()


@st.cache_data(ttl=TTL, show_spinner=False)
def news() -> pd.DataFrame:
    """Relevant news (refined set) with LLM labels and firm scope. One row per article."""
    version = settings()["dashboard"]["llm_prompt_version"]
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        docs = [d for d in db_manager.fetch_documents(conn, "news") if d["is_relevant"]]
        labels = {r["doc_id"]: r for r in db_manager.fetch_analysis_results(conn, version)}
        scope = db_manager.fetch_news_firm_scope(conn)
    rows = []
    for d in docs:
        lab, sc = labels.get(d["doc_id"], {}), scope.get(d["doc_id"], {})
        rows.append({
            "doc_id": d["doc_id"], "title": d["title"], "url": d["url"], "source_name": d["source_name"],
            "published_at": d["published_at"], "dup_group_id": d["dup_group_id"],
            "sentiment": lab.get("sentiment"), "category": lab.get("primary_category"),
            "keywords": lab.get("issue_keywords") or [], "issue_summary": lab.get("issue_summary"),
            "company_scope": sc.get("company_scope", "untagged"), "kr_company_ids": sc.get("kr_company_ids", []),
        })
    df = pd.DataFrame(rows, columns=["doc_id", "title", "url", "source_name", "published_at", "dup_group_id",
                                     "sentiment", "category", "keywords", "issue_summary", "company_scope",
                                     "kr_company_ids"])
    df["published_at"] = pd.to_datetime(df["published_at"])
    df["month"] = df["published_at"].dt.to_period("M").astype(str)
    df["week"] = df["published_at"].dt.to_period("W-SUN").dt.start_time
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def dart() -> pd.DataFrame:
    """DART filings: the firms' OWN filings and Big4-filed CLIENT audit reports (principle 8)."""
    big4 = get_big4_corp_codes()
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        docs = db_manager.fetch_documents(conn, "dart")
        names = db_manager.fetch_dart_corp_names(conn)
    rows = []
    for d in docs:
        own = d.get("corp_code") in big4
        rows.append({
            "doc_id": d["doc_id"], "report_nm": d["report_nm"] or d["title"], "url": d["url"],
            "rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"] or d["published_at"],
            "company_id": (d["company_ids"] or [None])[0], "filer": d["source_name"],
            "filing_role": "own" if own else "audit_client",
            "client_name": None if own else names.get(d.get("corp_code") or "", None),
            "has_text": bool((d.get("content") or "").strip()),
        })
    df = pd.DataFrame(rows, columns=["doc_id", "report_nm", "url", "rcept_no", "rcept_dt", "company_id", "filer",
                                     "filing_role", "client_name", "has_text"])
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"])
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def spikes() -> pd.DataFrame:
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        df = pd.DataFrame(db_manager.fetch_news_spikes(conn),
                          columns=["scope", "week", "count", "threshold", "spike_type", "leading_firm", "category",
                                   "background", "collection_impact"])
    df["week"] = pd.to_datetime(df["week"])
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def audit_monthly() -> pd.DataFrame:
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        df = pd.DataFrame(db_manager.fetch_audit_filings_monthly(conn), columns=["month", "pblntf_ty", "filer", "count"])
    df["month_start"] = pd.to_datetime(df["month"] + "-01")
    return df


@st.cache_data(ttl=TTL, show_spinner=False)
def issue_summaries() -> list[dict]:
    """Reviewed per-quarter issue summaries (only rows both reviewers approved are stored)."""
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        return db_manager.fetch_issue_summaries(conn)


@st.cache_data(ttl=TTL, show_spinner=False)
def insights() -> list[dict]:
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        return db_manager.fetch_insights(conn)
