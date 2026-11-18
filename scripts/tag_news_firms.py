"""수집한 뉴스 전체에 가이드 4-2 법인 범위·언급 역할 1차 판정을 붙이고, 정제본을 따로 저장한다.

Usage: python scripts/tag_news_firms.py      (먼저 python scripts/collect_data.py --only news)

입력: data/raw/news/<검색어>/*.json (본 수집 원본). 기간 밖 기사는 제외 (build_news_docs 와 동일 기준)
출력: data/processed/news_firm_scope.jsonl  — 전체 기사 + 판정 + exclude_reason (뺀 이유, 남기면 null)
      data/processed/news_refined.jsonl     — exclude_reason 이 없는 기사만 (분석·검수 표본의 모집단)
      doc_id 는 DB documents 와 같은 URL 해시.
      DB 에는 아직 넣지 않는다: documents 에 company_scope 등 열을 추가하는 건 별도 스키마 PR (CLAUDE.md)
제외 기준: src/preprocessing/firm_scope.exclude_reason (no_brand, scope_unknown, scope_conflict, career_bio,
          mention_only, research_cited, speaker_event) + 남길 기사끼리의 완전 중복 (exact_duplicate, 가장 먼저 게시된 1건만 남김)
정합성 검사: python scripts/check_news_refined.py
모든 행은 규칙 기반 1차 판정이므로 needs_review=True. 정확도는 정제본 표본 검수로 잰다. LLM 은 쓰지 않는다.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import _bootstrap  # noqa: F401

from src.common.config import PROCESSED_DIR, RAW_NEWS_DIR, get_period, load_company_mapping
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.data_processor import build_news_docs
from src.preprocessing.firm_scope import FirmScopeTagger, exclude_reason
from src.preprocessing.text_cleaning import content_hash

OUT_ALL = PROCESSED_DIR / "news_firm_scope.jsonl"
OUT_REFINED = PROCESSED_DIR / "news_refined.jsonl"


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:  # 매번 전체를 다시 쓴다 → 재실행해도 중복 없음
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _summary(title: str, rows: list[dict]) -> None:
    print(f"\n[{title}] {len(rows):,}건")
    for key in ("search_keyword", "company_scope", "mention_role"):
        print(f"  {key}: {dict(Counter(r[key] for r in rows).most_common())}")
    print(f"  member_firm_ids: {dict(Counter(m for r in rows for m in r['member_firm_ids']).most_common())}")


def mark_exact_duplicates(rows: list[dict]) -> None:
    """남길 기사 중 제목+요약이 완전히 같은 것은 가장 먼저 게시된 1건만 남긴다 (전처리 규칙: 완전 중복은 한 번만).

    유사 제목(같은 보도자료의 다른 기사)은 지우지 않는다 — 보도량 자체가 분석 대상이다.
    """
    first: dict[str, str] = {}
    for r in sorted(rows, key=lambda r: (r["published_at"], r["doc_id"])):
        if r["exclude_reason"] is not None:
            continue
        h = content_hash(r["title"], r["content"])
        if h in first:
            r["exclude_reason"] = "exact_duplicate"
            r["duplicate_of"] = first[h]
        else:
            first[h] = r["doc_id"]


def main() -> None:
    mapping = load_company_mapping()
    docs = build_news_docs(RAW_NEWS_DIR, get_period(), CompanyMatcher(mapping))
    tagger = FirmScopeTagger(mapping)
    rows = []
    for d in docs:
        tagged = tagger.tag(d["title"], d["content"])
        rows.append({"doc_id": d["doc_id"], "published_at": d["published_at"],
                     "search_keyword": d["search_keyword"], "title": d["title"], "content": d["content"],
                     "url": d["url"], **tagged, "exclude_reason": exclude_reason(tagged)})
    mark_exact_duplicates(rows)
    refined = [r for r in rows if r["exclude_reason"] is None]
    _write(OUT_ALL, rows)
    _write(OUT_REFINED, refined)

    print(f"저장: {OUT_ALL} (전체 {len(rows):,}건, 기간 내·URL 중복 제거)")
    print(f"저장: {OUT_REFINED} (정제본 {len(refined):,}건)")
    print(f"제외: {dict(Counter(r['exclude_reason'] for r in rows if r['exclude_reason']))}")
    _summary("정제본", refined)


if __name__ == "__main__":
    main()
