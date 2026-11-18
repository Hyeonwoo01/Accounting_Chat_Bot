from __future__ import annotations

from datetime import date
from pathlib import Path

from src.collectors.dart_collector import DartCollector, DocumentResult
from src.collectors.http_client import RateLimitedClient
from src.common.dart import report_type
from src.preprocessing.company_matcher import CompanyMatcher
from tests.conftest import FakeResponse, FakeSession, make_zip
from verify_dart_market import (
    TypeScan,
    WindowScan,
    check_documents,
    filer_company,
    full_collection_calls,
    is_keyword_hit,
    pick_samples,
    project,
    scan,
    write_report,
)

FILERS = {"삼일회계법인": "pwc", "삼정회계법인": "kpmg"}
CFG = {
    "sample_windows": [["2026-03-01", "2026-03-10"]],
    "max_pages_per_type": 5,
    "filer_types": ["F"],
    "keyword_types": ["I"],
    "report_keywords": ["감사인"],
    "sample_documents": 3,
    "daily_call_limit": 20000,
}
MAPPING = {"context_words": ["회계법인"], "companies": [
    {"company_id": "pwc", "aliases": ["삼일회계법인"], "weak_aliases": [], "kr_entities": [{"name": "삼일회계법인"}]},
    {"company_id": "kpmg", "aliases": ["삼정회계법인"], "weak_aliases": [], "kr_entities": [{"name": "삼정회계법인"}]},
]}


def test_report_type_drops_correction_tag_and_period():
    assert report_type("[기재정정]감사보고서 (2023.12)") == "감사보고서"
    assert report_type("[첨부추가] 사업보고서") == "사업보고서"


def test_filer_and_keyword_matching():
    assert filer_company("삼일회계법인", FILERS) == "pwc"
    assert filer_company("삼일제약", FILERS) is None
    assert is_keyword_hit("외부감사인선임", ["감사인"])
    assert not is_keyword_hit("감사보고서", ["감사인"])


def test_projection_and_call_estimate():
    w = WindowScan(date(2026, 3, 1), date(2026, 3, 10))  # 10 days
    w.types["F"] = TypeScan("F", total_count=1000, total_page=10, scanned=100,
                            hits=[{}] * 5)  # 5% of 1000 = 50 in 10 days
    assert project([w], "F", period_days=100) == 500
    assert full_collection_calls([w], period_days=100) == 100


def test_list_page_has_no_corp_code(no_sleep):
    session = FakeSession([FakeResponse(payload={"status": "000", "total_count": 0, "total_page": 0, "list": []})])
    collector = DartCollector(RateLimitedClient(0, session=session, sleep=no_sleep), api_key="k")
    collector.list_page(date(2026, 3, 1), date(2026, 5, 31), pblntf_ty="F")
    params = session.calls[0]["params"]
    assert "corp_code" not in params and params["pblntf_ty"] == "F" and params["bgn_de"] == "20260301"


class FakeCollector:
    def __init__(self, pages: dict[str, list[dict]], tmp: Path) -> None:
        self.pages = pages
        self.tmp = tmp

    def list_page(self, start, end, pblntf_ty=None, page=1, page_count=100):
        items = self.pages[pblntf_ty]
        return {"status": "000", "total_count": len(items) * 10, "total_page": 1, "list": items}

    def download_document(self, rcept_no):
        path = self.tmp / f"{rcept_no}.zip"
        path.write_bytes(make_zip({f"{rcept_no}.xml": "<P>신규 감사인은 삼정회계법인이다</P>"}))
        return DocumentResult(rcept_no, ok=True, path=path)


def test_scan_and_report_end_to_end(tmp_path):
    pages = {
        "F": [{"rcept_no": "1", "report_nm": "감사보고서 (2025.12)", "flr_nm": "삼일회계법인", "corp_name": "가"},
              {"rcept_no": "2", "report_nm": "감사보고서 (2025.12)", "flr_nm": "다른회계법인", "corp_name": "나"}],
        "I": [{"rcept_no": "3", "report_nm": "외부감사인선임", "flr_nm": "다", "corp_name": "다"},
              {"rcept_no": "4", "report_nm": "주주총회소집결의", "flr_nm": "라", "corp_name": "라"}],
    }
    collector = FakeCollector(pages, tmp_path)
    windows = scan(collector, CFG, FILERS)
    assert [len(windows[0].types[t].hits) for t in ("F", "I")] == [1, 1]

    samples = pick_samples(windows, CFG)
    assert {s["rcept_no"] for s in samples} == {"1", "3"}
    docs = check_documents(collector, samples, CompanyMatcher(MAPPING))
    assert {d["rcept_no"]: d["big4"] for d in docs}["3"] == ["kpmg"]

    report = write_report(windows, docs, CFG, period_days=1096)
    assert "외부감사인선임" in report and "추정 판정" in report
    assert "| 관련 법인 수 (표본) | 2 |" in report  # pwc as filer + kpmg mentioned
