"""Day 5: RAG evaluation on evaluation/rag_test_questions.json (guide 10, src/rag/CLAUDE.md).

Two modes:
  --retrieval-only  embed the questions only (no chat call) and print the best relevance per question,
                    answerable vs unanswerable — the data for choosing settings.rag.min_relevance.
  (default)         full pipeline; writes every answer + citations to data/processed/rag_eval/answers.csv
                    (git-ignored) and the automatic metrics to evaluation/rag_eval_report.md.

Answer correctness needs a human: fill `human_correct` (1/0) in answers.csv, then
  python scripts/evaluate_rag.py --score data/processed/rag_eval/answers.csv
adds it to the report. The CSV is never overwritten once `human_correct` has values (use --force).

Search comparison (vector / hybrid / hybrid_rerank, settings.rag.retrieval):
  python scripts/evaluate_rag.py --mode hybrid              # answers_hybrid.csv + rag_eval_hybrid.md
  python scripts/evaluate_rag.py --score data/processed/rag_eval/answers_hybrid.csv --mode hybrid
  python scripts/evaluate_rag.py --compare                  # every scored answers_<mode>.csv -> comparison
Questions marked "set": "holdout" were added after the search settings were tuned, so they show
whether a change only fits the original questions.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from src.common.config import PROJECT_ROOT, get_embedding_model, get_llm_model, load_settings
from src.common.csv_io import read_excel_csv
from src.rag.rag_pipeline import PROMPT_VERSION, RETRIEVAL_MODES, Answer, RagPipeline, detect_firms, load_pipeline

QUESTIONS = PROJECT_ROOT / "evaluation" / "rag_test_questions.json"
OUT_DIR = PROJECT_ROOT / "data" / "processed" / "rag_eval"
ANSWERS = OUT_DIR / "answers.csv"
REPORT = PROJECT_ROOT / "evaluation" / "rag_eval_report.md"
COMPARISON = PROJECT_ROOT / "evaluation" / "rag_search_comparison.md"
FIELDS = ["id", "set", "answerable", "question", "refused", "refusal_ok", "reason", "top_score", "retrieved",
          "verified_citations", "audit_client_cited", "unverified_numbers", "answer", "citations",
          "human_correct", "human_note"]


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


def paths_for(mode: str | None) -> tuple[Path, Path]:
    """(answers.csv, report) — the default run keeps the original names; a --mode run gets its own files."""
    if not mode:
        return ANSWERS, REPORT
    return OUT_DIR / f"answers_{mode}.csv", PROJECT_ROOT / "evaluation" / f"rag_eval_{mode}.md"


def row_for(q: dict, a: Answer) -> dict:
    cites = [{"rcept_no": c.metadata.get("rcept_no"), "report_nm": c.metadata.get("report_nm"),
              "rcept_dt": c.metadata.get("rcept_dt"), "filing_role": c.metadata.get("filing_role"),
              "client_name": c.metadata.get("client_name"), "quote": c.quote} for c in a.citations]
    return {
        "id": q["id"], "set": q.get("set", "base"), "answerable": int(q["answerable"]), "question": q["question"],
        "refused": int(a.refused),
        # Unanswerable must be refused; answerable must not be (whether the answer is right is human-judged).
        "refusal_ok": int(a.refused != q["answerable"]),
        "reason": a.reason, "top_score": "" if a.top_score is None else round(a.top_score, 4),
        "retrieved": a.retrieved, "verified_citations": len(a.citations),
        "audit_client_cited": int(any(c["filing_role"] == "audit_client" for c in cites)),
        # numbers in the answer not found in the cited chunks (shown as "확인 필요" on the dashboard)
        "unverified_numbers": " ".join(a.unverified_numbers),
        # newlines -> " / " so each answer stays on one Excel row
        "answer": " / ".join(x.strip() for x in a.answer.splitlines() if x.strip()),
        "citations": json.dumps(cites, ensure_ascii=False),
        "human_correct": "", "human_note": "",
    }


def read_answers(path: Path) -> list[dict]:
    """answers.csv as Excel may have re-saved it (UTF-8, cp949 CSV, or UTF-16 tab 'Unicode text')."""
    rows = read_excel_csv(path, dtype=str, keep_default_na=False).to_dict("records")
    missing = {"id", "answerable", "refused", "human_correct"} - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)} — save it as 'CSV (쉼표로 분리)'.")
    return rows


def label_problems(rows: list[dict]) -> list[str]:
    """Judgements that cannot be right, usually a value typed one row off in Excel:
    a refused answer judged correct, a label on an unanswerable question, a value other than 0/1,
    or an answerable question left unjudged."""
    out = []
    for r in rows:
        label = str(r.get("human_correct") or "").strip()
        answerable, refused = str(r["answerable"]).strip() == "1", str(r["refused"]).strip() == "1"
        if not answerable:
            if label or str(r.get("human_note") or "").strip():
                out.append(f"{r['id']}: unanswerable question — leave human_correct / human_note empty")
        elif label not in {"0", "1"}:
            out.append(f"{r['id']}: human_correct must be 1 or 0 (got {label!r})")
        elif refused and label == "1":
            out.append(f"{r['id']}: the answer was refused, so it cannot be judged correct (1)")
    return out


def has_human_labels(path: Path) -> bool:
    if not path.exists():
        return False
    return any((r.get("human_correct") or "").strip() for r in read_answers(path))


def ensure_writable(path: Path) -> None:
    """Fail fast when the file is locked (e.g. open in Excel on Windows)."""
    if not path.exists():
        return
    try:
        with path.open("a", encoding="utf-8-sig"):
            pass
    except PermissionError:
        raise SystemExit(f"{path} is open in another program (Excel?). Close it and run again.") from None


def save_answers(rows: list[dict], path: Path) -> Path:
    """Write answers; if the file got locked during the run, save next to it instead of losing the run."""
    for target in (path, path.with_name(f"answers_{datetime.now():%Y%m%d_%H%M%S}.csv")):
        try:
            with target.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDS)
                w.writeheader()
                w.writerows(rows)
            return target
        except PermissionError:
            print(f"cannot write {target} (open in another program?)")
    raise SystemExit("could not save answers")


def summarize(rows: list[dict]) -> dict:
    ans = [r for r in rows if int(r["answerable"])]
    unans = [r for r in rows if not int(r["answerable"])]
    labelled = [r for r in ans if str(r.get("human_correct", "")).strip() in {"0", "1"}]
    return {
        "questions": len(rows), "answerable": len(ans), "unanswerable": len(unans),
        "unanswerable_refused": sum(int(r["refused"]) for r in unans),
        "answerable_answered": sum(1 - int(r["refused"]) for r in ans),
        "answered_with_verified_citation": sum(1 for r in ans if not int(r["refused"]) and int(r["verified_citations"]) > 0),
        "human_labelled": len(labelled),
        "human_correct": sum(int(r["human_correct"]) for r in labelled),
    }


def write_report(rows: list[dict], cfg: dict, path: Path = REPORT, mode: str | None = None) -> None:
    s = summarize(rows)
    mode = mode or (cfg.get("retrieval") or {}).get("mode", "vector")
    lines = [
        "# Day 5 RAG 평가 리포트", "",
        f"- 생성: {datetime.now():%Y-%m-%d %H:%M} / `scripts/evaluate_rag.py` / 검색 방식 `{mode}`",
        f"- 모델: LLM `{get_llm_model()}` (프롬프트 `{PROMPT_VERSION}`), 임베딩 `{get_embedding_model()}` / k={cfg['k']}, "
        f"거절 기준 유사도={cfg['min_relevance']}, 청크 {cfg['chunk_size']}자(겹침 {cfg['chunk_overlap']})",
        f"- 질문: {s['questions']}개 (답변 가능 {s['answerable']}, 근거 없음 {s['unanswerable']}) — "
        "`evaluation/rag_test_questions.json`", "",
        "## 거절 기준값 근거", "", cfg.get("min_relevance_basis") or "(settings.rag.min_relevance_basis 미기록)", "",
        "## 자동 지표", "",
        "| 지표 | 값 |", "|---|---|",
        f"| 근거 없는 질문을 거절 | {s['unanswerable_refused']}/{s['unanswerable']} |",
        f"| 답변 가능 질문에 답변 | {s['answerable_answered']}/{s['answerable']} |",
        f"| 답변 중 원문 대조 통과 인용 ≥1 | {s['answered_with_verified_citation']}/{s['answerable_answered']} |",
        f"| 사람 판정 정답 (판정한 것만) | "
        f"{s['human_correct']}/{s['human_labelled'] if s['human_labelled'] else '미판정'} |",
        "",
        "## 질문별", "",
        "| ID | 답변 가능 | 거절 | 판정 | 최고 유사도 | 검증 인용 | 고객사 감사보고서 인용 | 사람 판정 | 거절 사유 / 사람 메모 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        ok = "O" if int(r["refusal_ok"]) else "X"
        lines.append(f"| {r['id']} | {'예' if int(r['answerable']) else '아니오'} | "
                     f"{'예' if int(r['refused']) else '아니오'} | {ok} | {r['top_score']} | "
                     f"{r['verified_citations']} | {'예' if int(r['audit_client_cited']) else ''} | "
                     f"{(r.get('human_correct') or '').strip()} | "
                     f"{' / '.join(x for x in (r['reason'], (r.get('human_note') or '').strip()) if x).replace('|', '/')} |")
    lines += ["", "판정 O = 근거 없는 질문은 거절, 답변 가능 질문은 답변. 답의 정확성은 `human_correct`로 따로 본다.",
              "거절 사유가 '검색 유사도 …'이면 1차(유사도, LLM 호출 없음), 그 외는 2차(모델 거절·인용 검증)에서 거절된 것이다.",
              "거절 기준값을 이 질문들로 정했으므로 이 표의 거절 판정은 새 질문보다 낙관적이다.",
              "고객사 감사보고서를 인용한 답변은 고객사·감사인을 밝혔는지 사람이 확인한다 (원칙 8).", ""]
    # '~' twice on a line renders as strikethrough on GitHub (ranges such as 0.627~0.728)
    path.write_text(re.sub(r"(?<!\\)~", r"\\~", "\n".join(lines)), encoding="utf-8")


def retrieval_only(pipe: RagPipeline, questions: list[dict]) -> None:
    by_group: dict[bool, list[float]] = {True: [], False: []}
    for q in questions:
        hits, top = pipe.search(q["question"], **q.get("filters", {}))
        top = top or 0.0
        by_group[q["answerable"]].append(top)
        print(f"{q['id']} answerable={q['answerable']!s:5} top={top:.4f} hits={len(hits)}")
    for flag, scores in by_group.items():
        if scores:
            print(f"answerable={flag}: min={min(scores):.4f} median={statistics.median(scores):.4f} max={max(scores):.4f}")
    print(f"current min_relevance={pipe.min_relevance}. Pick a value between the groups and record why.")


def show(pipe: RagPipeline, questions: list[dict], target: str, company_id: str | None = None,
         filing_role: str | None = None, k: int | None = None) -> None:
    """Print what retrieval returns (question embedding only, no chat call).

    `target` is an evaluation question ID (its filters are used) or any question text (filters from
    --company / --role; a single firm named in the text is applied as in RagPipeline.ask).
    """
    q = next((q for q in questions if q["id"] == target), None)
    if q is not None:
        question, filters = q["question"], dict(q.get("filters", {}))
    else:
        question, filters = target, {"company_id": company_id, "filing_role": filing_role}
        if not company_id:
            named = detect_firms(question, pipe.firm_aliases)
            if len(named) == 1:
                filters["company_id"] = next(iter(named))
    if k:
        pipe.k = k
    print(f"{question}  filters={ {key: v for key, v in filters.items() if v} }  k={pipe.k}  mode={pipe.retrieval}")
    for i, (text, md, score) in enumerate(pipe.retrieve(question, **filters), 1):
        who = f"client={md.get('client_name') or '?'}" if md.get("filing_role") == "audit_client" else "own"
        shown = "keyword-only" if score is None else f"{score:.4f}"
        print(f"\n[{i}] score={shown} {md.get('report_nm')} {md.get('rcept_dt')} {md.get('filer')} {who}"
              f" | section={md.get('section_title')}")
        print("    " + " ".join(text.split())[:300])


def compare(out: Path = COMPARISON) -> None:
    """Side-by-side metrics of every answers_<mode>.csv that has human labels, base vs holdout questions."""
    runs = {}
    for mode in RETRIEVAL_MODES:
        path = OUT_DIR / f"answers_{mode}.csv"
        if path.exists():
            rows = read_answers(path)
            if any(str(r.get("human_correct") or "").strip() for r in rows):
                runs[mode] = rows
    if not runs:
        raise SystemExit("no scored answers_<mode>.csv yet: run --mode, fill human_correct, then --score.")
    problems = [f"answers_{mode}.csv — {p}" for mode, rows in runs.items() for p in label_problems(rows)]
    if problems:  # same check as --score: a refused answer judged 1, a label one row off, ...
        print("Human labels look misplaced (fix the CSV, then run --compare again):")
        print("\n".join(f"  {p}" for p in problems))
        raise SystemExit(1)
    lines = ["# 공시 QA 검색 방식 비교", "", f"- 생성: {datetime.now():%Y-%m-%d %H:%M} / `scripts/evaluate_rag.py --compare`",
             "- base = 기존 평가 질문(검색 설정을 맞출 때 쓴 질문), holdout = 검색 고도화 뒤 새로 추가한 질문, "
             "holdout2 = 검색 방식을 정한 뒤 추가한 질문(수치 비교·여러 법인·최신 공시·표현 변경)", "",
             "| 방식 | 질문 묶음 | 근거 없는 질문 거절 | 답변 가능 질문에 답변 | 사람 판정 정답 |", "|---|---|---|---|---|"]
    for mode, rows in runs.items():
        extra = sorted({r.get("set") or "base" for r in rows} - {"base", "holdout"})
        for name in ("base", "holdout", *extra, "전체"):
            part = rows if name == "전체" else [r for r in rows if (r.get("set") or "base") == name]
            if not part:
                continue
            s = summarize(part)
            lines.append(f"| {mode} | {name} | {s['unanswerable_refused']}/{s['unanswerable']} | "
                         f"{s['answerable_answered']}/{s['answerable']} | "
                         f"{s['human_correct']}/{s['human_labelled'] or '미판정'} |")
    ids = [r["id"] for r in next(iter(runs.values()))]
    lines += ["", "## 질문별 사람 판정 (1 정답 / 0 오답 / 거 = 거절)", "",
              "| ID | " + " | ".join(runs) + " |", "|---|" + "---|" * len(runs)]
    for qid in ids:
        cells = []
        for rows in runs.values():
            r = next((x for x in rows if x["id"] == qid), None)
            if r is None:
                cells.append("")
            elif not int(r["answerable"]):
                cells.append("거" if int(r["refused"]) else "답변(X)")
            else:
                cells.append((str(r.get("human_correct") or "").strip() or "?") + (" (거)" if int(r["refused"]) else ""))
        lines.append(f"| {qid} | " + " | ".join(cells) + " |")
    lines += ["", "평가 질문이 적어(11+holdout) 한두 문항 차이는 우연일 수 있다. holdout에서도 같은 방향인지 함께 본다.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"comparison -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--show", metavar="ID_OR_TEXT",
                    help="print the retrieved chunks for an evaluation question ID (Q06) or any question text")
    ap.add_argument("--company", help="with --show TEXT: pwc | kpmg | ey | deloitte")
    ap.add_argument("--role", choices=["own", "audit_client"], help="with --show TEXT: filing_role filter")
    ap.add_argument("--k", type=int, help="with --show: number of chunks to retrieve (default settings.rag.k)")
    ap.add_argument("--score", type=Path, help="answers.csv with human_correct filled -> update report")
    ap.add_argument("--force", action="store_true", help="overwrite answers.csv even if it has human labels")
    ap.add_argument("--mode", choices=RETRIEVAL_MODES,
                    help="search method for this run (default settings.rag.retrieval.mode); results go to *_<mode> files")
    ap.add_argument("--compare", action="store_true", help="compare every scored answers_<mode>.csv")
    args = ap.parse_args()
    cfg = load_settings()["rag"]
    answers_path, report_path = paths_for(args.mode)

    if args.compare:
        compare()
        return
    if args.score:
        rows = read_answers(args.score)
        problems = label_problems(rows)
        if problems:
            print("Human labels look misplaced (fix answers.csv, then run --score again):")
            print("\n".join(f"  {p}" for p in problems))
            raise SystemExit(1)
        write_report(rows, cfg, report_path, args.mode)
        print(f"report -> {report_path}")
        return

    questions = load_questions()
    pipe = load_pipeline(args.mode)
    if args.show:
        show(pipe, questions, args.show, args.company, args.role, args.k)
        return
    if args.retrieval_only:
        retrieval_only(pipe, questions)
        return
    if has_human_labels(answers_path) and not args.force:
        raise SystemExit(f"{answers_path} already has human_correct values. Score it with --score, or use --force.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ensure_writable(answers_path)  # before any API call: an open Excel file must not waste a whole run
    rows = []
    for q in questions:
        a = pipe.ask(q["question"], **q.get("filters", {}))
        rows.append(row_for(q, a))
        print(f"{q['id']} refused={a.refused} top={a.top_score} citations={len(a.citations)}")
    saved = save_answers(rows, answers_path)
    write_report(rows, cfg, report_path, args.mode)
    print(f"answers -> {saved}\nreport  -> {report_path}\nFill human_correct (1/0) in {saved.name}, then --score"
          + (f" ... --mode {args.mode}." if args.mode else "."))


if __name__ == "__main__":
    main()
