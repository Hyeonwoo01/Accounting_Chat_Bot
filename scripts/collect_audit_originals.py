"""Download a reproducible sample of Big4-filed client audit report originals (branch B-1).

Usage: python scripts/collect_audit_originals.py

Selection (config/settings.yaml dart_market.originals): submitted in the configured month,
corrections excluded, optional client class filter, one filing per client, N random clients
per firm, fixed seed.
The selected list is written to evaluation/audit_originals_sample.csv (metadata only, no text)
so the sample is reviewable and reproducible. Already downloaded originals are skipped.
Run scripts/process_data.py afterwards to load the extracted text.
"""

from __future__ import annotations

import csv
import sys
from collections import Counter

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector, DartFatalError
from src.collectors.http_client import RateLimitedClient
from src.common.config import EVALUATION_DIR, RAW_DART_DIR, get_big4_corp_codes, load_company_mapping, load_settings
from src.common.dart import viewer_url
from src.preprocessing.data_processor import select_audit_originals

FIELDS = ["rcept_no", "company_id", "corp_code", "corp_name", "corp_cls", "rcept_dt", "report_nm", "url"]


def main() -> int:
    settings = load_settings()
    cfg = settings["dart_market"]["originals"]
    sample = select_audit_originals(
        RAW_DART_DIR, load_company_mapping(), get_big4_corp_codes(),
        month=cfg["month"], corp_cls=cfg["corp_cls"], per_firm=cfg["per_firm"], seed=cfg["seed"],
        exclude_corrections=cfg.get("exclude_corrections", True),
    )
    if not sample:
        print("No candidates. Run `collect_data.py --only market` first.")
        return 1

    EVALUATION_DIR.mkdir(exist_ok=True)
    out = EVALUATION_DIR / "audit_originals_sample.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for s in sample:
            writer.writerow({k: (viewer_url(s["rcept_no"]) if k == "url" else s.get(k, "")) for k in FIELDS})
    print(f"selected {len(sample)}: {dict(Counter(s['company_id'] for s in sample))} -> {out}", flush=True)

    dart = settings["dart"]
    collector = DartCollector(RateLimitedClient(dart["request_delay_sec"], dart["max_retries"]))
    ok = failed = skipped = 0
    try:
        for i, s in enumerate(sample, 1):
            result = collector.download_document(s["rcept_no"])
            ok += result.ok
            failed += not result.ok
            skipped += result.skipped
            status = "skip" if result.skipped else ("ok" if result.ok else f"FAIL {result.error[:60]}")
            print(f"  {i}/{len(sample)} {s['company_id']} {s['corp_name']} {s['report_nm']}: {status}", flush=True)
    except DartFatalError as e:
        print(f"stopped: {e}")
        return 2
    print(f"\ndone: ok={ok} (already had {skipped}), failed={failed}. Next: python scripts/process_data.py")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
