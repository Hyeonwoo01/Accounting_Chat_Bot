"""Day 4: classify refined news with the LLM (guide 6). Batch; results go to analysis_results.

Usage:
  python scripts/run_llm_classification.py                 # estimate only (no API call)
  python scripts/run_llm_classification.py --run --limit 20   # pilot: 20 articles
  python scripts/run_llm_classification.py --run           # everything not yet classified

Scope (guide 6-1): refined articles only (is_relevant), title + Naver summary as input, and only
the REPRESENTATIVE of each near-duplicate group (earliest relevant article); the other members get
a copy of its result. Articles already classified with the current PROMPT_VERSION are skipped, so a
re-run only pays for what is missing. Failures (malformed answer after one retry, API errors) are written to
data/processed/llm/failures.jsonl (doc_id + reason only) and can be retried by re-running.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import _bootstrap  # noqa: F401

from src.analysis.llm_classifier import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    ClassificationError,
    classify,
    estimate_tokens,
    group_representatives,
    openai_chat,
    propagate,
)
from src.common.config import PROCESSED_DIR, get_llm_model, get_openai_api_key, load_settings, mask_secret
from src.database import db_manager

logger = logging.getLogger("run_llm_classification")
FAIL_LOG = PROCESSED_DIR / "llm" / "failures.jsonl"
BATCH = 50


def pending_articles(prompt_version: str) -> tuple[list[dict], int, dict[str, list[str]]]:
    """(representatives not yet classified, relevant article count, groups)."""
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        news = [d for d in db_manager.fetch_documents(conn, "news") if d["is_relevant"]]
        done = {r["doc_id"] for r in db_manager.fetch_analysis_results(conn, prompt_version)}
    groups = group_representatives(news)
    by_id = {d["doc_id"]: d for d in news}
    return [by_id[rep] for rep in groups if rep not in done], len(news), groups


def propagate_to_members(prompt_version: str, groups: dict[str, list[str]]) -> int:
    with db_manager.connect() as conn:
        results = {r["doc_id"]: r for r in db_manager.fetch_analysis_results(conn, prompt_version)}
        return db_manager.insert_analysis_results(conn, propagate(results, groups))


def estimate(articles: list[dict], cfg: dict) -> dict:
    cpt = cfg["chars_per_token"]
    system = estimate_tokens(SYSTEM_PROMPT, cpt)
    inp = sum(system + estimate_tokens(f"제목: {a['title']}\n요약: {a['content']}", cpt) for a in articles)
    out = len(articles) * cfg["expected_output_tokens"]
    cost = None
    if cfg.get("price_per_1m_input") is not None and cfg.get("price_per_1m_output") is not None:
        cost = inp / 1e6 * cfg["price_per_1m_input"] + out / 1e6 * cfg["price_per_1m_output"]
    return {"calls": len(articles), "input_tokens": inp, "output_tokens": out, "cost_usd": cost}


def run(articles: list[dict], cfg: dict, workers: int) -> tuple[int, int]:
    chat = openai_chat(get_openai_api_key(), get_llm_model(), temperature=cfg.get("temperature"))
    stop_after = cfg.get("stop_after_consecutive_api_errors", 5)
    consecutive_api_errors = 0
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    ok = failed = 0
    buffer: list[dict] = []
    started = time.monotonic()

    def flush() -> None:
        if buffer:
            with db_manager.connect() as conn:
                db_manager.insert_analysis_results(conn, buffer)
            buffer.clear()

    def log_failure(fail_log, doc_id: str, reason: str) -> None:
        fail_log.write(json.dumps({"doc_id": doc_id, "reason": reason, "prompt_version": PROMPT_VERSION,
                                   "at": datetime.now().isoformat(timespec="seconds")}) + "\n")

    queue = iter(articles)
    done = 0
    stopped = False
    # Sliding window: only `workers` requests are in flight, so a systematic error (e.g. an
    # unsupported parameter) stops the run after a few calls instead of after all of them.
    with ThreadPoolExecutor(max_workers=workers) as pool, FAIL_LOG.open("a", encoding="utf-8") as fail_log:
        in_flight: dict = {}

        def submit_next() -> None:
            a = next(queue, None)
            if a is not None:
                fut = pool.submit(classify, chat, a["doc_id"], a["title"], a["content"], cfg["parse_retries"])
                in_flight[fut] = a

        for _ in range(workers):
            submit_next()
        while in_flight:
            fut = next(as_completed(in_flight))
            a = in_flight.pop(fut)
            done += 1
            try:
                buffer.append(fut.result())
                ok += 1
                consecutive_api_errors = 0
            except ClassificationError as e:
                failed += 1
                log_failure(fail_log, a["doc_id"], f"parse: {e}")
            except Exception as e:  # API/network error: record; a re-run retries it
                failed += 1
                consecutive_api_errors += 1
                reason = mask_secret(f"{type(e).__name__}: {e}")[:300]
                log_failure(fail_log, a["doc_id"], reason)
                if consecutive_api_errors >= stop_after and not stopped:
                    stopped = True
                    logger.error("stopping: %d API errors in a row (last: %s). Remaining requests not sent.",
                                 consecutive_api_errors, reason)
            if not stopped:
                submit_next()
            if len(buffer) >= BATCH:
                flush()
            if done % 50 == 0:
                logger.info("%d/%d done (ok=%d failed=%d, %.0fs)", done, len(articles), ok, failed,
                            time.monotonic() - started)
    logger.info("%d/%d processed (ok=%d failed=%d, %.0fs)%s", done, len(articles), ok, failed,
                time.monotonic() - started, " — stopped early" if stopped else "")
    flush()
    return ok, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM classification of refined news (guide 6)")
    parser.add_argument("--run", action="store_true", help="actually call the API (default: estimate only)")
    parser.add_argument("--limit", type=int, default=None, help="classify at most N pending articles")
    parser.add_argument("--workers", type=int, default=4, help="parallel API calls")
    args = parser.parse_args()

    cfg = load_settings()["llm_classification"]
    pending, total, groups = pending_articles(PROMPT_VERSION)
    batch = pending[: args.limit] if args.limit else pending
    est = estimate(batch, cfg)
    print(f"model={get_llm_model()} prompt_version={PROMPT_VERSION}")
    print(f"refined articles={total:,}, near-duplicate groups (LLM calls needed)={len(groups):,}, "
          f"representatives still to classify={len(pending):,}, this run={len(batch):,}")
    print(f"estimated tokens: input≈{est['input_tokens']:,} output≈{est['output_tokens']:,} (rough, ±30%)")
    print("estimated cost: " + (f"≈ ${est['cost_usd']:.2f}" if est["cost_usd"] is not None
                                else "set llm_classification.price_per_1m_* in settings.yaml to estimate"))
    if not args.run:
        print("\nestimate only. Add --run to call the API (try --run --limit 20 first).")
        return 0
    if not batch:
        copied = propagate_to_members(PROMPT_VERSION, groups)
        print(f"nothing to classify. copied results to {copied:,} group member(s).")
        return 0

    ok, failed = run(batch, cfg, args.workers)
    copied = propagate_to_members(PROMPT_VERSION, groups)
    print(f"copied representative results to {copied:,} near-duplicate member(s)")
    print(f"\ndone: ok={ok:,} failed={failed:,}. Failures: {FAIL_LOG} (re-run to retry them)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
