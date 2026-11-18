"""Regex-based text cleaning (guide 5-1). Each pattern documents what it removes."""

from __future__ import annotations

import hashlib
import html
import re

# <b>검색어</b> 등 인라인 HTML 태그 (공백 없이 제거해야 "삼일PwC ," 같은 잔여 공백이 생기지 않음)
_HTML_TAG = re.compile(r"<[^>]+>")
# 기사 머리말: [속보] [단독] (종합) 【특징주】 등 — 제목 맨 앞에 붙은 괄호 표기
_HEADLINE_PREFIX = re.compile(r"^\s*(?:[\[【(〔<]\s*(?:속보|단독|종합|특징주|포토|영상|인터뷰|사진|기획|칼럼|사설|이슈)[^\]】)〕>]*[\]】)〕>]\s*)+")
# 이메일 주소
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# 기자명 표기: "홍길동 기자", "(서울=연합뉴스) 홍길동 기자 =" 등. "기자간담회"·"기자회견"은 제외
_REPORTER = re.compile(r"(?:\([^)]*=[^)]*\)\s*)?[가-힣]{2,4}\s?(?:기자|특파원)(?![가-힣])(?:\s*=)?")
# 한글·영문·숫자와 기본 문장부호 외의 특수문자 (%, ·, ~ 등 의미 있는 기호는 유지)
_SPECIAL = re.compile(r"[^\w\s.,!?%·~()\-'\"/&+]")
_WHITESPACE = re.compile(r"\s+")


def clean_text(text: str | None) -> str:
    if not text:
        return ""
    text = html.unescape(_HTML_TAG.sub("", text))  # tags first, then &quot; &amp; ...
    text = _EMAIL.sub(" ", text)
    text = _REPORTER.sub(" ", text)
    text = _SPECIAL.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def clean_title(title: str | None) -> str:
    if not title:
        return ""
    title = html.unescape(_HTML_TAG.sub("", title))
    title = _HEADLINE_PREFIX.sub("", title)
    title = _SPECIAL.sub(" ", title)
    return _WHITESPACE.sub(" ", title).strip()


def normalize_for_compare(text: str) -> str:
    """Lowercase, keep only letters/digits — used for hashing and title similarity."""
    return re.sub(r"[^0-9a-z가-힣]", "", text.lower())


def content_hash(*parts: str) -> str:
    joined = "\x1f".join(normalize_for_compare(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
