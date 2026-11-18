from __future__ import annotations

import json
from datetime import date

from src.common.config import Period
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.data_processor import (
    assign_dup_groups,
    build_dart_docs,
    build_news_docs,
    extract_dart_text,
)
from src.preprocessing.text_cleaning import clean_text, clean_title, content_hash
from tests.conftest import make_zip

MAPPING = {
    "context_words": ["회계법인", "감사"],
    "companies": [
        {"company_id": "pwc", "aliases": ["삼일회계법인", "PwC"], "weak_aliases": ["삼일"],
         "kr_entities": [{"name": "삼일회계법인", "corp_code": "00000001", "verified": True}]},
        {"company_id": "ey", "aliases": ["EY한영"], "weak_aliases": ["EY"], "kr_entities": []},
    ],
}


def test_clean_text_and_title():
    assert clean_title("[속보] <b>삼일PwC</b>, AI &quot;감사&quot; 도입") == "삼일PwC, AI \"감사\" 도입"
    assert clean_text("홍길동 기자 = 내용 hong@news.com  입니다") == "내용 입니다"
    assert clean_text("삼일PwC 대표 기자간담회 개최") == "삼일PwC 대표 기자간담회 개최"
    assert content_hash("A  b", "c") == content_hash("a b", "C")


def test_company_matcher_weak_alias_needs_context():
    m = CompanyMatcher(MAPPING)
    assert m.match("삼일절 기념 행사") == []
    assert m.match("삼일 회계법인 감사 품질") == ["pwc"]
    assert m.match("KEY 기술 발표") == []
    assert m.match("EY 회계법인 신규 서비스") == ["ey"]
    assert m.match("PwC와 EY한영 비교") == ["ey", "pwc"]


def test_company_matcher_spaced_dart_names():
    m = CompanyMatcher(MAPPING)
    assert m.match("감사인 삼 일 회 계 법 인 대표이사") == ["pwc"]
    assert m.match("삼  일회계 법인") == ["pwc"]
    assert m.match("감사 기간은 삼 일 이다") == []  # short weak alias stays unspaced-only


def _write_news(raw, name, items, keyword="회계법인 AI"):
    path = raw / "abc" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"query": {"query": keyword, "keyword": keyword, "category": "technology_trend"},
              "start": 1, "response": {"items": items}}
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def test_build_news_docs_filters_period_and_dedupes_urls(tmp_path):
    item = {"title": "<b>삼일PwC</b> AI 감사", "description": "회계법인 AI", "originallink":
            "https://www.news.co.kr/a", "link": "https://n.news.naver.com/a", "pubDate": "Mon, 02 Sep 2024 09:00:00 +0900"}
    old = {**item, "originallink": "https://www.news.co.kr/old", "pubDate": "Mon, 02 Jan 2023 09:00:00 +0900"}
    _write_news(tmp_path, "1.json", [item, old])
    _write_news(tmp_path, "2.json", [item])
    docs = build_news_docs(tmp_path, Period(date(2023, 11, 1), date(2026, 10, 31)), CompanyMatcher(MAPPING))
    assert len(docs) == 1
    d = docs[0]
    assert d["doc_id"].startswith("NEWS_") and d["source_name"] == "news.co.kr"
    assert d["company_ids"] == ["pwc"] and d["published_at"] == "2024-09-02"


def test_assign_dup_groups_within_window():
    docs = [
        {"doc_id": "a", "published_at": "2024-01-01", "title": "삼일PwC, 생성형 AI 감사 플랫폼 출시"},
        {"doc_id": "b", "published_at": "2024-01-02", "title": "삼일PwC 생성형 AI 감사플랫폼 출시"},
        {"doc_id": "c", "published_at": "2024-01-20", "title": "삼일PwC, 생성형 AI 감사 플랫폼 출시"},
        {"doc_id": "d", "published_at": "2024-01-02", "title": "삼정KPMG 신임 대표 선임"},
    ]
    groups = assign_dup_groups(docs)
    assert groups["b"] == "a"
    assert groups["c"] == "c"  # outside the 3-day window
    assert groups["d"] == "d"


def test_extract_dart_text_keeps_sections():
    xml = "<DOCUMENT><TITLE ATOC='Y'>1. 회사의 개요</TITLE><P>본문&amp;내용</P><TABLE><TR><TD>A</TD><TD>B</TD></TR></TABLE></DOCUMENT>"
    assert extract_dart_text(xml) == "## 1. 회사의 개요\n본문&내용\nA B"


def test_build_dart_docs(tmp_path):
    (tmp_path / "list").mkdir()
    listing = {"request": {"corp_code": "00000001", "pblntf_ty": "F"},
               "response": {"status": "000", "list": [
                   {"rcept_no": "20240101000001", "rcept_dt": "20240101", "report_nm": " 감사보고서 ",
                    "corp_code": "00000001", "flr_nm": "삼일회계법인"},
                   {"rcept_no": "20240201000002", "rcept_dt": "20240201", "report_nm": "기타",
                    "corp_code": "00000001", "flr_nm": "삼일회계법인"}]}}
    (tmp_path / "list" / "x.json").write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "documents").mkdir()
    xml = "<TITLE>개요</TITLE><P>내용</P>".encode("cp949")
    (tmp_path / "documents" / "20240101000001.zip").write_bytes(make_zip({"20240101000001.xml": xml}))

    docs, stats = build_dart_docs(tmp_path, MAPPING)
    assert stats == {"filings": 2, "with_text": 1, "extract_failed": 0, "no_document": 1}
    d = {x["rcept_no"]: x for x in docs}["20240101000001"]
    assert d["doc_id"] == "DART_20240101000001" and d["title"] == "감사보고서"
    assert d["url"].endswith("rcpNo=20240101000001")
    assert d["pblntf_ty"] == "F" and d["company_ids"] == ["pwc"]
    assert d["content"] == "## 개요\n내용"


def test_build_audit_report_docs_keeps_big4_filed_and_counts_all(tmp_path):
    from src.preprocessing.data_processor import build_audit_report_docs

    (tmp_path / "market").mkdir()
    listing = {"request": {"pblntf_ty": "F"}, "response": {"status": "000", "list": [
        {"rcept_no": "20240320000001", "rcept_dt": "20240320", "report_nm": "감사보고서 (2023.12)",
         "corp_code": "00999999", "corp_name": "고객사", "flr_nm": "삼일회계법인"},
        {"rcept_no": "20240320000002", "rcept_dt": "20240320", "report_nm": "감사보고서 (2023.12)",
         "corp_code": "00888888", "corp_name": "다른고객", "flr_nm": "중소회계법인"},
    ]}}
    for name in ("F_a_p1.json", "F_a_p2.json"):  # same filing in two pages counts once
        (tmp_path / "market" / name).write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")

    docs, monthly = build_audit_report_docs(tmp_path, MAPPING)
    assert [(d["doc_id"], d["company_ids"], d["corp_code"], d["content"]) for d in docs] == [
        ("DART_20240320000001", ["pwc"], "00999999", "")]
    assert monthly == [
        {"month": "2024-03", "pblntf_ty": "F", "filer": "other", "count": 1},
        {"month": "2024-03", "pblntf_ty": "F", "filer": "pwc", "count": 1},
    ]


def test_select_audit_originals_filters_and_is_reproducible(tmp_path):
    from src.preprocessing.data_processor import select_audit_originals

    (tmp_path / "market").mkdir()
    items = [
        # listed client, both separate and consolidated -> one row, consolidated preferred
        {"rcept_no": "1", "rcept_dt": "20260310", "report_nm": "감사보고서 (2025.12)", "corp_code": "A",
         "corp_cls": "Y", "flr_nm": "삼일회계법인"},
        {"rcept_no": "2", "rcept_dt": "20260310", "report_nm": "연결감사보고서 (2025.12)", "corp_code": "A",
         "corp_cls": "Y", "flr_nm": "삼일회계법인"},
        {"rcept_no": "3", "rcept_dt": "20260311", "report_nm": "감사보고서 (2025.12)", "corp_code": "B",
         "corp_cls": "E", "flr_nm": "삼일회계법인"},  # unlisted -> out
        {"rcept_no": "4", "rcept_dt": "20260410", "report_nm": "감사보고서 (2025.12)", "corp_code": "C",
         "corp_cls": "K", "flr_nm": "삼일회계법인"},  # April -> out
        {"rcept_no": "5", "rcept_dt": "20260312", "report_nm": "회계법인사업보고서", "corp_code": "00260295",
         "corp_cls": "E", "flr_nm": "삼일회계법인"},  # firm's own -> out
        {"rcept_no": "6", "rcept_dt": "20260312", "report_nm": "감사보고서 (2025.12)", "corp_code": "D",
         "corp_cls": "K", "flr_nm": "중소회계법인"},  # non-Big4 -> out
    ]
    listing = {"request": {"pblntf_ty": "F"}, "response": {"list": items}}
    (tmp_path / "market" / "F_p1.json").write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")

    args = dict(big4_corp_codes={"00260295"}, month="2026-03", corp_cls=["Y", "K"], per_firm=30, seed=42)
    first = select_audit_originals(tmp_path, MAPPING, **args)
    assert [(s["rcept_no"], s["company_id"]) for s in first] == [("2", "pwc")]
    assert first == select_audit_originals(tmp_path, MAPPING, **args)
    # No class filter: unlisted client B is included too.
    any_cls = select_audit_originals(tmp_path, MAPPING, **{**args, "corp_cls": []})
    assert sorted(s["rcept_no"] for s in any_cls) == ["2", "3"]


def test_select_audit_originals_excludes_corrections(tmp_path):
    from src.preprocessing.data_processor import select_audit_originals

    (tmp_path / "market").mkdir()
    items = [{"rcept_no": "9", "rcept_dt": "20260320", "report_nm": "[기재정정]감사보고서 (2020.12)",
              "corp_code": "Z", "corp_cls": "E", "flr_nm": "삼일회계법인"}]
    listing = {"request": {"pblntf_ty": "F"}, "response": {"list": items}}
    (tmp_path / "market" / "F_p1.json").write_text(json.dumps(listing, ensure_ascii=False), encoding="utf-8")
    args = dict(big4_corp_codes=set(), month="2026-03", corp_cls=[], per_firm=30, seed=42)
    assert select_audit_originals(tmp_path, MAPPING, **args) == []
    assert len(select_audit_originals(tmp_path, MAPPING, **args, exclude_corrections=False)) == 1


# ---------------------------------------------------------------- firm scope (guide 4-2)

def _tagger():
    from src.common.config import load_company_mapping
    from src.preprocessing.firm_scope import FirmScopeTagger
    return FirmScopeTagger(load_company_mapping())


def test_firm_scope_brand_only_is_not_korean_member():
    # 글로벌 브랜드 발표를 한국 회원 법인으로 연결하지 않는다
    r = _tagger().tag("딜로이트, 글로벌 CEO 설문 결과 발표", "딜로이트 글로벌 본사가 전 세계 CEO를 조사했다.")
    assert r["company_ids"] == ["deloitte"]
    assert r["member_firm_ids"] == []
    assert (r["company_scope"], r["region"]) == ("global_network", "GLOBAL")


def test_firm_scope_member_aliases_come_from_mapping():
    r = _tagger().tag("한영회계법인, 내부회계 자문 서비스 출시", "")
    assert r["member_firm_ids"] == ["ey_korea"]
    assert r["company_scope"] == "member_firm"


def test_firm_scope_mention_in_body_is_not_subject():
    # 기사 본문에 이름이 나왔을 뿐이면 그 회사의 행동(subject)으로 보지 않는다
    r = _tagger().tag("A사 IPO 추진", "이번 상장은 삼일PwC가 실사를 맡았다.")
    assert r["mention_role"] == "advisor"


def test_exclude_reason_drops_no_brand_and_unknown_scope():
    from src.preprocessing.firm_scope import exclude_reason
    t = _tagger()
    assert exclude_reason(t.tag("구글, 대만 태양광 투자", "")) == "no_brand"
    assert exclude_reason(t.tag("6월 2주차 ESG 핫클립", "PwC 리포트 요약")) == "scope_unknown"
    assert exclude_reason(t.tag("삼정KPMG, AI 감사 플랫폼 도입", "")) is None


def test_subject_found_in_body_when_firm_acts():
    # 제목에 이름이 없어도 요약에서 그 회사가 주어로 행동하면 subject
    r = _tagger().tag("AI 감사 시대 열린다", "삼일PwC는 13일 생성형 AI 감사 플랫폼을 출시했다고 밝혔다.")
    assert r["mention_role"] == "subject"


def test_second_pass_excludes_mention_only_career_and_scope_conflict():
    from src.preprocessing.firm_scope import exclude_reason
    t = _tagger()
    # 이름만 나열되고 회사의 행동이 없음
    assert exclude_reason(t.tag("회계사 시험 합격자 발표", "합격자들은 삼일PwC 등 대형 법인에 지원한다.")) == "mention_only"
    # 인물 경력 소개
    assert exclude_reason(t.tag("A사 신임 CFO 선임", "신임 CFO는 삼정KPMG 출신이다.")) == "career_bio"
    # 한국 법인 별칭 + 글로벌 본사 발표: 한국 법인 실적으로 연결하지 않는다
    assert exclude_reason(t.tag("AI 투자 확대", "EY한영은 EY 글로벌 본사의 AI 투자 계획을 소개했다.")) == "scope_conflict"


def test_third_pass_review_v1_cases():
    """검수 v1에서 관련 없음으로 판정된 유형 (evaluation/news_review_sample_v1.csv)."""
    from src.preprocessing.firm_scope import exclude_reason
    t = _tagger()
    # 경력: "거쳐", "경력", "사회생활을 시작"
    assert t.tag("보령 지배구조", "미시건대를 졸업한 그는 삼정KPMG를 거쳐 보령에 합류했다.")["mention_role"] == "career_bio"
    assert t.tag("감사위원 후보 평가", "백 후보는 딜로이트안진회계법인에서 기업자문 업무를 수행한 경력을 갖췄다.")["mention_role"] == "career_bio"
    assert t.tag("공동대표 인터뷰", "토론토대학교를 졸업한 그는 한영회계법인에서 사회생활을 시작했다.")["mention_role"] == "career_bio"
    # "경력직 채용"은 경력 소개가 아니다
    assert t.tag("채용 확대", "삼일PwC는 경력 회계사 100명을 채용한다고 밝혔다.")["mention_role"] != "career_bio"
    # 단어 일부 오탐: "토론토", "전국 무대" 는 행사가 아니다
    assert t.tag("공동대표", "토론토대 출신 대표는 EY한영 감사본부에 있었다.")["mention_role"] != "speaker_event"
    assert t.tag("영남대 수상", "최우수상(한영회계법인상)을 수상하며 전국 무대에서 입증했다.")["mention_role"] != "speaker_event"
    # 보고서 인용·행사 발표자는 정제본에서 뺀다
    assert exclude_reason(t.tag("반도체 시장 전망", "삼정KPMG가 발간한 보고서에 따르면 시장이 커진다.")) == "research_cited"
    assert exclude_reason(t.tag("ESG 인증 포럼 개최", "이진규 삼일회계법인 파트너 등이 포럼에 참여한다.")) == "speaker_event"
    # 주인공은 남긴다
    assert exclude_reason(t.tag("삼정KPMG, AI 감사 플랫폼 도입", "")) is None


def test_fourth_pass_sale_lead_advisor():
    """검수 v2: Big4가 매각 주관사로만 나오는 기사는 뺀다 (엑셀 22·32행 홈플러스 기사)."""
    from src.preprocessing.firm_scope import exclude_reason
    t = _tagger()
    assert exclude_reason(t.tag("홈플러스 매각", "매각주관사인 삼일회계법인은 투자안내서를 발송했다.")) == "sale_lead_advisor"
    assert exclude_reason(t.tag("해태htb 본입찰", "매각 주관사인 삼정KPMG 주도로 진행 중이다.")) == "sale_lead_advisor"
    assert exclude_reason(t.tag("삼일PwC, 홈플러스 매각 주관", "")) == "sale_lead_advisor"
    # 매각 주관이 다른 회사면 sale_lead_advisor 는 아니다 (자문사라 5차 정제의 advisor 로 빠진다)
    assert exclude_reason(t.tag("B사 인수", "실사는 EY한영이 맡았다. 한편 C사 매각 주관사는 삼성증권이다.")) == "advisor"


def test_fifth_pass_advisor_and_report_publication():
    """검수 v3: 자문사 기사 전체, Big4 보고서·조사 발간 기사는 뺀다. 감사보고서는 빼지 않는다."""
    from src.preprocessing.firm_scope import exclude_reason
    t = _tagger()
    assert exclude_reason(t.tag("A사 IPO", "상장 자문은 삼정KPMG가 맡았다.")) == "advisor"
    assert exclude_reason(t.tag("삼일PwC, 새정부 정책 분석 보고서 발간", "")) == "report_publication"
    assert exclude_reason(t.tag("한국 CEO 95% 선별 투자", "EY한영은 이러한 내용을 담은 CEO 아웃룩 설문조사 결과를 발표했다.")) in ("research_cited", "report_publication")
    assert exclude_reason(t.tag("삼정KPMG, '감사위원회 핸드북' 개정판 발간", "")) == "report_publication"
    assert exclude_reason(t.tag("삼일PwC, A사 감사보고서 의견거절", "")) is None
    assert exclude_reason(t.tag("딜로이트안진, 신임 대표 선임", "")) is None
