"""Turn the local spike notes into a title-free summary for the report and dashboard (guide 5-3).

Usage: python scripts/summarize_spike_notes.py   (after filling data/processed/eda/spike_notes.md)

Reads   data/processed/eda/spike_notes.md   (local only: contains news titles)
Writes  evaluation/spike_summary.md          type / leading firm / category / background per spike
                                              week + counts by type. NO article titles — safe to commit.
        data/processed/eda/spike_annotations.csv   the same fields for dashboard chart annotations

Title lines (indented "  - ...") are dropped. Check that the background sentences you wrote do
not quote article titles verbatim before committing the summary.
"""

from __future__ import annotations

import re
import sys
from collections import Counter

import _bootstrap  # noqa: F401
import pandas as pd

from src.common.config import EVALUATION_DIR, PROCESSED_DIR

NOTES = PROCESSED_DIR / "eda" / "spike_notes.md"
SPIKE_TYPES = ("법인 행사·보도자료", "규제·감독 사건", "실적·수주", "제재·사고", "인사", "수집 방식 영향", "기타")
FIELDS = {"유형": "type", "주 법인": "leading_firm", "관련 카테고리": "category", "배경": "background",
          "수집 영향": "collection_impact"}
_HEADER = re.compile(r"^###\s+(\S+)\s+(\d{4}-\d{2}-\d{2})\s*$")
_COUNT = re.compile(r"^-\s*(\d+)건\s*\(기준\s*([\d.]+)\)")
_FIELD = re.compile(r"^-\s*(유형|주 법인|관련 카테고리|배경|수집 영향)\s*:\s*(.*)$")


def parse_notes(text: str) -> list[dict]:
    """Entries keyed by '### <label> <week>'; title lines (indented) are ignored."""
    entries: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if m := _HEADER.match(line):
            cur = {"scope": m.group(1), "week": m.group(2), "count": None, "threshold": None,
                   **{v: "" for v in FIELDS.values()}}
            entries.append(cur)
        elif cur is None or line.startswith("  "):
            continue  # preamble or an article title
        elif m := _COUNT.match(line):
            cur["count"], cur["threshold"] = int(m.group(1)), float(m.group(2))
        elif m := _FIELD.match(line):
            cur[FIELDS[m.group(1)]] = m.group(2).strip()
    for e in entries:
        # "유형: 인사 (정기 인사 + 포럼)" -> type "인사", detail kept separately
        raw = e["type"]
        e["type"] = raw.split("(", 1)[0].strip()
        e["type_detail"] = raw.split("(", 1)[1].rstrip(")").strip() if "(" in raw else ""
    return entries


def invalid_types(entries: list[dict]) -> list[str]:
    return [f"{e['scope']} {e['week']}: {e['type']}" for e in entries if e["type"] and e["type"] not in SPIKE_TYPES]


def render(entries: list[dict]) -> str:
    filled = [e for e in entries if e["type"]]
    overall = [e for e in filled if e["scope"] == "전체"]
    lines = [
        "# 이슈 급증 시점과 배경 (가이드 5-3)",
        "",
        "- 출처: 로컬 `spike_notes.md` (사람이 대표 제목을 읽고 기록). 이 요약에는 기사 제목을 넣지 않는다.",
        "- 기준: 정제본 주간 기사량이 직전 8주 평균 + 2σ 초과",
        f"- 기록 완료 {len(filled)} / 전체 {len(entries)}개 항목",
        "",
        "## 유형별 개수 (전체 기준)",
        "",
        "| 유형 | 주 수 |",
        "|---|---|",
        *[f"| {t} | {n} |" for t, n in Counter(e["type"] for e in overall).most_common()],
        "",
        "## 주 법인별 개수 (전체 기준)",
        "",
        "| 주 법인 | 주 수 |",
        "|---|---|",
        *[f"| {t} | {n} |" for t, n in Counter(e["leading_firm"] or "(미기록)" for e in overall).most_common()],
        "",
        "## 항목",
        "",
        "| 범위 | 주 | 기사 수 (기준) | 유형 | 주 법인 | 관련 카테고리 | 배경 | 수집 영향 |",
        "|---|---|---|---|---|---|---|---|",
        *[f"| {e['scope']} | {e['week']} | {e['count']} ({e['threshold']}) | {e['type']} | {e['leading_firm']} | "
          f"{e['category']} | {e['background']} | {e['collection_impact']} |" for e in filled],
    ]
    bad = invalid_types(entries)
    if bad:
        lines += ["", f"- ⚠️ 목록에 없는 유형 {len(bad)}개 (유형 칸에는 목록 이름만, 설명은 괄호 안에): " + "; ".join(bad)]
    missing = [f"{e['scope']} {e['week']}" for e in entries if not e["type"]]
    if missing:
        lines += ["", f"- 유형 미기록 {len(missing)}개: " + ", ".join(missing)]
    return "\n".join(lines) + "\n"


def main() -> int:
    if not NOTES.exists():
        print(f"{NOTES} not found. Run scripts/run_eda.py and fill it first.")
        return 1
    entries = parse_notes(NOTES.read_text(encoding="utf-8"))
    EVALUATION_DIR.mkdir(exist_ok=True)
    (EVALUATION_DIR / "spike_summary.md").write_text(render(entries), encoding="utf-8")
    pd.DataFrame(entries).to_csv(PROCESSED_DIR / "eda" / "spike_annotations.csv", index=False, encoding="utf-8-sig")
    filled = sum(1 for e in entries if e["type"])
    by_type = Counter(e["type"] for e in entries if e["type"] and e["scope"] == "전체")
    print(f"entries={len(entries)} filled={filled} -> {EVALUATION_DIR / 'spike_summary.md'}")
    print("전체 기준 유형별: " + ", ".join(f"{t} {n}" for t, n in by_type.most_common()))
    bad = invalid_types(entries)
    if bad:
        print(f"\n⚠️ 목록에 없는 유형 {len(bad)}개. 유형 칸에는 아래 중 하나만 쓰고 설명은 괄호 안에:")
        print("   " + " / ".join(SPIKE_TYPES))
        for b in bad[:20]:
            print("   - " + b)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
