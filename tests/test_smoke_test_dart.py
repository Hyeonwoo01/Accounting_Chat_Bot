"""The smoke test's own logic, exercised with fake HTTP so a failure there means a real API change."""

from __future__ import annotations

from datetime import date

from smoke_test_dart import run_checks
from src.collectors.dart_collector import DartCollector
from src.collectors.http_client import RateLimitedClient
from tests.conftest import FakeResponse, FakeSession, make_zip

MAPPING = {"context_words": ["회계법인"], "companies": [
    {"company_id": "pwc", "aliases": ["삼일회계법인"], "weak_aliases": [], "kr_entities": [{"name": "삼일회계법인"}]},
]}
WINDOW = (date(2024, 8, 1), date(2024, 8, 14))
LISTING = {"status": "000", "total_count": 3, "total_page": 1, "list": [
    {"rcept_no": "20240801000000", "rcept_dt": "20240801", "report_nm": "회계법인사업보고서",
     "corp_code": "00260295", "flr_nm": "삼일회계법인"},
    {"rcept_no": "20240801000001", "rcept_dt": "20240801", "report_nm": "감사보고서 (2024.06)",
     "corp_code": "00999999", "flr_nm": "삼일회계법인"},
    {"rcept_no": "20240801000002", "rcept_dt": "20240801", "report_nm": "감사보고서 (2024.06)",
     "corp_code": "00888888", "flr_nm": "중소회계법인"},
]}


def _collector(responses, tmp_path, no_sleep):
    session = FakeSession(responses)
    return DartCollector(RateLimitedClient(0, session=session, sleep=no_sleep), raw_dir=tmp_path, api_key="k")


def test_all_checks_pass_on_expected_responses(tmp_path, no_sleep):
    doc = make_zip({"20240801000001.xml": "<P>감사인 삼 일 회 계 법 인 대표이사</P>"})
    collector = _collector([FakeResponse(payload=LISTING), FakeResponse(payload=LISTING), FakeResponse(200, doc)],
                           tmp_path, no_sleep)
    results = run_checks(collector, tmp_path, MAPPING, WINDOW, big4_corp_codes={"00260295"})
    assert [ok for _, ok, _ in results] == [True] * 6, results
    assert "20240801000001" in results[3][2]  # the client report, not the firm's own


def test_reports_failure_when_filer_name_missing(tmp_path, no_sleep):
    doc = make_zip({"20240801000001.xml": "<P>감사인 서명 없음</P>"})
    collector = _collector([FakeResponse(payload=LISTING), FakeResponse(payload=LISTING), FakeResponse(200, doc)],
                           tmp_path, no_sleep)
    checks = run_checks(collector, tmp_path, MAPPING, WINDOW, big4_corp_codes={"00260295"})
    results = {name[:2]: ok for name, ok, _ in checks}
    assert results["5."] is False and results["6."] is True
