"""Re-judge gate 2 on what is actually loaded in the DB (DART rows with extracted text).

Usage: python scripts/recheck_gate2.py   (after process_data.py)

Same criteria as config/settings.yaml gate2_criteria, unchanged. Writes evaluation/gate2_recheck.md.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime

import _bootstrap  # noqa: F401

from src.common.config import EVALUATION_DIR, get_big4_corp_codes, load_settings
from src.common.dart import report_type
from src.database import db_manager


def judge(docs: list[dict], criteria: dict[str, int]) -> list[tuple[str, int, int, bool]]:
    with_text = [d for d in docs if d["content"].strip()]
    months = {d["published_at"][:7] for d in with_text}
    rows = [
        ("텍스트 추출 가능한 공시 수", len(with_text), criteria["min_extractable_docs"]),
        ("공시가 있는 서로 다른 월 수", len(months), criteria["min_distinct_months"]),
        ("공시가 있는 서로 다른 연도 수", len({m[:4] for m in months}), criteria["min_distinct_years"]),
        ("공시가 확인된 법인 수", len({c for d in with_text for c in d["company_ids"]}),
         criteria["min_companies_with_docs"]),
        ("서로 다른 공시 종류 수", len({report_type(d["report_nm"] or d["title"]) for d in with_text}),
         criteria["min_distinct_report_types"]),
    ]
    return [(n, a, r, a >= r) for n, a, r in rows]


def main() -> int:
    with db_manager.connect() as conn:
        docs = db_manager.fetch_documents(conn, "dart")
    criteria = load_settings()["gate2_criteria"]
    verdict = judge(docs, criteria)
    own = get_big4_corp_codes()
    with_text = [d for d in docs if d["content"].strip()]
    kinds = Counter(("자체" if d["corp_code"] in own else "고객사 감사보고서", report_type(d["report_nm"] or d["title"]))
                    for d in with_text)
    lines = [
        "# 게이트 2 재판정 — DB 적재 기준 (분기 B-1)",
        "",
        f"- 실행 일시: {datetime.now():%Y-%m-%d %H:%M}",
        f"- DART 행 {len(docs):,}건 중 텍스트 보유 {len(with_text):,}건. 기준은 조회 전 확정값 그대로.",
        "",
        "| 구분 | 공시 종류 | 건수 |",
        "|---|---|---|",
        *[f"| {role} | {kind} | {n} |" for (role, kind), n in kinds.most_common()],
        "",
        "| 기준 | 실제 | 필요 | 통과 |",
        "|---|---|---|---|",
        *[f"| {n} | {a} | {r} | {'O' if p else 'X'} |" for n, a, r, p in verdict],
        "",
        f"**결과: {'기준 충족' if all(p for *_, p in verdict) else '기준 미달'}**",
    ]
    report = "\n".join(lines) + "\n"
    (EVALUATION_DIR / "gate2_recheck.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
