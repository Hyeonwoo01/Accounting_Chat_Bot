"""Branch B-1 feasibility check: market-wide DART filings that involve the Big4 (guide 0-4).

Usage: python scripts/verify_dart_market.py

Gate 2 showed the Big4 firms' own filings are one yearly report each. B-1 widens the
disclosure axis to filings by *other* companies that involve the Big4:

  A. 외부감사관련(F) filings whose filer (flr_nm) is a Big4 firm — audit reports they submit
  B. 거래소/주요사항/기타 filings whose title mentions the auditor (e.g. 외부감사인 선임·변경)

Only sample windows (config `dart_market`) are scanned, with a page cap, so totals for the
full period are linear ESTIMATES. Writes evaluation/gate2b_market_report.md.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import _bootstrap  # noqa: F401

from src.collectors.dart_collector import DartCollector
from src.collectors.http_client import RateLimitedClient
from src.common.config import EVALUATION_DIR, get_period, load_company_mapping, load_settings
from src.common.dart import PBLNTF_TYPES, report_type
from src.preprocessing.company_matcher import CompanyMatcher
from src.preprocessing.data_processor import extract_dart_zip

EXISTING_BIG4_REPORT_TYPES = {"회계법인사업보고서"}  # from gate 2 (the firms' own filings)


@dataclass
class TypeScan:
    pblntf_ty: str
    total_count: int = 0
    total_page: int = 0
    scanned: int = 0
    hits: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WindowScan:
    start: date
    end: date
    types: dict[str, TypeScan] = field(default_factory=dict)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def filer_company(flr_nm: str, filer_names: dict[str, str]) -> str | None:
    """company_id if the filer is a Big4 Korean entity (exact entity name contained in flr_nm)."""
    return next((cid for name, cid in filer_names.items() if name in (flr_nm or "")), None)


def is_keyword_hit(report_nm: str, keywords: list[str]) -> bool:
    return any(k in report_nm for k in keywords)


def project(windows: list[WindowScan], pblntf_ty: str, period_days: int) -> float:
    """Hits per scanned filing x window total, summed over windows, scaled to the full period."""
    found, days = 0.0, 0
    for w in windows:
        t = w.types.get(pblntf_ty)
        if t is None:
            continue
        days += w.days
        if t.scanned:
            found += len(t.hits) / t.scanned * t.total_count
    return found / days * period_days if days else 0.0


def full_collection_calls(windows: list[WindowScan], period_days: int) -> int:
    pages = sum(t.total_page for w in windows for t in w.types.values())
    days = sum(w.days for w in windows)
    return round(pages / days * period_days) if days else 0


def scan(collector: DartCollector, cfg: dict[str, Any], filer_names: dict[str, str]) -> list[WindowScan]:
    windows = []
    for start_s, end_s in cfg["sample_windows"]:
        w = WindowScan(date.fromisoformat(start_s), date.fromisoformat(end_s))
        for ty in cfg["filer_types"] + cfg["keyword_types"]:
            t = TypeScan(ty)
            page = 1
            while page <= cfg["max_pages_per_type"]:
                payload = collector.list_page(w.start, w.end, pblntf_ty=ty, page=page)
                t.total_count = int(payload.get("total_count", 0))
                t.total_page = int(payload.get("total_page", 0))
                items = payload.get("list", [])
                if not items:
                    break
                t.scanned += len(items)
                last = min(t.total_page, cfg["max_pages_per_type"])
                print(f"  {start_s}~{end_s} {ty}: page {page}/{last} (total {t.total_count:,})", flush=True)
                for item in items:
                    if ty in cfg["filer_types"]:
                        cid = filer_company(item.get("flr_nm", ""), filer_names)
                        if cid:
                            t.hits.append({**item, "pblntf_ty": ty, "company_id": cid})
                    elif is_keyword_hit(item.get("report_nm", ""), cfg["report_keywords"]):
                        t.hits.append({**item, "pblntf_ty": ty})
                if page >= t.total_page:
                    break
                page += 1
            w.types[ty] = t
            print(f"{start_s}~{end_s} {ty}: total={t.total_count:,} scanned={t.scanned:,} hits={len(t.hits)}", flush=True)
        windows.append(w)
    return windows


def check_documents(collector: DartCollector, hits: list[dict[str, Any]], matcher: CompanyMatcher) -> list[dict[str, Any]]:
    rows = []
    for i, h in enumerate(hits, 1):
        print(f"  document {i}/{len(hits)}: {h['rcept_no']} {h['report_nm']}", flush=True)
        doc = collector.download_document(h["rcept_no"])
        row = {"rcept_no": h["rcept_no"], "report_nm": h["report_nm"], "corp_name": h.get("corp_name", ""),
               "ok": doc.ok, "chars": 0, "big4": [], "error": doc.error}
        if doc.ok:
            try:
                text = extract_dart_zip(doc.path, h["rcept_no"])
                row["chars"] = len(text)
                row["big4"] = matcher.match(text)
            except Exception as e:  # record, do not abort the check
                row["error"] = f"extract: {type(e).__name__}"
        rows.append(row)
    return rows


def pick_samples(windows: list[WindowScan], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Mostly keyword hits (the uncertain part), plus a couple of filer hits."""
    n = cfg["sample_documents"]
    kw = [h for w in windows for ty in cfg["keyword_types"] for h in w.types[ty].hits]
    fl = [h for w in windows for ty in cfg["filer_types"] for h in w.types[ty].hits]
    n_fl = min(2, len(fl))
    return kw[: n - n_fl] + fl[:n_fl]


def write_report(windows, docs, cfg, period_days) -> str:
    criteria = load_settings()["gate2_criteria"]
    lines = [
        "# 게이트 2-B1 — 시장 전체 공시 중 Big4 관련 공시 확인",
        "",
        f"- 실행 일시: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 표본 창: {', '.join(f'{w.start}~{w.end}' for w in windows)} (유형·창당 최대 {cfg['max_pages_per_type']}페이지)",
        f"- 공시명 키워드: {', '.join(cfg['report_keywords'])}",
        "- **전체 기간 수치는 표본으로부터의 선형 추정이다.** 성수기·비수기 각 1개 창만 봤다.",
        "",
        "## 유형별 규모",
        "",
        "| 창 | 유형 | 전체 건수 | 훑은 건수 | Big4 관련 |",
        "|---|---|---|---|---|",
    ]
    for w in windows:
        for t in w.types.values():
            lines.append(f"| {w.start}~{w.end} | {PBLNTF_TYPES.get(t.pblntf_ty, t.pblntf_ty)} | "
                         f"{t.total_count:,} | {t.scanned:,} | {len(t.hits)} |")

    filer_hits = [h for w in windows for ty in cfg["filer_types"] for h in w.types[ty].hits]
    kw_hits = [h for w in windows for ty in cfg["keyword_types"] for h in w.types[ty].hits]

    lines += ["", "## A. Big4가 제출인인 외부감사관련 공시", ""]
    by_firm = Counter(h["company_id"] for h in filer_hits)
    lines.append("- 법인별(표본): " + (", ".join(f"{k} {v}" for k, v in by_firm.most_common()) or "없음"))
    lines.append("- 공시명 종류(표본): " + (", ".join(
        f"{k} ({v})" for k, v in Counter(report_type(h["report_nm"]) for h in filer_hits).most_common(10)) or "없음"))
    proj_a = sum(project(windows, ty, period_days) for ty in cfg["filer_types"])
    lines.append(f"- 전체 기간 추정: 약 {proj_a:,.0f}건")

    lines += ["", "## B. 공시명에 감사인 키워드가 있는 공시", ""]
    lines.append("- 공시명 종류(표본): " + (", ".join(
        f"{k} ({v})" for k, v in Counter(report_type(h["report_nm"]) for h in kw_hits).most_common(10)) or "없음"))
    proj_b = sum(project(windows, ty, period_days) for ty in cfg["keyword_types"])
    lines.append(f"- 전체 기간 추정: 약 {proj_b:,.0f}건 (Big4 언급 여부와 무관)")

    lines += ["", "## 원문 표본 확인", "", "| 접수번호 | 회사 | 공시명 | 원문 | 글자 수 | 언급된 Big4 |", "|---|---|---|---|---|---|"]
    for d in docs:
        status = "O" if d["ok"] and not d["error"] else f"X {d['error'][:40]}"
        lines.append(f"| {d['rcept_no']} | {d['corp_name']} | {d['report_nm']} | {status} | {d['chars']:,} | "
                     f"{', '.join(d['big4']) or '-'} |")

    kw_docs = [d for d in docs if any(d["rcept_no"] == h["rcept_no"] for h in kw_hits) and d["chars"]]
    mention_rate = (sum(bool(d["big4"]) for d in kw_docs) / len(kw_docs)) if kw_docs else 0.0
    proj_total = proj_a + proj_b * mention_rate
    types = {report_type(h["report_nm"]) for h in filer_hits}
    types |= {report_type(d["report_nm"]) for d in kw_docs if d["big4"]}
    types |= EXISTING_BIG4_REPORT_TYPES
    firms = set(by_firm) | {c for d in kw_docs for c in d["big4"]}

    calls = full_collection_calls(windows, period_days)
    lines += [
        "",
        "## 추정 판정 (게이트 2 기준 적용)",
        "",
        f"B의 Big4 언급 비율은 원문 표본 {len(kw_docs)}건 기준 {mention_rate:.0%}로 가정했다.",
        "",
        "| 기준 | 추정/표본 값 | 필요 | 통과 |",
        "|---|---|---|---|",
        f"| 공시 수 (A + B×언급 비율, 추정) | {proj_total:,.0f} | {criteria['min_extractable_docs']} | "
        f"{'O' if proj_total >= criteria['min_extractable_docs'] else 'X'} |",
        f"| 공시 종류 수 (표본 + 기존 사업보고서) | {len(types)} | {criteria['min_distinct_report_types']} | "
        f"{'O' if len(types) >= criteria['min_distinct_report_types'] else 'X'} |",
        f"| 관련 법인 수 (표본) | {len(firms)} | {criteria['min_companies_with_docs']} | "
        f"{'O' if len(firms) >= criteria['min_companies_with_docs'] else 'X'} |",
        "| 월 수 / 연도 수 | 전체 수집 후 확인 | "
        f"{criteria['min_distinct_months']} / {criteria['min_distinct_years']} | - |",
        "",
        "## 비용",
        "",
        f"- 이번 검증 호출: 목록 {sum(min(t.total_page, cfg['max_pages_per_type']) for w in windows for t in w.types.values())}회"
        f" + 원문 {len(docs)}회 내외",
        f"- 위 유형 전체를 기간 내 모두 훑을 때 추정 목록 호출: 약 {calls:,}회 "
        f"(일일 한도 {cfg['daily_call_limit']:,}회 기준 약 {calls / cfg['daily_call_limit']:.1f}일)",
        "",
        "> 추정치가 기준 근처라면 창을 늘려 다시 확인한다. 진행 여부(B-1 / C)는 팀이 결정하고 이 문서에 기록한다.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    settings = load_settings()
    cfg = settings["dart_market"]
    period = get_period()
    period_days = (period.end - period.start).days + 1
    mapping = load_company_mapping()
    filer_names = {e["name"]: c["company_id"] for c in mapping["companies"] for e in c.get("kr_entities", [])}

    collector = DartCollector(RateLimitedClient(settings["dart"]["request_delay_sec"], settings["dart"]["max_retries"]))
    print(f"scanning {len(cfg['sample_windows'])} window(s) x {len(cfg['filer_types']) + len(cfg['keyword_types'])} "
          f"type(s), up to {cfg['max_pages_per_type']} pages each", flush=True)
    windows = scan(collector, cfg, filer_names)
    docs = check_documents(collector, pick_samples(windows, cfg), CompanyMatcher(mapping))

    report = write_report(windows, docs, cfg, period_days)
    EVALUATION_DIR.mkdir(exist_ok=True)
    (EVALUATION_DIR / "gate2b_market_report.md").write_text(report, encoding="utf-8")
    print("\n" + report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
