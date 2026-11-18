"""Consistency checks on the refined news set, and exact-duplicate marking."""

from __future__ import annotations

from datetime import date

from check_news_refined import check
from src.common.config import load_company_mapping
from src.preprocessing.firm_scope import FirmScopeTagger, exclude_reason
from tag_news_firms import mark_exact_duplicates

START, END = date(2023, 11, 1), date(2026, 10, 31)


def _row(doc_id, title, content="", published="2025-01-10", url=None):
    tagged = FirmScopeTagger(load_company_mapping()).tag(title, content)
    return {"doc_id": doc_id, "published_at": published, "search_keyword": "회계법인 AI", "title": title,
            "content": content, "url": url or f"https://x.example.com/{doc_id}", **tagged,
            "exclude_reason": exclude_reason(tagged)}


def test_clean_row_has_no_issues():
    assert check([_row("NEWS_1", "삼일PwC, AI 감사 플랫폼 출시")], load_company_mapping(), START, END) == {}


def test_check_finds_contradictions_and_duplicates():
    good = _row("NEWS_1", "삼일PwC, AI 감사 플랫폼 출시")
    dup = _row("NEWS_2", "삼일PwC, AI 감사 플랫폼 출시")                      # 완전 중복
    bad = _row("NEWS_3", "삼정KPMG, 신규 서비스 출시", published="2020-01-01")   # 기간 밖
    bad["region"] = "GLOBAL"                                                   # 범위와 지역 불일치
    bad["member_firm_ids"] = ["ey_korea"]                                      # 기사에 없는 법인
    issues = check([good, dup, bad], load_company_mapping(), START, END)
    assert issues["완전 중복 (제목+요약 동일, URL만 다름)"] == ["NEWS_2"]
    assert issues["기간 밖 기사"] == ["NEWS_3"]
    assert issues["region 이 company_scope 와 불일치"] == ["NEWS_3"]
    assert issues["회원 법인 별칭이 기사에 없음 (브랜드명만으로 연결)"] == ["NEWS_3"]


def test_mark_exact_duplicates_keeps_earliest():
    rows = [_row("NEWS_B", "삼일PwC, AI 감사 플랫폼 출시", published="2025-01-11"),
            _row("NEWS_A", "삼일PwC, AI 감사 플랫폼 출시", published="2025-01-10"),
            _row("NEWS_C", "삼정KPMG, 신규 서비스 출시")]
    mark_exact_duplicates(rows)
    assert [r["exclude_reason"] for r in rows] == ["exact_duplicate", None, None]
    assert rows[0]["duplicate_of"] == "NEWS_A"
