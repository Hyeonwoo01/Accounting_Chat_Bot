"""최종 정제본 뉴스의 정합성 검사 (기사 내용을 해석하지 않는 기계 검사).

Usage: python scripts/check_news_refined.py       (먼저 python scripts/tag_news_firms.py)

판정이 "맞는지"(정확도)는 사람 검수로만 잰다 (evaluation/news_review_guide.md).
여기서는 판정끼리 모순되거나, 규칙대로 되지 않았거나, 중복·기간·필수값 문제가 있는 행을 찾는다.
입력: data/processed/news_refined.jsonl / 출력: evaluation/news_refined_check.md (+ 화면). 0건이어야 통과.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime

import _bootstrap  # noqa: F401

from src.common.config import PROCESSED_DIR, PROJECT_ROOT, get_period, load_company_mapping
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.text_cleaning import content_hash

IN = PROCESSED_DIR / "news_refined.jsonl"
OUT = PROJECT_ROOT / "evaluation" / "news_refined_check.md"

ALLOWED_SCOPE = {"member_firm", "global_network", "industry_wide"}
ALLOWED_ROLE = {"subject", "advisor", "speaker_event", "research_cited"}
REGION_OF = {"member_firm": "KR", "global_network": "GLOBAL", "industry_wide": "unknown"}
REQUIRED = ("doc_id", "published_at", "search_keyword", "title", "url")


def check(rows: list[dict], mapping: dict, period_start: date, period_end: date) -> dict[str, list[str]]:
    """{검사 이름: 걸린 doc_id 목록}."""
    matcher = CompanyMatcher(mapping)
    member_aliases = {m["member_firm_id"]: (c["company_id"], m.get("aliases", []))
                      for c in mapping["companies"] for m in c.get("member_firms", [])}
    issues: dict[str, list[str]] = defaultdict(list)
    seen_id, seen_url, seen_hash = set(), set(), {}

    for r in rows:
        d = r.get("doc_id", "?")
        text = f"{r.get('title', '')} {r.get('content', '')}"

        # 필수값·허용값
        if any(not r.get(k) for k in REQUIRED):
            issues["필수값 누락"].append(d)
        if r.get("exclude_reason"):
            issues["제외 사유가 있는데 정제본에 남음"].append(d)
        if r.get("company_scope") not in ALLOWED_SCOPE:
            issues["정제본에 허용되지 않는 company_scope"].append(d)
        if r.get("mention_role") not in ALLOWED_ROLE:
            issues["정제본에 허용되지 않는 mention_role"].append(d)

        # 기간·중복
        try:
            if not (period_start <= date.fromisoformat(r["published_at"]) <= period_end):
                issues["기간 밖 기사"].append(d)
        except (KeyError, ValueError):
            issues["게시일 형식 오류"].append(d)
        if d in seen_id:
            issues["doc_id 중복"].append(d)
        if r.get("url") in seen_url:
            issues["URL 중복"].append(d)
        seen_id.add(d)
        seen_url.add(r.get("url"))
        h = content_hash(r.get("title", ""), r.get("content", ""))
        if h in seen_hash:
            issues["완전 중복 (제목+요약 동일, URL만 다름)"].append(d)
        seen_hash.setdefault(h, d)

        # 판정 재현: 저장된 값이 지금 규칙·매핑으로 다시 계산한 값과 같은가
        if sorted(matcher.match(text)) != sorted(r.get("company_ids", [])):
            issues["company_ids 가 텍스트와 불일치"].append(d)
        if not r.get("company_ids"):
            issues["company_ids 비어 있음"].append(d)

        # 4-2 판정끼리의 모순
        scope, members = r.get("company_scope"), r.get("member_firm_ids", [])
        if r.get("region") != REGION_OF.get(scope):
            issues["region 이 company_scope 와 불일치"].append(d)
        if scope == "member_firm" and not members:
            issues["member_firm 인데 member_firm_ids 없음"].append(d)
        for mid in members:
            cid, aliases = member_aliases.get(mid, (None, []))
            if cid not in r.get("company_ids", []):
                issues["회원 법인의 브랜드가 company_ids 에 없음"].append(d)
            if not any(a in text for a in aliases):
                issues["회원 법인 별칭이 기사에 없음 (브랜드명만으로 연결)"].append(d)
        if r.get("scope_conflict"):
            issues["scope_conflict 인데 남음"].append(d)
        if r.get("mention_role") == "subject":
            in_title = bool(matcher.match(r.get("title", "")))
            if not in_title and not str(r.get("role_rule", "")).startswith("요약에서"):
                issues["subject 인데 제목·요약 주어 근거 없음"].append(d)
            if set(r.get("subject_company_ids", [])) - set(r.get("company_ids", [])):
                issues["subject_company_ids 가 company_ids 밖"].append(d)

        # 판정 근거 칸: 검수 때 근거를 볼 수 있어야 한다
        if not r.get("scope_rule") or not r.get("role_rule"):
            issues["판정 근거 비어 있음"].append(d)
        if re.search(r"<[^>]+>|&quot;|&amp;", text):
            issues["HTML 태그·엔티티 잔존"].append(d)
    return dict(issues)


def to_markdown(n_rows: int, issues: dict[str, list[str]]) -> str:
    lines = ["# 최종 정제본 정합성 검사", "",
             f"- 실행 일시: {datetime.now():%Y-%m-%d %H:%M}",
             f"- 대상: `data/processed/news_refined.jsonl` {n_rows:,}건",
             "- 기사 내용을 해석하지 않는 기계 검사다. 판정 정확도는 사람 검수로 잰다.", ""]
    if not issues:
        lines.append("**문제 0건 — 통과**")
    else:
        lines += ["| 검사 | 건수 | 예시 doc_id |", "|---|---|---|"]
        for name, ids in sorted(issues.items(), key=lambda x: -len(x[1])):
            lines.append(f"| {name} | {len(ids):,} | {', '.join(ids[:3])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = [json.loads(line) for line in IN.read_text(encoding="utf-8").splitlines()]
    period = get_period()
    issues = check(rows, load_company_mapping(), period.start, period.end)
    md = to_markdown(len(rows), issues)
    OUT.write_text(md, encoding="utf-8")
    print(md)
    print(f"저장: {OUT}")
    print(f"문제 있는 행(중복 제외): {len({d for ids in issues.values() for d in ids}):,} / {len(rows):,}")
    by_kw = Counter(r["search_keyword"] for r in rows)
    print(f"카테고리별: {dict(by_kw)}")


if __name__ == "__main__":
    main()
