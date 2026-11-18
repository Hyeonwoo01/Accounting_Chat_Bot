"""Gate 2 steps 2-4: check that DART filings for the verified entities are usable (guide 0-3).

Usage: python scripts/verify_dart_gate.py [--max-docs-per-corp N]

Steps:
  2. list.json per entity and disclosure type for the configured period
  3. download original documents (document.xml)
  4. extract text and measure success rate / average length
Step 5 (chunking + embedding trial) runs after the RAG module exists.

The pass criteria come from config/settings.yaml `gate2_criteria` and must be agreed
BEFORE running this. Writes evaluation/gate2_report.md with the raw numbers.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector
from src.collectors.http_client import RateLimitedClient
from src.common.config import EVALUATION_DIR, get_period, get_verified_entities, load_settings
from src.common.dart import PBLNTF_TYPES, report_type
from src.preprocessing.data_processor import extract_dart_zip


@dataclass
class EntityResult:
    company_id: str
    name: str
    corp_code: str
    filings: int = 0
    by_type: Counter = field(default_factory=Counter)
    download_attempted: int = 0
    download_ok: int = 0
    extract_ok: int = 0
    text_lengths: list[int] = field(default_factory=list)
    extractable_months: set[str] = field(default_factory=set)
    extractable_report_types: set[str] = field(default_factory=set)
    failures: Counter = field(default_factory=Counter)


def evaluate(results: list[EntityResult], criteria: dict[str, int]) -> list[tuple[str, int, int, bool]]:
    """Returns (criterion, actual, required, passed) rows."""
    total = sum(r.extract_ok for r in results)
    months = set().union(*(r.extractable_months for r in results)) if results else set()
    companies = len({r.company_id for r in results if r.extract_ok > 0})
    report_types = set().union(*(r.extractable_report_types for r in results)) if results else set()
    rows = [
        ("텍스트 추출 가능한 공시 수", total, criteria["min_extractable_docs"]),
        ("공시가 있는 서로 다른 월 수", len(months), criteria["min_distinct_months"]),
        ("공시가 있는 서로 다른 연도 수", len({m[:4] for m in months}), criteria["min_distinct_years"]),
        ("공시가 확인된 법인 수", companies, criteria["min_companies_with_docs"]),
        ("서로 다른 공시 종류 수", len(report_types), criteria["min_distinct_report_types"]),
    ]
    return [(name, actual, req, actual >= req) for name, actual, req in rows]


def run(max_docs: int | None) -> list[EntityResult]:
    settings = load_settings()
    dart_cfg = settings["dart"]
    period = get_period()
    types = dart_cfg.get("pblntf_types") or list(PBLNTF_TYPES)
    collector = DartCollector(RateLimitedClient(dart_cfg["request_delay_sec"], dart_cfg["max_retries"]))

    results = []
    for entity in get_verified_entities():
        r = EntityResult(entity["company_id"], entity["name"], entity["corp_code"])
        filings = collector.fetch_filings_by_type(
            entity["corp_code"], period.start, period.end, types, dart_cfg["page_count"]
        )
        r.filings = len(filings)
        r.by_type = Counter(PBLNTF_TYPES.get(f["pblntf_ty"], f["pblntf_ty"]) for f in filings)
        for f in sorted(filings, key=lambda x: x["rcept_dt"])[:max_docs]:
            r.download_attempted += 1
            doc = collector.download_document(f["rcept_no"])
            if not doc.ok:
                r.failures[doc.error.split(":")[-1].strip()[:60]] += 1
                continue
            r.download_ok += 1
            try:
                text = extract_dart_zip(doc.path, f["rcept_no"])
            except Exception as e:  # report every failure reason rather than abort the gate
                r.failures[f"extract: {type(e).__name__}"] += 1
                continue
            if text.strip():
                r.extract_ok += 1
                r.text_lengths.append(len(text))
                r.extractable_months.add(f["rcept_dt"][:6])
                r.extractable_report_types.add(report_type(f["report_nm"]))
            else:
                r.failures["extract: empty text"] += 1
        results.append(r)
        print(f"[{r.company_id}] {r.name}: filings={r.filings} downloaded={r.download_ok}/{r.download_attempted} "
              f"extracted={r.extract_ok}")
    return results


def write_report(results: list[EntityResult], verdict: list[tuple[str, int, int, bool]], max_docs: int | None) -> str:
    period = get_period()
    lines = [
        "# 게이트 2 — DART 실데이터 확인 결과",
        "",
        f"- 실행 일시: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 조회 기간: {period.start} ~ {period.end}",
        f"- 법인당 원문 시도 상한: {max_docs or '전체'}",
        "",
        "## 법인별 결과",
        "",
        "| 브랜드 | 법인 | corp_code | 공시 수 | 원문 확보 | 텍스트 추출 | 평균 글자 수 | 공시 유형 분포 | 실패 사유 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        avg = round(sum(r.text_lengths) / len(r.text_lengths)) if r.text_lengths else 0
        types = ", ".join(f"{k} {v}" for k, v in r.by_type.most_common()) or "-"
        fails = ", ".join(f"{k} ({v})" for k, v in r.failures.most_common()) or "-"
        lines.append(f"| {r.company_id} | {r.name} | {r.corp_code} | {r.filings} | "
                     f"{r.download_ok}/{r.download_attempted} | {r.extract_ok} | {avg:,} | {types} | {fails} |")
    all_types = Counter(t for r in results for t in r.extractable_report_types)
    lines += ["", "## 텍스트 추출된 공시 종류 (정정 표시·대상 기간 제외)", ""]
    lines += [f"- {t} — {n}개 법인" for t, n in all_types.most_common()] or ["- 없음"]
    lines += ["", "## 기준 대비 판정", "", "| 기준 | 실제 | 필요 | 통과 |", "|---|---|---|---|"]
    lines += [f"| {n} | {a} | {q} | {'O' if p else 'X'} |" for n, a, q, p in verdict]
    passed = all(p for *_, p in verdict)
    lines += [
        "",
        f"**결과: {'기준 충족 → 분기 A 후보' if passed else '기준 미달 → 분기 B 또는 C 검토 (가이드 0-4)'}**",
        "",
        "> 산업군 승인(게이트 1) 결과와 함께 최종 분기를 결정하고 이 문서에 기록한다.",
        "> 단계 5(청킹·임베딩 시험)는 RAG 모듈 구현 후 추가한다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-docs-per-corp", type=int, default=None,
                        help="limit document downloads per entity (default: all)")
    args = parser.parse_args()

    if not get_verified_entities():
        print("No verified entities. Run scripts/find_corp_codes.py, fill corp_code in "
              "config/company_mapping.yaml and set verified: true.")
        return 1

    results = run(args.max_docs_per_corp)
    verdict = evaluate(results, load_settings()["gate2_criteria"])
    report = write_report(results, verdict, args.max_docs_per_corp)
    EVALUATION_DIR.mkdir(exist_ok=True)
    (EVALUATION_DIR / "gate2_report.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
