"""Naver news search collector via NAVER API HUB (guide 2-2).

The search API moved from Naver Developers (openapi.naver.com, X-Naver-Client-* headers) to
NAVER API HUB on NAVER Cloud; new keys only work with the API HUB endpoint and
X-NCP-APIGW-API-KEY-ID / X-NCP-APIGW-API-KEY headers. The URL is configurable
(settings.yaml news.api_url) because it was taken from search results, not read from the docs here.

Limits that shape this design (verify against the official docs before relying on them):
- one query returns at most `display` (<=100) items per call and `start` <= 1000,
  so roughly the newest ~1,000 articles per query are reachable, with no date filter.
- Therefore queries are split by company (keyword x alias) and every query logs `total`
  and the oldest pubDate actually reached. Queries with total beyond the reachable window
  are flagged `truncated` so EDA does not mistake missing history for a recent spike.

Raw pages: data/raw/news/<query_slug>/<YYYYMMDD>_s<start>.json
Query log: data/raw/news/query_log.jsonl
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from src.collectors.http_client import RateLimitedClient
from src.common.config import RAW_NEWS_DIR, get_naver_credentials

logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"


@dataclass(frozen=True)
class SearchQuery:
    query: str
    keyword: str      # the configured keyword this query belongs to (documents.search_keyword)
    category: str
    company_id: str | None = None  # set for keyword x alias queries; None for the base keyword query
    topic: str | None = None       # the topic appended to the alias (None for the base keyword query)


@dataclass
class QueryReport:
    query: str
    keyword: str
    total: int
    fetched: int
    newest_pub: str | None
    oldest_pub: str | None
    reachable: int
    truncated: bool               # total exceeds what the API lets us page through
    reached_period_start: bool    # oldest article reached is on/before period.start
    collected_at: str


def build_queries(settings: dict[str, Any], mapping: dict[str, Any]) -> list[SearchQuery]:
    """Base keyword query (optional) plus `<alias> <topic>` for every company alias and topic."""
    news = settings["news"]
    queries: dict[str, SearchQuery] = {}
    for kw in news["keywords"]:
        if news.get("include_base_query", True):
            queries.setdefault(kw["keyword"], SearchQuery(kw["keyword"], kw["keyword"], kw["category"]))
        if not news.get("split_by_company"):
            continue
        topics = kw.get("topics") or [kw["topic"]]
        for company in mapping["companies"]:
            for alias in company.get("search_aliases", []):
                for topic in topics:
                    q = f"{alias} {topic}"
                    queries.setdefault(
                        q, SearchQuery(q, kw["keyword"], kw["category"], company["company_id"], topic)
                    )
    return list(queries.values())


def parse_pub_date(value: str) -> datetime:
    """Naver pubDate is RFC 2822, e.g. 'Mon, 26 Sep 2016 07:50:00 +0900'."""
    return parsedate_to_datetime(value)


def _slug(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()[:10]


class NewsCollector:
    def __init__(
        self,
        client: RateLimitedClient,
        display: int = 100,
        max_start: int = 1000,
        raw_dir: Path = RAW_NEWS_DIR,
        credentials: tuple[str, str] | None = None,
        api_url: str = DEFAULT_API_URL,
    ) -> None:
        self.client = client
        self.display = display
        self.max_start = max_start
        self.raw_dir = raw_dir
        client_id, secret = credentials or get_naver_credentials()
        self.api_url = api_url
        self._headers = {"X-NCP-APIGW-API-KEY-ID": client_id, "X-NCP-APIGW-API-KEY": secret}

    @property
    def reachable(self) -> int:
        return self.max_start + self.display - 1

    def _fetch_page(self, query: SearchQuery, start: int, save: bool) -> dict[str, Any]:
        params = {"query": query.query, "display": self.display, "start": start, "sort": "date"}
        payload = self.client.get(self.api_url, params=params, headers=self._headers).json()
        if save:
            out = self.raw_dir / _slug(query.query) / f"{date.today():%Y%m%d}_s{start}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            record = {"query": asdict(query), "start": start, "response": payload}
            out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        return payload

    def collect(self, query: SearchQuery, period_start: date) -> QueryReport:
        """Page newest -> oldest until the period start, the API limit, or the end of results."""
        total, fetched, newest, oldest = 0, 0, None, None
        start, prev_end = 1, 0
        while start <= self.max_start:
            payload = self._fetch_page(query, start, save=True)
            total = int(payload.get("total", 0))
            items = payload.get("items", [])
            if not items:
                break
            dates = [parse_pub_date(i["pubDate"]) for i in items]
            fetched += len(items) - max(0, prev_end - start + 1)  # last page may overlap the previous one
            prev_end = start + len(items) - 1
            newest = newest or max(dates)
            oldest = min(dates) if oldest is None else min(oldest, *dates)
            if oldest.date() < period_start or start + self.display > total or start == self.max_start:
                break
            # The API accepts start <= max_start, so the deepest page starts AT max_start
            # (e.g. 1000 -> items 1000..1099), not at the next multiple of display.
            start = min(start + self.display, self.max_start)
        report = self._report(query, total, fetched, newest, oldest, period_start)
        self._append_log(report)
        return report

    def probe(self, query: SearchQuery, period_start: date) -> QueryReport:
        """Two calls: first page (total, newest) and the deepest reachable page (oldest reachable)."""
        first = self._fetch_page(query, 1, save=False)
        total = int(first.get("total", 0))
        items = list(first.get("items", []))
        last_start = min(self.max_start, max(1, ((min(total, self.reachable) - 1) // self.display) * self.display + 1))
        if last_start > 1:
            items += self._fetch_page(query, last_start, save=False).get("items", [])
        dates = [parse_pub_date(i["pubDate"]) for i in items]
        return self._report(
            query, total, len(items), max(dates, default=None), min(dates, default=None), period_start
        )

    def _report(self, query, total, fetched, newest, oldest, period_start) -> QueryReport:
        return QueryReport(
            query=query.query,
            keyword=query.keyword,
            total=total,
            fetched=fetched,
            newest_pub=newest.date().isoformat() if newest else None,
            oldest_pub=oldest.date().isoformat() if oldest else None,
            reachable=self.reachable,
            truncated=total > self.reachable,
            reached_period_start=bool(oldest and oldest.date() <= period_start),
            collected_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _append_log(self, report: QueryReport) -> None:
        log = self.raw_dir / "query_log.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(report), ensure_ascii=False) + "\n")
