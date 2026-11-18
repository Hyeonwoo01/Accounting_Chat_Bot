"""Make the blind review sheets for LLM results (guide 6-5).

Usage: python scripts/make_llm_review_sample.py [--n 100]   (after run_llm_classification.py --run)

Writes to data/processed/llm_review/ (git-ignored: contains news titles):
  review_A.csv, review_B.csv  same items, WITHOUT LLM labels. Each reviewer fills
                              human_sentiment (긍정/중립/부정) and human_category (규제/기술/시장/인프라)
                              on their own, without looking at the other sheet or the LLM output.
                              The summary is Naver's snippet — the same input the LLM saw. If it is not
                              enough, open `url`, or write 판단불가 (counted separately, not in accuracy).
                              opened_original: 1 if you opened the article to decide.
  summary_check.csv           title + summary + LLM issue_summary: fill summary_factual (1/0)
                              — done AFTER the blind labels, since it shows LLM output
  llm_key.csv                 the LLM labels per sample_id. Do not open before labelling.
  population_cells.csv        representatives per LLM sentiment x category (for weighted accuracy)
Sampling: representatives only (one per near-duplicate group, so 100 items = 100 distinct
events), stratified by LLM sentiment x category, fixed seed (validation.stratified_sample).
Never reuse these items as prompt examples.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from src.analysis.llm_classifier import PROMPT_VERSION, group_representatives
from src.analysis.validation import stratified_sample
from src.common.config import PROCESSED_DIR
from src.common.csv_io import read_excel_csv
from src.database import db_manager

OUT = PROCESSED_DIR / "llm_review"
SEED = 42


HUMAN_COLUMNS = ("human_sentiment", "human_category", "summary_factual")


def labelled_sheets() -> list[str]:
    """Sheets that already hold reviewer input (any non-empty human column)."""
    found = []
    for name in ("review_A.csv", "review_B.csv", "summary_check.csv"):
        path = OUT / name
        if not path.exists():
            continue
        df = read_excel_csv(path, dtype=str)
        cols = [c for c in HUMAN_COLUMNS if c in df]
        if cols and df[cols].fillna("").apply(lambda s: s.str.strip()).ne("").any().any():
            found.append(name)
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=100, help="sample size (guide: 100, at least 50)")
    parser.add_argument("--force", action="store_true", help="overwrite sheets that already contain labels")
    args = parser.parse_args()

    labelled = labelled_sheets()
    if labelled and not args.force:
        print("these sheets already contain reviewer input and were NOT overwritten:")
        for name in labelled:
            print(f"  - {OUT / name}")
        print("back them up first, then re-run with --force to regenerate.")
        return 2

    with db_manager.connect() as conn:
        results = pd.DataFrame(db_manager.fetch_analysis_results(conn, PROMPT_VERSION))
        docs = {d["doc_id"]: d for d in db_manager.fetch_documents(conn, "news")}
    if results.empty:
        print(f"no results for prompt_version={PROMPT_VERSION}. Run run_llm_classification.py --run first.")
        return 1
    reps = set(group_representatives([d for d in docs.values() if d["is_relevant"]]))
    results = results[results["doc_id"].isin(reps)]
    if args.n < 50:
        print("warning: guide 6-5 asks for at least 50 items")

    sample = stratified_sample(results, args.n, SEED).reset_index(drop=True)
    sample.insert(0, "sample_id", [f"S{i + 1:03d}" for i in range(len(sample))])
    sample["title"] = sample["doc_id"].map(lambda d: docs[d]["title"])
    sample["summary"] = sample["doc_id"].map(lambda d: docs[d]["content"])
    sample["url"] = sample["doc_id"].map(lambda d: docs[d]["url"])

    OUT.mkdir(parents=True, exist_ok=True)
    blind = sample[["sample_id", "title", "summary", "url"]].assign(
        human_sentiment="", human_category="", opened_original="", note="")
    for reviewer in ("A", "B"):
        blind.to_csv(OUT / f"review_{reviewer}.csv", index=False, encoding="utf-8-sig")
    results.groupby(["sentiment", "primary_category"]).size().rename("population").reset_index().to_csv(
        OUT / "population_cells.csv", index=False, encoding="utf-8-sig")
    sample[["sample_id", "title", "summary", "url", "issue_summary"]].assign(summary_factual="", note="").to_csv(
        OUT / "summary_check.csv", index=False, encoding="utf-8-sig")
    sample[["sample_id", "doc_id", "sentiment", "primary_category", "issue_keywords", "model_version"]].assign(
        prompt_version=PROMPT_VERSION).to_csv(OUT / "llm_key.csv", index=False, encoding="utf-8-sig")

    cells = sample.groupby(["sentiment", "primary_category"]).size()
    print(f"sample={len(sample)} (prompt_version={PROMPT_VERSION}) -> {OUT}")
    print("by LLM sentiment x category:\n" + cells.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
