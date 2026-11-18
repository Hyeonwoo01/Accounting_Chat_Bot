"""Gate 2 step 1: list corpCode.xml candidates for each company's Korean entities.

Usage: python scripts/find_corp_codes.py [--refresh]

Prints candidates only. A human picks the right corp_code, writes it (quoted) into
config/company_mapping.yaml and sets verified: true.
"""

from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector, find_corps
from src.collectors.http_client import RateLimitedClient
from src.common.config import load_company_mapping, load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="re-download corpCode.xml")
    args = parser.parse_args()

    dart = load_settings()["dart"]
    collector = DartCollector(RateLimitedClient(dart["request_delay_sec"], dart["max_retries"]))
    corps = collector.load_corp_codes(refresh=args.refresh)
    print(f"corpCode.xml: {len(corps):,} entities\n")

    for company in load_company_mapping()["companies"]:
        names = [e["name"] for e in company.get("kr_entities", [])]
        matches = find_corps(corps, names)
        print(f"[{company['company_id']}] search={names} -> {len(matches)} candidate(s)")
        for c in matches:
            stock = c["stock_code"] or "-"
            print(f"    corp_code={c['corp_code']}  name={c['corp_name']}  stock={stock}  modified={c['modify_date']}")
        if not matches:
            print("    (none) -> record as 'not found in corpCode.xml' in the gate report")


if __name__ == "__main__":
    main()
