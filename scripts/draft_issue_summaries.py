"""Advanced extension: draft per-quarter issue summaries with the LLM for two people to review. Batch only.

Usage:
  python scripts/draft_issue_summaries.py            # build the per-quarter inputs + estimate cost (no API call)
  python scripts/draft_issue_summaries.py --run      # one LLM call per quarter -> review sheet
  python scripts/draft_issue_summaries.py --run --force   # redo (the old sheet is kept as a .bak copy)

Inputs (DB only): relevant news with LLM labels, news_firm_scope, news_spikes (human backgrounds).
The LLM sees, per quarter, the largest events' stored labels and one-line LLM summaries plus the human
spike backgrounds — never titles or URLs. Evidence doc_ids come from the cited event ids, locally.

Outputs (local, git-ignored — they hold news titles):
  data/processed/issue_summaries/inputs.json          what the LLM saw per quarter (+ event -> doc_id)
  data/processed/issue_summaries/summary_drafts.csv   review sheet -> scripts/load_issue_summaries.py
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from draft_insights import firm_labels, load_frames
from src.analysis import issue_summary as isum
from src.analysis.llm_classifier import estimate_tokens, openai_chat
from src.common.config import PROCESSED_DIR, get_llm_model, get_openai_api_key, get_period, load_settings, mask_secret

OUT_DIR = PROCESSED_DIR / "issue_summaries"
INPUTS_FILE = OUT_DIR / "inputs.json"
SHEET_FILE = OUT_DIR / "summary_drafts.csv"


def build_inputs(settings: dict) -> tuple[list[tuple], dict[str, str]]:
    cfg = settings["issue_summary"]
    news, dart, spikes, _ = load_frames(settings["dashboard"]["llm_prompt_version"])
    period = get_period()
    firms, cats = firm_labels(), dict(settings["categories"])
    out = []
    for label, start, end in isum.quarters(period.start, period.end):
        payload, evidence = isum.period_payload(news, spikes, label, start, end, firms, cats, cfg["top_events"])
        if payload and payload["관련 기사 수"] >= cfg["min_articles"]:
            out.append((label, start, end, payload, evidence))
    preview = {r.doc_id: f"{r.doc_id} | [뉴스] {r.title} | {r.url}" for r in news.itertuples()}
    return out, preview


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft per-quarter issue summaries for human review")
    parser.add_argument("--run", action="store_true", help="call the LLM (default: inputs + estimate only)")
    parser.add_argument("--force", action="store_true", help="replace an existing review sheet (kept as .bak)")
    args = parser.parse_args()

    settings = load_settings()
    cfg, price = settings["issue_summary"], settings["llm_classification"]
    periods, preview = build_inputs(settings)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUTS_FILE.write_text(json.dumps([{"period": p[0], "input": p[3], "evidence": p[4]} for p in periods],
                                      ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"quarters={len(periods)} ({', '.join(p[0] for p in periods)}) -> {INPUTS_FILE}")
    if not periods:
        print("nothing to summarise: run the LLM classification and load_dashboard_tables.py first.")
        return 1
    inp = sum(estimate_tokens(m["content"], price["chars_per_token"])
              for p in periods for m in isum.build_messages(p[3]))
    out = cfg["expected_output_tokens"] * len(periods)
    cost = inp / 1e6 * price["price_per_1m_input"] + out / 1e6 * price["price_per_1m_output"]
    print(f"model={get_llm_model()} prompt_version={isum.PROMPT_VERSION} calls={len(periods)} (+1 retry each on a malformed answer)")
    print(f"estimated tokens: input≈{inp:,} output≈{out:,} → ≈ ${cost:.3f} (rough)")
    if not args.run:
        print("\nestimate only. Add --run to draft.")
        return 0
    if SHEET_FILE.exists():
        if not args.force:
            print(f"{SHEET_FILE} already exists (it may hold review marks). Use --force to redo it.")
            return 1
        backup = SHEET_FILE.with_suffix(f".{datetime.now():%Y%m%d-%H%M%S}.bak.csv")
        shutil.copy2(SHEET_FILE, backup)
        print(f"previous sheet kept: {backup}")

    chat = openai_chat(get_openai_api_key(), get_llm_model(), temperature=cfg.get("temperature"),
                       timeout=cfg.get("timeout_sec", 120))
    created = datetime.now().isoformat(timespec="seconds")
    rows, failed = [], []
    for label, start, end, payload, evidence in periods:
        try:
            summary, model = isum.draft_period(chat, payload, set(evidence), cfg.get("parse_retries", 1))
        except isum.SummaryError as e:
            failed.append(f"{label}: {e}")
            continue
        except Exception as e:  # API/network error: masked, readable; the other quarters still run
            failed.append(f"{label}: {mask_secret(f'{type(e).__name__}: {e}')[:200]}")
            continue
        rows.append(isum.sheet_row(label, start, end, payload, evidence, summary, model, created, preview))
        print(f"{label}: {summary['headline']}")
    pd.DataFrame(rows, columns=isum.SHEET_COLUMNS).to_csv(SHEET_FILE, index=False, encoding="utf-8-sig")
    flagged = sum(r["number_check"] != "OK" for r in rows)
    print(f"\n{len(rows)} drafts -> {SHEET_FILE} (number check flagged {flagged})")
    for f in failed:
        print(f"  failed {f} — re-run with --force to retry")
    print("Next: both reviewers check the evidence, edit headline / issue_n / evidence_n in place if needed,\n"
          "      mark reviewer_A / reviewer_B with O or X, then: python scripts/load_issue_summaries.py --reviewers \"A,B\"")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
