"""정제본 뉴스에서 4-2 판정 검수 표본을 뽑아 검수표(CSV)로 저장한다 (핵심 원칙 3: 표본 검수).

Usage: python scripts/make_news_review_sample.py [--per-category 25] [--seed 20261113]
       (먼저 python scripts/tag_news_firms.py)

입력: data/processed/news_refined.jsonl
출력: evaluation/news_review_sample.csv  — 엑셀에서 바로 열리도록 UTF-8 BOM
추출: 검색어 묶음(카테고리 4개)별 같은 수의 단순 무작위 추출. 시드 고정 → 다시 실행해도 같은 표본.
      카테고리 간 기사 수 차이가 커서(약 3.5배) 비례 추출이면 작은 카테고리 표본이 너무 적어진다.
      따라서 전체 정확도를 낼 때는 카테고리별 정확도를 기사 수로 가중한다 (evaluation/news_review_guide.md).
검수 칸(human_*)은 비워 두고 사람이 채운다. 이미 채운 파일은 --force 없이 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict

import _bootstrap  # noqa: F401

from src.common.config import PROCESSED_DIR, PROJECT_ROOT

IN = PROCESSED_DIR / "news_refined.jsonl"
OUT = PROJECT_ROOT / "evaluation" / "news_review_sample.csv"

# 검수 순서대로: 기사 → 관련성 → (자동 판정, 사람 판정, 판정 근거) × 3 → 참고 칸.
# 자동 판정 값을 사람 칸에 미리 채우지 않는다 (그대로 넘기면 정확도가 부풀려진다). 맞으면 "=" 를 입력한다.
COLUMNS = [
    "doc_id", "search_keyword", "published_at", "title", "content",
    "human_is_relevant",
    "company_scope", "human_company_scope", "scope_rule",
    "member_firm_ids", "human_member_firm_ids",
    "mention_role", "human_mention_role", "role_rule",
    "company_ids", "url", "reviewer", "note",
]
HUMAN_COLS = [c for c in COLUMNS if c.startswith("human_")]


def main() -> None:
    ap = argparse.ArgumentParser(description="뉴스 4-2 판정 검수 표본 추출")
    ap.add_argument("--per-category", type=int, default=25)
    ap.add_argument("--seed", type=int, default=20261113)
    ap.add_argument("--force", action="store_true", help="검수 내용이 있는 기존 파일도 덮어쓴다")
    args = ap.parse_args()

    if OUT.exists() and not args.force:
        with OUT.open(encoding="utf-8-sig") as f:
            if any(r.get(c) for r in csv.DictReader(f) for c in HUMAN_COLS):
                sys.exit(f"{OUT} 에 검수 내용이 있어 덮어쓰지 않습니다 (--force 로 강제).")

    by_kw: dict[str, list[dict]] = defaultdict(list)
    for line in IN.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        by_kw[r["search_keyword"]].append(r)

    rng = random.Random(args.seed)
    sample = []
    for kw in sorted(by_kw):
        rows = sorted(by_kw[kw], key=lambda r: r["doc_id"])  # 파일 순서와 무관하게 같은 표본
        sample += rng.sample(rows, min(args.per_category, len(rows)))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in sample:
            row = {c: r.get(c, "") for c in COLUMNS if c not in HUMAN_COLS}
            row["company_ids"] = ",".join(r["company_ids"])
            row["member_firm_ids"] = ",".join(r["member_firm_ids"])
            w.writerow(row)

    print(f"저장: {OUT} ({len(sample)}건, seed={args.seed})")
    for kw in sorted(by_kw):
        n = sum(r["search_keyword"] == kw for r in sample)
        print(f"  {kw}: 표본 {n} / 모집단 {len(by_kw[kw]):,}")


if __name__ == "__main__":
    main()
