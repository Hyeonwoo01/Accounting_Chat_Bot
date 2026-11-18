"""Day 7: draft dashboard conclusions with the LLM for two people to review (guide 8). Batch only.

Usage:
  python scripts/draft_insights.py            # build the fact list + estimate cost (no API call)
  python scripts/draft_insights.py --run      # one LLM call -> review sheet
  python scripts/draft_insights.py --run --force   # redo (the old sheet is kept as a .bak copy)

Inputs (DB only, after load_dashboard_tables.py): relevant news with LLM labels (settings.dashboard.
llm_prompt_version), news_firm_scope, news_spikes (human backgrounds), audit_filings_monthly, DART docs.

Outputs (local, git-ignored — they hold news titles):
  data/processed/insights/facts.json          the numbered facts the LLM saw (+ their evidence doc_ids)
  data/processed/insights/insight_drafts.csv  review sheet: fill reviewer_A / reviewer_B (O or X),
                                              optionally final_text / final_evidence_doc_ids, then run
                                              scripts/load_insights.py
The LLM gets aggregates and the human-written spike backgrounds only — never article titles or summaries.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from src.analysis import conclusions as cc
from src.analysis.llm_classifier import estimate_tokens, openai_chat
from src.common.config import (
    PROCESSED_DIR,
    get_big4_corp_codes,
    get_llm_model,
    get_openai_api_key,
    get_period,
    load_company_mapping,
    load_settings,
    mask_secret,
)
from src.database import db_manager

OUT_DIR = PROCESSED_DIR / "insights"
FACTS_FILE = OUT_DIR / "facts.json"
DRAFTS_FILE = OUT_DIR / "insight_drafts.csv"


def firm_labels() -> dict[str, str]:
    out = {}
    for c in load_company_mapping()["companies"]:
        members = c.get("member_firms") or [{}]
        out[c["company_id"]] = members[0].get("display_name") or c.get("display_name") or c["company_id"]
    return out


def load_frames(prompt_version: str) -> tuple[pd.DataFrame, pd.DataFrame, list[dict], pd.DataFrame]:
    """(relevant news with labels + firm scope, DART docs with filing role, spikes, audit monthly)."""
    big4 = get_big4_corp_codes()
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        docs = [d for d in db_manager.fetch_documents(conn, "news") if d["is_relevant"]]
        labels = {r["doc_id"]: r for r in db_manager.fetch_analysis_results(conn, prompt_version)}
        scope = db_manager.fetch_news_firm_scope(conn)
        dart_docs = db_manager.fetch_documents(conn, "dart")
        spikes = db_manager.fetch_news_spikes(conn)
        monthly = pd.DataFrame(db_manager.fetch_audit_filings_monthly(conn), columns=["month", "pblntf_ty", "filer", "count"])
    news = pd.DataFrame([{
        "doc_id": d["doc_id"], "title": d["title"], "url": d["url"], "published_at": d["published_at"],
        "dup_group_id": d["dup_group_id"],
        "sentiment": labels.get(d["doc_id"], {}).get("sentiment"),
        "category": labels.get(d["doc_id"], {}).get("primary_category"),
        "keywords": labels.get(d["doc_id"], {}).get("issue_keywords") or [],
        "issue_summary": labels.get(d["doc_id"], {}).get("issue_summary"),
        "kr_company_ids": scope.get(d["doc_id"], {}).get("kr_company_ids", []),
    } for d in docs], columns=["doc_id", "title", "url", "published_at", "dup_group_id", "sentiment", "category",
                              "keywords", "issue_summary", "kr_company_ids"])
    news["published_at"] = pd.to_datetime(news["published_at"])
    dart = pd.DataFrame([{
        "doc_id": d["doc_id"], "report_nm": d["report_nm"] or d["title"], "url": d["url"],
        "rcept_dt": d["rcept_dt"] or d["published_at"], "company_id": (d["company_ids"] or [None])[0],
        "filing_role": "own" if d.get("corp_code") in big4 else "audit_client",
    } for d in dart_docs], columns=["doc_id", "report_nm", "url", "rcept_dt", "company_id", "filing_role"])
    dart["rcept_dt"] = pd.to_datetime(dart["rcept_dt"])
    return news, dart, spikes, monthly


def build_facts(cfg: dict, settings: dict) -> tuple[list[cc.Fact], dict[str, str]]:
    news, dart, spikes, monthly = load_frames(settings["dashboard"]["llm_prompt_version"])
    period = get_period()
    months = range(cfg["months"][0], cfg["months"][1] + 1)
    years = range(period.start.year, period.end.year + 1)
    firms, cats = firm_labels(), dict(settings["categories"])
    facts = cc.number_facts(
        cc.category_facts(news, years, months, cats, cfg["evidence_per_category"])
        + cc.sentiment_facts(news, years, months, firms, cfg["min_articles_for_sentiment"])
        + cc.audit_facts(monthly, dart, years, months, firms)
        + cc.spike_facts(spikes, news, firms, cfg["evidence_per_spike"])
    )
    preview = {r.doc_id: f"{r.doc_id} | [뉴스] {r.title} | {r.url}" for r in news.itertuples()}
    preview.update({r.doc_id: f"{r.doc_id} | [공시] {r.report_nm} ({r.rcept_dt:%Y-%m-%d}) | {r.url}"
                    for r in dart.itertuples()})
    return facts, preview


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft dashboard conclusions for human review (guide 8)")
    parser.add_argument("--run", action="store_true", help="call the LLM (default: facts + estimate only)")
    parser.add_argument("--force", action="store_true", help="replace an existing review sheet (kept as .bak)")
    args = parser.parse_args()

    settings = load_settings()
    cfg, price = settings["insights"], settings["llm_classification"]
    facts, preview = build_facts(cfg, settings)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    FACTS_FILE.write_text(json.dumps([{**f.for_prompt(), "evidence_doc_ids": f.evidence_doc_ids} for f in facts],
                                     ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = pd.Series([f.kind for f in facts]).value_counts().to_dict() if facts else {}
    print(f"facts={len(facts)} {kinds} -> {FACTS_FILE}")
    if not facts:
        print("no facts: run load_dashboard_tables.py (and the LLM classification) first.")
        return 1

    messages = cc.build_messages(facts)
    inp = sum(estimate_tokens(m["content"], price["chars_per_token"]) for m in messages)
    out = cfg["expected_output_tokens"]
    cost = inp / 1e6 * price["price_per_1m_input"] + out / 1e6 * price["price_per_1m_output"]
    print(f"model={get_llm_model()} prompt_version={cc.PROMPT_VERSION} calls=1 (+1 retry on a malformed answer)")
    print(f"estimated tokens: input≈{inp:,} output≈{out:,} → ≈ ${cost:.3f} (rough)")
    if not args.run:
        print("\nestimate only. Add --run to draft.")
        return 0
    if DRAFTS_FILE.exists():
        if not args.force:
            print(f"{DRAFTS_FILE} already exists (it may hold review marks). Use --force to redo it.")
            return 1
        backup = DRAFTS_FILE.with_suffix(f".{datetime.now():%Y%m%d-%H%M%S}.bak.csv")
        shutil.copy2(DRAFTS_FILE, backup)
        print(f"previous sheet kept: {backup}")

    chat = openai_chat(get_openai_api_key(), get_llm_model(), temperature=cfg.get("temperature"),
                       timeout=cfg.get("timeout_sec", 180))
    try:
        drafts, model = cc.draft(chat, facts, cfg.get("parse_retries", 1))
    except cc.DraftError as e:
        print(f"the model's answer failed validation twice: {e}. Re-run to try again.")
        return 1
    except Exception as e:  # API/network error: readable and masked, no traceback with request details
        print(f"API call failed: {mask_secret(f'{type(e).__name__}: {e}')[:300]}")
        return 1
    rows = cc.draft_rows(drafts, facts, model, datetime.now().isoformat(timespec="seconds"), preview)
    pd.DataFrame(rows, columns=cc.DRAFT_COLUMNS).to_csv(DRAFTS_FILE, index=False, encoding="utf-8-sig")
    flagged = sum(r["number_check"] != "OK" for r in rows)
    print(f"\n{len(rows)} drafts -> {DRAFTS_FILE} (number check flagged {flagged})")
    print("Next: both reviewers fill reviewer_A / reviewer_B with O or X after checking the evidence links,\n"
          "      fix wording in final_text if needed, then: python scripts/load_insights.py --reviewers \"A,B\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
