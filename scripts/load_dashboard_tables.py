"""Load the dashboard read-model tables from batch outputs (dashboard reads the DB only).

Usage: python scripts/load_dashboard_tables.py
       (after tag_news_firms.py -> process_data.py -> run_eda.py -> summarize_spike_notes.py)

  news_firm_scope        <- DB news + data/processed/news_firm_scope.jsonl  (company_scope, kr_company_ids)
  news_spikes            <- spike candidates recomputed from the DB (same rule as run_eda.py, overall + per firm)
                            + human background from data/processed/eda/spike_annotations.csv
  audit_filings_monthly  <- data/processed/audit_filings_monthly.csv (written by process_data.py)
  dart_corp_names        <- corp_code -> corp_name from data/raw/dart/market/ (audited client names)

Each table is replaced in full, so re-running never duplicates rows. A missing source file leaves
that table empty and is reported.
"""

from __future__ import annotations

import logging
import sys

import _bootstrap  # noqa: F401
import pandas as pd

from run_eda import FIRM_SCOPE_FILE, load_scope_rows, member_to_company
from src.analysis import eda
from src.common.config import PROCESSED_DIR, RAW_DART_DIR, get_period, load_company_mapping, load_settings
from src.database import db_manager
from src.preprocessing.data_processor import iter_market_listings

log = logging.getLogger("load_dashboard_tables")
ANNOTATIONS = PROCESSED_DIR / "eda" / "spike_annotations.csv"
MONTHLY = PROCESSED_DIR / "audit_filings_monthly.csv"
ANNOTATION_FIELDS = {"type": "spike_type", "leading_firm": "leading_firm", "category": "category",
                     "background": "background", "collection_impact": "collection_impact"}


def spike_rows(by_scope: dict[str, pd.DataFrame], annotations: pd.DataFrame) -> list[dict]:
    """Spike candidates per scope ('all' / company_id) joined with recorded backgrounds on (scope, week).

    Notes label the overall scope '전체'; it is stored as 'all'.
    """
    notes: dict[tuple[str, str], dict] = {}
    for r in annotations.to_dict("records"):
        scope = "all" if str(r.get("scope")) == "전체" else str(r.get("scope"))
        notes[(scope, str(r.get("week")))] = r
    rows = []
    for scope, df in by_scope.items():
        for r in df.to_dict("records"):
            note = notes.get((scope, r["week"]), {})
            rows.append({"scope": scope, "week": r["week"], "count": int(r["count"]),
                         "threshold": float(r["threshold"]),
                         **{dst: (None if pd.isna(note.get(src)) else str(note.get(src)))
                            for src, dst in ANNOTATION_FIELDS.items()}})
    return rows


def corp_names(raw_dart_dir=None) -> dict[str, str]:
    """corp_code -> latest corp_name seen in the market listings (names can change; last file wins)."""
    names: dict[str, str] = {}
    for _, _, item in iter_market_listings(raw_dart_dir or RAW_DART_DIR):
        if item.get("corp_code") and item.get("corp_name"):
            names[str(item["corp_code"])] = str(item["corp_name"]).strip()
    return names


def main() -> int:
    settings, period = load_settings(), get_period()
    spike_cfg = settings["spike_detection"]
    start, end = period.start.isoformat(), period.end.isoformat()
    scope_path = PROCESSED_DIR / FIRM_SCOPE_FILE

    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        news = eda.news_frame(db_manager.fetch_documents(conn, "news"))
        if not scope_path.exists():
            log.warning("%s missing: run tag_news_firms.py -> process_data.py first", scope_path)
        if not news.empty:
            news = eda.attach_firm_scope(news, load_scope_rows(scope_path), member_to_company(load_company_mapping()))
            n_scope = db_manager.replace_news_firm_scope(conn, news[["doc_id", "company_scope", "kr_company_ids"]]
                                                         .to_dict("records"))
            by_scope = {"all": eda.spike_candidates(news, spike_cfg["baseline_weeks"], spike_cfg["sigma"],
                                                    start=start, end=end)}
            for c in eda.FIRMS:
                by_scope[c] = eda.spike_candidates(news[news["kr_company_ids"].map(lambda x, c=c: c in x)],
                                                   spike_cfg["baseline_weeks"], spike_cfg["sigma"],
                                                   start=start, end=end)
        else:
            n_scope, by_scope = db_manager.replace_news_firm_scope(conn, []), {}
        if not ANNOTATIONS.exists():
            log.warning("%s missing: spike backgrounds stay empty (run summarize_spike_notes.py)", ANNOTATIONS)
        notes = pd.read_csv(ANNOTATIONS, dtype=str) if ANNOTATIONS.exists() else pd.DataFrame()
        spikes = spike_rows(by_scope, notes)
        n_spikes = db_manager.replace_news_spikes(conn, spikes)

        if not MONTHLY.exists():
            log.warning("%s missing: run process_data.py", MONTHLY)
        monthly = pd.read_csv(MONTHLY, dtype={"month": str}).to_dict("records") if MONTHLY.exists() else []
        n_monthly = db_manager.replace_audit_filings_monthly(conn, monthly)
        n_names = db_manager.replace_dart_corp_names(conn, corp_names())

    annotated = sum(1 for r in spikes if r["spike_type"])
    print(f"news_firm_scope={n_scope:,}  news_spikes={n_spikes} (background recorded {annotated})  "
          f"audit_filings_monthly={n_monthly:,}  dart_corp_names={n_names:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
