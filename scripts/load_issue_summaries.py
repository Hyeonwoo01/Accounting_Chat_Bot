"""Load the reviewed per-quarter issue summaries into `issue_summaries`. Only quarters BOTH reviewers approved.

Usage:
  python scripts/load_issue_summaries.py --reviewers "홍길동,김철수" --check   # validate only
  python scripts/load_issue_summaries.py --reviewers "홍길동,김철수"           # replace the table

A row is loaded when reviewer_A and reviewer_B are both O (also Y / yes / 1 / 예 / ○), it has a headline
and at least one issue, and every issue's evidence doc_ids exist in the DB. The table is replaced in
full, so a re-run never duplicates; rejected rows are listed with the reason.
"""

from __future__ import annotations

import argparse
import sys

import _bootstrap  # noqa: F401

from draft_issue_summaries import SHEET_FILE
from src.analysis.issue_summary import reviewed_summaries
from src.common.csv_io import read_excel_csv
from src.database import db_manager


def main() -> int:
    parser = argparse.ArgumentParser(description="Load reviewed issue summaries")
    parser.add_argument("--reviewers", required=True, help='the two reviewers, e.g. "홍길동,김철수"')
    parser.add_argument("--check", action="store_true", help="validate only; do not write")
    args = parser.parse_args()
    names = [n.strip() for n in args.reviewers.split(",") if n.strip()]
    if len(names) != 2:
        print("--reviewers needs two names separated by a comma")
        return 2
    if not SHEET_FILE.exists():
        print(f"{SHEET_FILE} not found. Run scripts/draft_issue_summaries.py --run first.")
        return 1
    rows = read_excel_csv(SHEET_FILE, dtype=str, keep_default_na=False).to_dict("records")
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        known = {r[0] for r in conn.execute("SELECT doc_id FROM documents").fetchall()}
        accepted, rejected = reviewed_summaries(rows, known, ", ".join(names))
        print(f"rows={len(rows)} accepted={len(accepted)} rejected={len(rejected)}")
        for reason in rejected:
            print(f"  - {reason}")
        for r in accepted:
            print(f"  + [{r['period']}] {r['headline'][:60]} (이슈 {len(r['issues'])}개)")
        if args.check:
            print("\n--check: nothing written.")
            return 0
        n = db_manager.replace_issue_summaries(conn, accepted)
    print(f"\nissue_summaries replaced: {n} row(s). The dashboard shows them within 10 minutes (cache) or after a restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
