"""Day 1 (owner A): check how much history the Naver news API actually returns per query.

Usage: python scripts/verify_news_gate.py

Two calls per query (first page + deepest reachable page). Nothing is saved to data/raw;
writes evaluation/news_gate_report.md so the team can adjust keywords before Day 2.

The per-company coverage summary gives the earliest date from which EVERY query of that
company is complete; comparisons across companies are only fair from the latest of those dates.
"""

from __future__ import annotations

import sys
from datetime import date, datetime

import _bootstrap  # noqa: F401

from src.collectors.http_client import RateLimitedClient
from src.collectors.news_collector import NewsCollector, QueryReport, SearchQuery, build_queries
from src.common.config import (
    EVALUATION_DIR,
    NAVER_NEWS_DISABLED_REASON,
    get_period,
    load_company_mapping,
    load_settings,
    naver_news_enabled,
)


def coverage_start(reports: list[QueryReport], period_start: date) -> date:
    """Earliest date from which all given queries are complete (period start if none is cut off)."""
    starts = [
        date.fromisoformat(r.oldest_pub)
        for r in reports
        if r.truncated and not r.reached_period_start and r.oldest_pub
    ]
    return max([period_start, *starts])


def is_complete(r: QueryReport) -> bool:
    """Every article of the period is reachable: not truncated, or truncated but past the period start."""
    return not r.truncated or r.reached_period_start


def topic_completeness(results: list[tuple[SearchQuery, QueryReport]]) -> list[dict]:
    """Per topic: complete queries / all queries, and which alias queries were cut off (oldest date)."""
    by_topic: dict[str, list[tuple[SearchQuery, QueryReport]]] = {}
    for q, r in results:
        by_topic.setdefault(q.topic or q.keyword, []).append((q, r))
    rows = []
    for topic, items in by_topic.items():
        cut = [f"{r.query.rsplit(' ', len(topic.split()))[0]}({r.oldest_pub})" for _, r in items if not is_complete(r)]
        rows.append({
            "topic": topic,
            "keyword": items[0][0].keyword,
            "complete": len(items) - len(cut),
            "total": len(items),
            "cut": cut,
        })
    return rows


def main() -> int:
    if not naver_news_enabled():
        print(NAVER_NEWS_DISABLED_REASON, file=sys.stderr)
        return 3
    settings = load_settings()
    news = settings["news"]
    period = get_period()
    collector = NewsCollector(
        RateLimitedClient(news["request_delay_sec"], news["max_retries"]),
        display=news["display"], max_start=news["max_start"], api_url=news["api_url"],
    )
    queries = build_queries(settings, load_company_mapping())

    lines = [
        "# 뉴스 수집 가능 범위 확인 (네이버 검색 API)",
        "",
        f"- 실행 일시: {datetime.now():%Y-%m-%d %H:%M}",
        f"- 목표 기간: {period.start} ~ {period.end}",
        f"- 검색어당 도달 가능 건수: {collector.reachable:,}",
        "",
        "| 검색어 | 키워드 | total | 가장 최근 | 도달 가능한 가장 오래된 기사 | 잘림 | 기간 시작 도달 |",
        "|---|---|---|---|---|---|---|",
    ]
    problems = 0
    by_company: dict[str, list[QueryReport]] = {}
    results: list[tuple[SearchQuery, QueryReport]] = []
    for i, q in enumerate(queries, 1):
        r = collector.probe(q, period.start)
        results.append((q, r))
        by_company.setdefault(q.company_id or "(base)", []).append(r)
        problem = r.truncated and not r.reached_period_start
        problems += problem
        lines.append(f"| {r.query} | {r.keyword} | {r.total:,} | {r.newest_pub or '-'} | {r.oldest_pub or '-'} | "
                     f"{'예' if r.truncated else '아니오'} | {'O' if r.reached_period_start else 'X'} |")
        print(f"{i}/{len(queries)} {q.query}: total={r.total} oldest={r.oldest_pub} truncated={r.truncated}",
              flush=True)
    topics = topic_completeness(results)
    passed = [t["topic"] for t in topics if t["complete"] == t["total"]]
    lines += [
        "",
        "## 주제별 완전 수집 여부",
        "",
        "규칙: 주제의 모든 검색어(법인 × 별칭)가 기간 시작까지 닿아야 사용한다 (법인 간 비교의 공정성).",
        "",
        "| 묶음 | 주제 | 완전 수집 | 잘린 검색어 (도달한 가장 오래된 날짜) | 사용 |",
        "|---|---|---|---|---|",
        *[f"| {t['keyword']} | {t['topic']} | {t['complete']}/{t['total']} | {', '.join(t['cut']) or '-'} | "
          f"{'O' if t['complete'] == t['total'] else 'X'} |" for t in topics],
        "",
        f"**사용 가능 주제 ({len(passed)}개): {', '.join(passed) or '없음'}**",
    ]
    starts = {c: coverage_start(rs, period.start) for c, rs in sorted(by_company.items())}
    lines += [
        "",
        "## 기업별 완전 수집 가능 시작일",
        "",
        "그 기업의 모든 검색어가 빠짐없이 수집되는 가장 이른 날짜. 기업 간 비교는 가장 늦은 날짜부터만 공정하다.",
        "",
        "| 기업 | 검색어 수 | 완전 수집 시작일 |",
        "|---|---|---|",
        *[f"| {c} | {len(by_company[c])} | {s} |" for c, s in starts.items()],
        "",
        f"**공통 분석 가능 기간: {max(starts.values())} ~ {period.end}**" if starts else "",
        "",
        f"**기간 시작까지 도달하지 못하는 잘린 검색어: {problems}개**",
        "",
        "> 잘린 검색어는 더 잘게 나누거나(키워드 × 별칭), 확보된 실제 기간만 급증 분석에 사용한다 (가이드 2-2).",
    ]
    EVALUATION_DIR.mkdir(exist_ok=True)
    (EVALUATION_DIR / "news_gate_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nwrote {EVALUATION_DIR / 'news_gate_report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
