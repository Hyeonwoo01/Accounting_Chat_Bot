"""Collect raw news and DART filings into data/raw/ (guide 12-2, step 1).

Usage: python scripts/collect_data.py [--only news|dart|market]

  news   : Naver news (keyword x alias queries)
  dart   : the Big4 firms' own filings + originals
  market : market-wide 외부감사관련 listings (branch B-1). Long (~1,000+ calls); cached per page,
           so it can be stopped with Ctrl+C and re-run to resume.

Default (no --only) runs news and dart. Safe to re-run.
"""

from __future__ import annotations

import argparse
import logging
import sys

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector, DartFatalError
from src.collectors.http_client import RateLimitedClient
from src.collectors.news_collector import NewsCollector, build_queries
from src.common.config import (
    NAVER_NEWS_DISABLED_REASON,
    get_period,
    get_verified_entities,
    load_company_mapping,
    load_settings,
    naver_news_enabled,
)
from src.common.dart import PBLNTF_TYPES

logger = logging.getLogger("collect_data")


def collect_news(settings: dict) -> None:
    news = settings["news"]
    period = get_period()
    collector = NewsCollector(
        RateLimitedClient(news["request_delay_sec"], news["max_retries"]),
        display=news["display"], max_start=news["max_start"], api_url=news["api_url"],
    )
    for q in build_queries(settings, load_company_mapping()):
        r = collector.collect(q, period.start)
        flag = " [TRUNCATED: period start not reached]" if r.truncated and not r.reached_period_start else ""
        logger.info("news %r: fetched=%d/%d oldest=%s%s", q.query, r.fetched, r.total, r.oldest_pub, flag)


def collect_dart(settings: dict) -> None:
    dart = settings["dart"]
    period = get_period()
    entities = get_verified_entities()
    if not entities:
        logger.warning("no verified DART entities in config/company_mapping.yaml; skipping DART")
        return
    collector = DartCollector(RateLimitedClient(dart["request_delay_sec"], dart["max_retries"]))
    types = dart.get("pblntf_types") or list(PBLNTF_TYPES)
    for e in entities:
        filings = collector.fetch_filings_by_type(e["corp_code"], period.start, period.end, types, dart["page_count"])
        results = [collector.download_document(f["rcept_no"]) for f in filings]
        ok = sum(r.ok for r in results)
        new = sum(r.ok and not r.skipped for r in results)
        logger.info("dart %s: filings=%d documents ok=%d (new %d) failed=%d",
                    e["name"], len(filings), ok, new, len(results) - ok)


def collect_market(settings: dict) -> None:
    dart = settings["dart"]
    period = get_period()
    collector = DartCollector(RateLimitedClient(dart["request_delay_sec"], dart["max_retries"]))
    for ty in settings["dart_market"]["filer_types"]:
        calls = collector.collect_market_listings(period.start, period.end, ty, dart["page_count"])
        logger.info("market %s: done, %d API calls this run (cached pages skipped)", ty, calls)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=["news", "dart", "market"])
    args = parser.parse_args()
    settings = load_settings()
    try:
        if args.only in (None, "news"):
            if naver_news_enabled():
                collect_news(settings)
            elif args.only == "news":
                logger.error(NAVER_NEWS_DISABLED_REASON)
                return 3
            else:
                logger.warning("skipping news: %s", NAVER_NEWS_DISABLED_REASON)
        if args.only in (None, "dart"):
            collect_dart(settings)
        if args.only == "market":
            collect_market(settings)
    except DartFatalError as e:
        logger.error("stopped: %s", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
