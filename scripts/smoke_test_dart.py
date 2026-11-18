"""Smoke test against the REAL DART API with a tiny scope (about 5 calls, under a minute).

Usage: python scripts/smoke_test_dart.py

Unit tests use fake HTTP responses; this checks that the live API still matches our
assumptions before the long collection runs. Everything is written to a temporary
directory — data/ is not touched. Exit code 1 if any check fails.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Callable
from datetime import date
from pathlib import Path

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector
from src.collectors.http_client import RateLimitedClient
from src.common.config import get_big4_corp_codes, load_company_mapping, load_settings
from src.database import db_manager
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.data_processor import build_audit_report_docs, extract_dart_zip

# Off-peak fortnight: few pages, but Big4 filings were present in the B-1 sample (Jul-Sep).
WINDOW = (date(2026, 8, 3), date(2026, 8, 14))


def run_checks(
    collector: DartCollector, raw_dir: Path, mapping: dict, window=WINDOW, big4_corp_codes: set[str] | None = None
) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, fn: Callable[[], str]) -> bool:
        try:
            results.append((name, True, fn()))
            return True
        except Exception as e:  # report and stop dependent checks
            results.append((name, False, f"{type(e).__name__}: {e}"))
            return False

    state: dict = {}

    def market_list() -> str:
        payload = collector.list_page(*window, pblntf_ty="F", page=1)
        assert "total_count" in payload, "response has no total_count"
        return f"total_count={int(payload['total_count']):,}, total_page={payload.get('total_page')}"

    def cache_resume() -> str:
        first = collector.collect_market_listings(*window, "F")
        second = collector.collect_market_listings(*window, "F")
        assert first > 0, "first run made no calls"
        assert second == 0, f"second run made {second} calls; cache not used"
        return f"first run {first} call(s), second run 0"

    def big4_rows() -> str:
        docs, monthly = build_audit_report_docs(raw_dir, mapping)
        state["docs"] = docs
        assert docs, "no filing with a Big4 filer in this window"
        by_firm: dict[str, int] = {}
        for d in docs:
            by_firm[d["company_ids"][0]] = by_firm.get(d["company_ids"][0], 0) + 1
        return f"{len(docs)} Big4-filed rows {by_firm}; monthly rows={len(monthly)}"

    def original_text() -> str:
        # Prefer a CLIENT audit report: the firms' own reports are not where names get spaced out.
        own = big4_corp_codes or set()
        clients = [d for d in state["docs"] if d["corp_code"] not in own]
        assert clients, "only the firms' own filings in this window; no client audit report to check"
        doc = clients[0]
        result = collector.download_document(doc["rcept_no"])
        assert result.ok, result.error
        text = extract_dart_zip(result.path, doc["rcept_no"])
        assert text.strip(), "extracted text is empty"
        state.update(doc=doc, text=text)
        return f"client audit report {doc['rcept_no']} {doc['report_nm']} (filer {doc['company_ids'][0]}) -> {len(text):,} chars"

    def filer_in_text() -> str:
        found = CompanyMatcher(mapping).match(state["text"])
        filer = state["doc"]["company_ids"][0]
        assert filer in found, (
            f"filer {filer} not found in the document text (found {found or 'none'}); "
            "check how the firm name is written in the original"
        )
        return f"filer {filer} identified in text (all matches: {found})"

    def db_load() -> str:
        with db_manager.connect(raw_dir / "smoke.db") as conn:
            db_manager.init_db(conn)
            db_manager.upsert_dart(conn, state["docs"])
            db_manager.upsert_dart(conn, state["docs"])  # re-run must not duplicate
            n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert n == len(state["docs"]), f"{n} rows for {len(state['docs'])} docs"
        return f"{n} rows after loading twice"

    if check("1. market list.json without corp_code", market_list) and \
            check("2. page cache and resume", cache_resume) and \
            check("3. Big4-filed rows from listing", big4_rows) and \
            check("4. original download and text extraction", original_text):
        check("5. filer name found in original (spaced-name fix)", filer_in_text)
        check("6. idempotent DB load", db_load)
    return results


def main() -> int:
    dart = load_settings()["dart"]
    with tempfile.TemporaryDirectory(prefix="dart_smoke_") as tmp:
        raw_dir = Path(tmp)
        collector = DartCollector(RateLimitedClient(dart["request_delay_sec"], dart["max_retries"]), raw_dir=raw_dir)
        results = run_checks(collector, raw_dir, load_company_mapping(), big4_corp_codes=get_big4_corp_codes())

    print(f"\nDART smoke test — window {WINDOW[0]} ~ {WINDOW[1]}\n")
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name}\n       {detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed} passed, {failed} failed" + ("" if len(results) == 6 else " (later checks skipped)"))
    return 0 if failed == 0 and len(results) == 6 else 1


if __name__ == "__main__":
    sys.exit(main())
