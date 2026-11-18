from __future__ import annotations

import json
from datetime import date

import pytest
import requests

from src.collectors.dart_collector import DartCollector, DartFatalError, find_corps, parse_corp_codes
from src.collectors.http_client import CollectorError, RateLimitedClient
from src.collectors.news_collector import NewsCollector, SearchQuery, build_queries
from tests.conftest import FakeResponse, FakeSession, make_zip

KEY = "TEST-DART-KEY-123"
CORP_XML = """<?xml version="1.0" encoding="UTF-8"?><result>
<list><corp_code>00000001</corp_code><corp_name>삼일회계법인</corp_name><stock_code> </stock_code><modify_date>20240101</modify_date></list>
<list><corp_code>00000002</corp_code><corp_name>삼일제약</corp_name><stock_code>000520</stock_code><modify_date>20240101</modify_date></list>
</result>"""


def client(session, no_sleep, retries=3):
    return RateLimitedClient(delay_sec=0, max_retries=retries, session=session, sleep=no_sleep)


# ---------------------------------------------------------------- http client

def test_client_retries_transient_errors(no_sleep):
    session = FakeSession([FakeResponse(503), requests.ConnectionError("boom"), FakeResponse(200, b"ok")])
    assert client(session, no_sleep).get("https://x").content == b"ok"
    assert len(session.calls) == 3


def test_client_error_message_masks_secret(no_sleep, monkeypatch):
    monkeypatch.setenv("DART_API_KEY", KEY)
    session = FakeSession([requests.ConnectionError(f"failed https://x?crtfc_key={KEY}")])
    with pytest.raises(CollectorError) as e:
        client(session, no_sleep, retries=1).get("https://x")
    assert KEY not in str(e.value)


# ---------------------------------------------------------------- DART

def test_parse_and_find_corp_codes():
    corps = parse_corp_codes(make_zip({"CORPCODE.xml": CORP_XML}))
    assert [c["corp_code"] for c in find_corps(corps, ["삼일회계법인"])] == ["00000001"]


def test_fetch_filings_paginates_and_saves_raw_without_key(tmp_path, no_sleep):
    pages = [
        FakeResponse(payload={"status": "000", "total_page": 2, "list": [{"rcept_no": "1"}]}),
        FakeResponse(payload={"status": "000", "total_page": 2, "list": [{"rcept_no": "2"}]}),
    ]
    collector = DartCollector(client(FakeSession(pages), no_sleep), raw_dir=tmp_path, api_key=KEY)
    filings = collector.fetch_filings("00000001", date(2024, 1, 1), date(2024, 12, 31), pblntf_ty="A")
    assert [f["rcept_no"] for f in filings] == ["1", "2"]
    saved = sorted((tmp_path / "list").glob("*.json"))
    assert len(saved) == 2
    record = json.loads(saved[0].read_text(encoding="utf-8"))
    assert record["request"]["pblntf_ty"] == "A"
    assert KEY not in saved[0].read_text(encoding="utf-8")


def test_fetch_filings_no_data_and_fatal_status(tmp_path, no_sleep):
    empty = DartCollector(client(FakeSession([FakeResponse(payload={"status": "013"})]), no_sleep),
                          raw_dir=tmp_path, api_key=KEY)
    assert empty.fetch_filings("1", date(2024, 1, 1), date(2024, 2, 1)) == []

    limited = DartCollector(client(FakeSession([FakeResponse(payload={"status": "020"})]), no_sleep),
                            raw_dir=tmp_path, api_key=KEY)
    with pytest.raises(DartFatalError):
        limited.fetch_filings("1", date(2024, 1, 1), date(2024, 2, 1))


def test_fetch_filings_by_type_tags_type(tmp_path, no_sleep):
    pages = [
        FakeResponse(payload={"status": "000", "total_page": 1, "list": [{"rcept_no": "1"}]}),
        FakeResponse(payload={"status": "013"}),
    ]
    collector = DartCollector(client(FakeSession(pages), no_sleep), raw_dir=tmp_path, api_key=KEY)
    filings = collector.fetch_filings_by_type("1", date(2024, 1, 1), date(2024, 2, 1), ["A", "B"])
    assert filings == [{"rcept_no": "1", "pblntf_ty": "A"}]


def test_download_document_success_skip_and_failure(tmp_path, no_sleep):
    good = make_zip({"20240101000001.xml": "<DOCUMENT/>"})
    session = FakeSession([
        FakeResponse(200, good),
        FakeResponse(200, b'{"status":"014","message":"file not found"}'),
    ])
    collector = DartCollector(client(session, no_sleep), raw_dir=tmp_path, api_key=KEY)

    first = collector.download_document("20240101000001")
    assert first.ok and not first.skipped and first.path.read_bytes() == good
    assert collector.download_document("20240101000001").skipped  # no request made
    assert len(session.calls) == 1

    failed = collector.download_document("20240101000002")
    assert not failed.ok
    log = (tmp_path / "failures.jsonl").read_text(encoding="utf-8")
    assert "20240101000002" in log and KEY not in log
    assert not collector.document_path("20240101000002").exists()


# ---------------------------------------------------------------- Naver news

def _item(pub: str, url: str = "https://news.example.com/1") -> dict:
    return {"title": "t", "description": "d", "originallink": url, "link": url, "pubDate": pub}


def test_build_queries_splits_by_company():
    settings = {"news": {"split_by_company": True,
                         "keywords": [{"keyword": "회계법인 AI", "topic": "AI", "category": "technology_trend"}]}}
    mapping = {"companies": [{"company_id": "pwc", "search_aliases": ["삼일PwC"]},
                             {"company_id": "kpmg", "search_aliases": ["삼정KPMG"]}]}
    assert [q.query for q in build_queries(settings, mapping)] == ["회계법인 AI", "삼일PwC AI", "삼정KPMG AI"]


def test_news_collect_stops_at_period_start(tmp_path, no_sleep):
    pages = [
        FakeResponse(payload={"total": 5000, "items": [_item("Mon, 02 Sep 2024 09:00:00 +0900")] * 2}),
        FakeResponse(payload={"total": 5000, "items": [_item("Fri, 01 Sep 2023 09:00:00 +0900")] * 2}),
    ]
    session = FakeSession(pages)
    collector = NewsCollector(client(session, no_sleep), display=2, max_start=1000,
                              raw_dir=tmp_path, credentials=("id", "secret"))
    report = collector.collect(SearchQuery("q", "kw", "technology_trend"), date(2023, 11, 1))
    assert len(session.calls) == 2  # stopped after passing the period start
    assert report.reached_period_start and report.truncated
    assert report.oldest_pub == "2023-09-01"
    assert (tmp_path / "query_log.jsonl").exists()


def test_news_probe_flags_truncation(no_sleep, tmp_path):
    pages = [
        FakeResponse(payload={"total": 50000, "items": [_item("Mon, 02 Sep 2026 09:00:00 +0900")]}),
        FakeResponse(payload={"total": 50000, "items": [_item("Mon, 01 Jul 2026 09:00:00 +0900")]}),
    ]
    session = FakeSession(pages)
    collector = NewsCollector(client(session, no_sleep), raw_dir=tmp_path, credentials=("id", "secret"))
    report = collector.probe(SearchQuery("q", "kw", "c"), date(2023, 11, 1))
    assert session.calls[1]["params"]["start"] == 1000
    assert session.calls[0]["url"] == "https://naverapihub.apigw.ntruss.com/search/v1/news"
    assert session.calls[0]["headers"] == {"X-NCP-APIGW-API-KEY-ID": "id", "X-NCP-APIGW-API-KEY": "secret"}
    assert report.truncated and not report.reached_period_start
    assert not any(tmp_path.iterdir())  # probe does not write raw data


# ---------------------------------------------------------------- market listings (branch B-1)

def test_quarter_windows():
    from src.collectors.dart_collector import quarter_windows

    w = quarter_windows(date(2023, 11, 1), date(2024, 4, 15))
    assert w == [(date(2023, 11, 1), date(2023, 12, 31)), (date(2024, 1, 1), date(2024, 3, 31)),
                 (date(2024, 4, 1), date(2024, 4, 15))]


def test_market_listings_cache_and_resume(tmp_path, no_sleep):
    page = lambda n: FakeResponse(payload={"status": "000", "total_page": 2, "list": [{"rcept_no": str(n)}]})
    session = FakeSession([page(1), page(2)])
    collector = DartCollector(client(session, no_sleep), raw_dir=tmp_path, api_key=KEY)
    assert collector.collect_market_listings(date(2024, 1, 1), date(2024, 3, 31), "F") == 2
    files = sorted((tmp_path / "market").glob("*.json"))
    assert [f.name for f in files] == ["F_20240101_20240331_p1.json", "F_20240101_20240331_p2.json"]
    assert KEY not in files[0].read_text(encoding="utf-8")

    # Interrupted after page 1: page 2 missing -> only page 2 is requested again.
    files[1].unlink()
    session.responses = [page(2)]
    assert collector.collect_market_listings(date(2024, 1, 1), date(2024, 3, 31), "F") == 1
    assert len(session.calls) == 3


def test_client_error_includes_masked_body(no_sleep, monkeypatch):
    monkeypatch.setenv("NAVER_CLIENT_SECRET", "SECRETVAL1")
    body = b'{"errorMessage":"Authentication failed","errorCode":"024","echo":"SECRETVAL1"}'
    session = FakeSession([FakeResponse(401, body)])
    with pytest.raises(CollectorError) as e:
        client(session, no_sleep).get("https://openapi.naver.com/v1/search/news.json")
    assert "024" in str(e.value) and "SECRETVAL1" not in str(e.value)


def test_build_queries_topics_without_base_query():
    settings = {"news": {"include_base_query": False, "split_by_company": True,
                         "keywords": [{"keyword": "회계법인 규제", "category": "regulation_policy",
                                       "topics": ["감리", "제재"]}]}}
    mapping = {"companies": [{"company_id": "pwc", "search_aliases": ["삼일PwC", "삼일회계법인"]}]}
    qs = build_queries(settings, mapping)
    assert [q.query for q in qs] == ["삼일PwC 감리", "삼일PwC 제재", "삼일회계법인 감리", "삼일회계법인 제재"]
    assert {q.company_id for q in qs} == {"pwc"} and {q.keyword for q in qs} == {"회계법인 규제"}


def test_news_coverage_start():
    from verify_news_gate import coverage_start
    from src.collectors.news_collector import QueryReport

    def rep(oldest, truncated, reached):
        return QueryReport("q", "k", 5000, 2, "2026-10-01", oldest, 1099, truncated, reached, "now")

    start = date(2023, 11, 1)
    assert coverage_start([rep("2020-01-01", True, True), rep("2010-01-01", False, True)], start) == start
    assert coverage_start([rep("2025-06-01", True, False), rep("2026-02-10", True, False)], start) == date(2026, 2, 10)


def test_topic_completeness():
    from verify_news_gate import topic_completeness
    from src.collectors.news_collector import QueryReport

    def pair(alias, topic, truncated, reached, oldest="2020-01-01"):
        q = SearchQuery(f"{alias} {topic}", "회계법인 규제", "regulation_policy", "pwc", topic)
        return q, QueryReport(q.query, q.keyword, 5000, 2, "2026-10-01", oldest, 1099, truncated, reached, "now")

    rows = topic_completeness([
        pair("삼일PwC", "감리", False, True), pair("삼일회계법인", "감리", True, True),
        pair("삼일PwC", "AI 감사", True, False, "2025-06-01"), pair("삼일회계법인", "AI 감사", False, True),
    ])
    by = {r["topic"]: r for r in rows}
    assert (by["감리"]["complete"], by["감리"]["total"], by["감리"]["cut"]) == (2, 2, [])
    assert (by["AI 감사"]["complete"], by["AI 감사"]["cut"]) == (1, ["삼일PwC(2025-06-01)"])


def test_news_collect_reaches_the_deepest_page(tmp_path, no_sleep):
    # display=2, max_start=5: pages start at 1, 3, then 5 (not 7). Reachable items 1..6.
    page = lambda: FakeResponse(payload={"total": 100, "items": [_item("Mon, 02 Sep 2024 09:00:00 +0900")] * 2})
    session = FakeSession([page(), page(), page()])
    collector = NewsCollector(client(session, no_sleep), display=2, max_start=5,
                              raw_dir=tmp_path, credentials=("id", "secret"))
    report = collector.collect(SearchQuery("q", "kw", "c"), date(2023, 11, 1))
    assert [c["params"]["start"] for c in session.calls] == [1, 3, 5]
    assert report.fetched == 6


def test_news_collect_overlapping_last_page_is_not_double_counted(tmp_path, no_sleep):
    # display=100, max_start=1000: pages 1..901 then 1000 (overlaps item 1000 once).
    page = lambda: FakeResponse(payload={"total": 5000, "items": [_item("Mon, 02 Sep 2024 09:00:00 +0900")] * 100})
    session = FakeSession([page() for _ in range(11)])
    collector = NewsCollector(client(session, no_sleep), raw_dir=tmp_path, credentials=("id", "secret"))
    report = collector.collect(SearchQuery("q", "kw", "c"), date(2023, 11, 1))
    assert [c["params"]["start"] for c in session.calls][-2:] == [901, 1000]
    assert report.fetched == 1099


def test_naver_news_is_disabled_by_config():
    from src.common.config import naver_news_enabled

    assert naver_news_enabled() is False  # 검색 API 특약 2.3: do not re-enable without a new decision


def test_news_scripts_refuse_when_disabled(capsys):
    import verify_news_gate

    assert verify_news_gate.main() == 3
    assert "news.enabled: false" in capsys.readouterr().err
