"""DART constants and helpers shared by collectors, preprocessing, RAG and the dashboard."""

import re

DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"

PBLNTF_TYPES = {
    "A": "정기공시", "B": "주요사항보고", "C": "발행공시", "D": "지분공시", "E": "기타공시",
    "F": "외부감사관련", "G": "펀드공시", "H": "자산유동화", "I": "거래소공시", "J": "공정위공시",
}


def viewer_url(rcept_no: str) -> str:
    return DART_VIEWER_URL.format(rcept_no=rcept_no)


_BRACKET_TAG = re.compile(r"\[[^\]]*\]")         # [기재정정], [첨부추가] 등 정정 표시
_PERIOD_SUFFIX = re.compile(r"\(\d{4}\.\d{2}\)")  # (2023.12) 같은 대상 기간


def report_type(report_nm: str) -> str:
    """Filing kind without correction tags or period, so yearly repeats count as one type."""
    name = _PERIOD_SUFFIX.sub("", _BRACKET_TAG.sub("", report_nm))
    return re.sub(r"\s+", " ", name).strip()
