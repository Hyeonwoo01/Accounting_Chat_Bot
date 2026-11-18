"""Day 7: load the reviewed conclusions into `insights` (guide 8). Only rows BOTH reviewers approved.

Usage:
  python scripts/load_insights.py --reviewers "홍길동,김철수" --check   # validate only, write nothing
  python scripts/load_insights.py --reviewers "홍길동,김철수"           # replace the insights table

Reads data/processed/insights/insight_drafts.csv (Excel re-saves are fine). A row is loaded when
reviewer_A and reviewer_B are both O (also accepted: Y / yes / 1 / 예 / ○), it has a period and text,
and every evidence doc_id exists in the DB. final_text / final_evidence_doc_ids override the draft.
Rows added by hand (source = manual) are checked the same way. The table is replaced in full, so a
re-run never duplicates; rejected rows are listed with the reason.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import _bootstrap  # noqa: F401

from draft_insights import DRAFTS_FILE
from src.analysis.conclusions import reviewed_insights
from src.common.csv_io import read_excel_csv
from src.database import db_manager


def main() -> int:
    parser = argparse.ArgumentParser(description="Load reviewed conclusions into insights")
    parser.add_argument("--reviewers", required=True, help='the two reviewers, e.g. "홍길동,김철수"')
    parser.add_argument("--check", action="store_true", help="validate only; do not write")
    args = parser.parse_args()
    names = [n.strip() for n in args.reviewers.split(",") if n.strip()]
    if len(names) != 2:
        print("--reviewers needs two names separated by a comma")
        return 2
    if not DRAFTS_FILE.exists():
        print(f"{DRAFTS_FILE} not found. Run scripts/draft_insights.py --run first.")
        return 1

    rows = read_excel_csv(DRAFTS_FILE, dtype=str, keep_default_na=False).to_dict("records")
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        known = {r[0] for r in conn.execute("SELECT doc_id FROM documents").fetchall()}
        accepted, rejected = reviewed_insights(rows, known, ", ".join(names))
        print(f"rows={len(rows)} accepted={len(accepted)} rejected={len(rejected)}")
        for reason in rejected:
            print(f"  - {reason}")
        for r in accepted:
            print(f"  + [{r['period']}] {r['conclusion_text'][:60]}… (근거 {len(r['evidence_doc_ids'])}건)")
        if args.check:
            print("\n--check: nothing written.")
            return 0
        n = db_manager.replace_insights(conn, accepted, datetime.now().isoformat(timespec="seconds"))
    print(f"\ninsights replaced: {n} row(s). The dashboard shows them within 10 minutes (cache) or after a restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
