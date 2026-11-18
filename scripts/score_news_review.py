"""사람이 채운 뉴스 검수표로 4-2 규칙 판정의 정확도를 계산한다 (evaluation/news_review_guide.md).

Usage: python scripts/score_news_review.py

입력: evaluation/news_review_sample.csv (human_* 칸을 채운 것)
      data/processed/news_refined.jsonl (카테고리별 모집단 크기 → 전체 값 가중치)
출력: evaluation/news_review_result.md  (+ 화면 출력)

- human_* 칸의 "=" 는 "자동 판정과 같다"는 뜻이다.
- human_is_relevant 가 빈 행은 아직 검수하지 않은 것으로 보고 계산에서 빼며, 건수를 따로 표시한다.
- 관련 없음(0) 행은 관련성에만 들어가고 범위·법인·역할 정확도에서는 빠진다.
- 카테고리마다 같은 수를 뽑았으므로 전체 값은 정제본 기사 수로 가중한다.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import datetime

import _bootstrap  # noqa: F401

from src.common.config import PROCESSED_DIR, PROJECT_ROOT

SAMPLE = PROJECT_ROOT / "evaluation" / "news_review_sample.csv"
REFINED = PROCESSED_DIR / "news_refined.jsonl"
OUT = PROJECT_ROOT / "evaluation" / "news_review_result.md"

FIELDS = [  # (자동 칸, 사람 칸, 표에 쓸 이름)
    ("company_scope", "human_company_scope", "company_scope"),
    ("member_firm_ids", "human_member_firm_ids", "member_firm_ids"),
    ("mention_role", "human_mention_role", "mention_role"),
]


def _ids(value: str) -> frozenset[str]:
    return frozenset(v.strip() for v in value.split(",") if v.strip())


def matches(field: str, auto: str, human: str) -> bool:
    human = human.strip()
    if human == "=":
        return True
    if field == "member_firm_ids":  # 순서·공백 무관, 집합으로 비교. 빈 값 = 해당 법인 없음
        return _ids(auto) == _ids(human)
    return auto.strip() == human


def score(rows: list[dict], population: dict[str, int]) -> dict:
    """카테고리별·전체(모집단 가중) 관련성과 항목별 정확도."""
    per_kw: dict[str, dict] = defaultdict(lambda: {"reviewed": 0, "relevant": 0, "unreviewed": 0,
                                                   "correct": Counter(), "answered": Counter()})
    for r in rows:
        s = per_kw[r["search_keyword"]]
        rel = r.get("human_is_relevant", "").strip()
        if rel == "":
            s["unreviewed"] += 1
            continue
        s["reviewed"] += 1
        if rel != "1":
            continue
        s["relevant"] += 1
        for auto_col, human_col, name in FIELDS:
            human = r.get(human_col, "")
            if human.strip() == "" and name != "member_firm_ids":
                continue  # 답을 안 적은 칸은 분모에서도 뺀다
            s["answered"][name] += 1
            s["correct"][name] += matches(name, r.get(auto_col, ""), human)

    def rate(num: int, den: int) -> float | None:
        return num / den if den else None

    result = {"categories": {}, "overall": {}}
    for kw, s in sorted(per_kw.items()):
        result["categories"][kw] = {
            "population": population.get(kw, 0), "reviewed": s["reviewed"], "unreviewed": s["unreviewed"],
            "relevance": rate(s["relevant"], s["reviewed"]),
            **{name: rate(s["correct"][name], s["answered"][name]) for *_, name in FIELDS},
            "n": {name: s["answered"][name] for *_, name in FIELDS},
        }
    for metric in ["relevance"] + [name for *_, name in FIELDS]:
        pairs = [(c["population"], c[metric]) for c in result["categories"].values() if c[metric] is not None]
        weight = sum(p for p, _ in pairs)
        result["overall"][metric] = sum(p * v for p, v in pairs) / weight if weight else None
    return result


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.0%}"


def to_markdown(result: dict) -> str:
    cats = result["categories"]
    lines = [
        "# 뉴스 법인 판정 검수 결과 (가이드 4-2)",
        "",
        f"- 계산 일시: {datetime.now():%Y-%m-%d %H:%M}",
        "- 검수표: `evaluation/news_review_sample.csv` / 기준: `evaluation/news_review_guide.md`",
        "- 판정: 규칙 기반 (`src/preprocessing/firm_scope.py`). 검수 범위: 제목·요약",
        f"- 검수 완료 {sum(c['reviewed'] for c in cats.values())}건 / "
        f"미검수 {sum(c['unreviewed'] for c in cats.values())}건",
        "",
        "| 카테고리(검색어 묶음) | 정제본 기사 수 | 검수 | 관련성 | company_scope | member_firm_ids | mention_role |",
        "|---|---|---|---|---|---|---|",
    ]
    for kw, c in cats.items():
        n = c["n"]
        lines.append(
            f"| {kw} | {c['population']:,} | {c['reviewed']} | {_fmt(c['relevance'])} | "
            f"{_fmt(c['company_scope'])} (n={n['company_scope']}) | "
            f"{_fmt(c['member_firm_ids'])} (n={n['member_firm_ids']}) | "
            f"{_fmt(c['mention_role'])} (n={n['mention_role']}) |"
        )
    o = result["overall"]
    lines += [
        f"| **전체 (기사 수 가중)** | {sum(c['population'] for c in cats.values()):,} | "
        f"{sum(c['reviewed'] for c in cats.values())} | {_fmt(o['relevance'])} | {_fmt(o['company_scope'])} | "
        f"{_fmt(o['member_firm_ids'])} | {_fmt(o['mention_role'])} |",
        "",
        "- 관련성 = 관련 있음(1) ÷ 검수 건수. 나머지 정확도의 분모(n)는 관련 있음이면서 답을 적은 건수.",
        "- 카테고리별 표본이 25건 안팎이라 오차가 크다. 수치는 표본 수와 함께 인용한다.",
        "- 정제본 기준이다. 정제에서 뺀 기사(no_brand, scope_unknown, scope_conflict, career_bio, mention_only, research_cited, speaker_event)는 이 수치에 들어 있지 않다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    with SAMPLE.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    population = Counter(json.loads(line)["search_keyword"]
                         for line in REFINED.read_text(encoding="utf-8").splitlines())
    md = to_markdown(score(rows, population))
    OUT.write_text(md, encoding="utf-8")
    print(md)
    print(f"저장: {OUT}")


if __name__ == "__main__":
    main()
