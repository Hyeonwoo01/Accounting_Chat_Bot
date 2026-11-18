"""Identify which Big4 brands a document mentions (guide 2-4).

A mention is not the same as the company acting: multi-company articles keep every
matched ID, and downstream per-company sentiment uses single-company docs only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Aliases at least this long (ignoring spaces) also match when DART spaces out every character,
# e.g. "삼 일 회 계 법 인" in audit report signatures. Shorter ones ("삼일") would match ordinary
# phrases such as "삼 일 만에", so they must appear unspaced.
SPACED_MATCH_MIN_LEN = 4


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Latin aliases need word boundaries ("EY" must not match "KEY"); Korean aliases do not,
    # because particles attach directly ("삼일회계법인은").
    escaped = re.escape(alias)
    if re.fullmatch(r"[A-Za-z&. ]+", alias):
        return re.compile(rf"(?<![A-Za-z]){escaped}(?![A-Za-z])", re.IGNORECASE if len(alias) > 3 else 0)
    chars = [c for c in alias if not c.isspace()]
    if len(chars) >= SPACED_MATCH_MIN_LEN:
        return re.compile(r"\s*".join(re.escape(c) for c in chars))
    return re.compile(escaped)


@dataclass
class _Company:
    company_id: str
    strong: list[re.Pattern[str]]
    weak: list[re.Pattern[str]]


class CompanyMatcher:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._context = [_alias_pattern(w) for w in mapping.get("context_words", [])]
        self._companies = [
            _Company(
                c["company_id"],
                [_alias_pattern(a) for a in c.get("aliases", [])],
                [_alias_pattern(a) for a in c.get("weak_aliases", [])],
            )
            for c in mapping["companies"]
        ]

    def match(self, text: str) -> list[str]:
        has_context = any(p.search(text) for p in self._context)
        found = []
        for c in self._companies:
            if any(p.search(text) for p in c.strong) or (has_context and any(p.search(text) for p in c.weak)):
                found.append(c.company_id)
        return sorted(found)


def filer_identification(docs: list[dict], matcher: CompanyMatcher, big4_corp_codes: set[str]) -> dict:
    """On client audit reports with text: how often is the filing firm found in the text itself?

    The filer comes from DART metadata (flr_nm), so it is a free ground truth for the matcher.
    Returns counts plus up to 10 misses for manual inspection.
    """
    checked = found = 0
    misses = []
    for d in docs:
        if not d.get("content", "").strip() or d.get("corp_code") in big4_corp_codes:
            continue
        checked += 1
        filer = d["company_ids"][0] if d["company_ids"] else None
        if filer and filer in matcher.match(d["content"]):
            found += 1
        elif len(misses) < 10:
            misses.append({"rcept_no": d.get("rcept_no"), "title": d["title"], "filer": filer})
    return {"checked": checked, "found": found, "rate": round(found / checked, 3) if checked else None,
            "misses": misses}
