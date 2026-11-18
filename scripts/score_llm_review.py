"""Score the blind LLM review (guide 6-5). Run after both reviewers filled their sheets.

Usage: python scripts/score_llm_review.py

Reads data/processed/llm_review/{review_A,review_B,summary_check,llm_key}.csv and, if present,
adjudication.csv (final labels for items where A and B disagreed).

1. A vs B agreement and Cohen's kappa (per field).
2. Final human label = the agreed label, or the adjudicated one. Disagreements without an
   adjudicated label are written to adjudication.csv to fill, and left out of accuracy.
3. LLM vs final label: accuracy and per-class accuracy; issue_summary factual share.

Outputs:
  evaluation/llm_validation_report.md      numbers only (no article text) — safe to commit
  data/processed/llm_review/misclassified.csv   titles of wrong items, local only
Only measured values are written (CLAUDE.md principle 3).
"""

from __future__ import annotations

import sys
from datetime import datetime

import _bootstrap  # noqa: F401
import pandas as pd

from src.analysis.validation import (
    accuracy_table,
    cohen_kappa,
    is_irrelevant,
    is_unclear,
    norm_category,
    norm_sentiment,
    weighted_accuracy,
)
from src.common.config import EVALUATION_DIR, PROCESSED_DIR
from src.common.csv_io import read_excel_csv

DIR = PROCESSED_DIR / "llm_review"
FIELDS = {"sentiment": norm_sentiment, "category": norm_category}


def _read(name: str) -> pd.DataFrame:
    return read_excel_csv(DIR / name, dtype=str).set_index("sample_id")


def _special(v: object) -> bool:
    return is_unclear(v) or is_irrelevant(v)


def invalid_labels(sheets: dict[str, pd.DataFrame]) -> list[str]:
    """Cells that would silently drop out of every metric: blank, or not a known code/alias/special value."""
    bad = []
    for reviewer, df in sheets.items():
        for field, norm in FIELDS.items():
            for sid, v in df[f"human_{field}"].items():
                if not isinstance(v, str) or not v.strip():
                    bad.append(f"review_{reviewer}.csv {sid} human_{field} is blank")
                elif not _special(v) and norm(v) is None:
                    bad.append(f"review_{reviewer}.csv {sid} human_{field}={v!r}")
    return bad


def per_reviewer_section(a: pd.DataFrame, b: pd.DataFrame, llm: dict[str, pd.Series],
                         weights: pd.Series | None) -> list[str]:
    """LLM accuracy against each reviewer separately. Needs no adjudication and is not biased toward
    easy items the way 'agreed items only' is; the two values bracket the accuracy."""
    lines = ["## 1-1. 검수자별 LLM 정확도 (합의 전, 각 검수자 라벨을 정답으로)", "",
             "| 항목 | 검수자 | n | 표본 정확도 | 모집단 가중 추정 |", "|---|---|---|---|---|"]
    for field, norm in FIELDS.items():
        for name, df in (("A", a), ("B", b)):
            truth = df[f"human_{field}"].map(norm)
            pred = llm[field].reindex(truth.index)
            acc, _ = accuracy_table(truth, pred)
            wacc = weighted_accuracy(truth, pred, weights) if weights is not None else None
            lines.append(f"| {field} | {name} | {int((truth.notna() & pred.notna()).sum())} | {acc} | {wacc} |")
    lines += ["", "판단불가·관련없음은 제외. 두 값의 범위를 LLM 정확도로 보고한다 (검수자 간 기준 차이가 그 폭이다).", ""]
    return lines


def final_labels(a: pd.DataFrame, b: pd.DataFrame, adj: pd.DataFrame | None
                 ) -> tuple[pd.DataFrame, pd.DataFrame, set[str]]:
    """(final labels per item, open disagreements, items finally judged 관련없음). Adjudicated values override.

    관련없음 is decided per item (from human_category): both reviewers -> irrelevant; one reviewer -> a dispute.
    """
    final = pd.DataFrame(index=a.index)
    open_rows = []
    irr_a = a["human_category"].map(is_irrelevant)
    irr_b = b["human_category"].map(is_irrelevant)
    irrelevant = set(a.index[irr_a & irr_b])
    decided_irr: set[str] = set()
    if adj is not None and "final_category" in adj:
        decided_irr = set(adj.index[adj["final_category"].map(is_irrelevant)])
    for sid in a.index[irr_a ^ irr_b]:
        if sid in decided_irr:
            irrelevant.add(sid)
        elif adj is None or "final_category" not in adj or sid not in adj.index or \
                adj.loc[[sid], "final_category"].map(FIELDS["category"]).dropna().empty:
            open_rows.append({"sample_id": sid, "field": "relevance",
                              "reviewer_A": "관련없음" if irr_a[sid] else a.at[sid, "human_category"],
                              "reviewer_B": "관련없음" if irr_b[sid] else b.at[sid, "human_category"]})
    for field, norm in FIELDS.items():
        ha, hb = a[f"human_{field}"].map(norm), b[f"human_{field}"].map(norm)
        final[field] = ha.where(ha == hb)
        if adj is not None and f"final_{field}" in adj:
            decided = adj[f"final_{field}"].map(norm).dropna()
            decided = decided[~decided.index.duplicated(keep="last")]
            final.loc[decided.index.intersection(final.index), field] = decided
        final.loc[list(irrelevant), field] = None
        for sid in final.index[final[field].isna() & ha.notna() & hb.notna()]:
            if sid not in irrelevant:
                open_rows.append({"sample_id": sid, "field": field, "reviewer_A": ha[sid], "reviewer_B": hb[sid]})
    return final, pd.DataFrame(open_rows), irrelevant


def main() -> int:
    try:
        a, b, key = _read("review_A.csv"), _read("review_B.csv"), _read("llm_key.csv")
    except FileNotFoundError as e:
        print(f"missing sheet: {e.filename}. Run make_llm_review_sample.py and fill both sheets.")
        return 1
    adj_path = DIR / "adjudication.csv"
    adj = _read("adjudication.csv") if adj_path.exists() else None
    check = _read("summary_check.csv") if (DIR / "summary_check.csv").exists() else None

    bad = invalid_labels({"A": a, "B": b})
    if bad:
        print("Blank or unrecognised labels — every row needs a value in both label cells:")
        print("\n".join(f"  {x}" for x in bad))
        print("sentiment: 긍정/중립/부정 | category: 규제(정책) / 기술(트렌드) / 시장(경쟁) / 인프라(공급망)\n"
              "special (both cells): 판단불가 = title+summary cannot tell, 관련없음 = not about the firms/industry.\n"
              "Same event as another row: still label it, and write 'S010과 같은 사건' in note.")
        return 1
    final, open_disputes, irrelevant = final_labels(a, b, adj)
    weights = None
    pop_path = DIR / "population_cells.csv"
    if pop_path.exists():
        pop = read_excel_csv(pop_path).set_index(["sentiment", "primary_category"])["population"]
        cell = list(zip(key["sentiment"], key["primary_category"]))
        in_sample = pd.Series(cell, index=key.index).map(pd.Series(cell).value_counts())
        weights = pd.Series([pop.get(c) for c in cell], index=key.index) / in_sample
    lines = [
        "# LLM 분류 표본 검수 결과 (가이드 6-5)",
        "",
        f"- 계산 일시: {datetime.now():%Y-%m-%d %H:%M}",
        f"- prompt_version: {key['prompt_version'].iloc[0]}, 모델: {key['model_version'].iloc[0]}",
        f"- 표본 {len(key)}건 (LLM 감성 × 카테고리 층화, 시드 고정). 검수자 2명이 LLM 결과를 보지 않고 각자 라벨링",
        "",
        "## 1. 검수자 간 일치",
        "",
        "| 항목 | 둘 다 라벨 | 일치율 | Cohen's kappa |",
        "|---|---|---|---|",
    ]
    for field, norm in FIELDS.items():
        ha, hb = a[f"human_{field}"].map(norm), b[f"human_{field}"].map(norm)
        both = ha.notna() & hb.notna()
        rate = round(float((ha[both] == hb[both]).mean()), 3) if both.any() else None
        lines.append(f"| {field} | {int(both.sum())} | {rate} | {cohen_kappa(ha, hb)} |")
    unclear = {f"{r}·{field}": int(df[f"human_{field}"].map(is_unclear).sum())
               for r, df in (("A", a), ("B", b)) for field in FIELDS}
    opened = {r: int(pd.to_numeric(df.get("opened_original"), errors="coerce").fillna(0).sum())
              for r, df in (("A", a), ("B", b))}
    irr = {r: int(df["human_category"].map(is_irrelevant).sum()) for r, df in (("A", a), ("B", b))}
    lines += ["", f"- 관련없음(산업·법인과 무관한 기사로 판단): " + ", ".join(f"{k} {v}건" for k, v in irr.items())
              + f", 최종 {len(irrelevant)}건 — LLM 정확도에서 제외, 뉴스 정제(관련 기사 판정)의 오류로 따로 기록"]
    lines += [f"- 판단불가(제목·요약만으로 판단할 수 없음): " + ", ".join(f"{k} {v}건" for k, v in unclear.items())
              + " — 정확도 계산에서 제외, 입력 범위(제목+요약)의 한계로 리포트에 기록",
              f"- 원문을 열어 판단한 건수: " + ", ".join(f"{k} {v}건" for k, v in opened.items())]
    lines += ["", f"- 합의되지 않은 항목: {len(open_disputes)}건"
              + (" → `adjudication.csv`에 final_sentiment / final_category를 채운 뒤 다시 실행" if len(open_disputes) else ""),
              ""]

    llm = {"sentiment": key["sentiment"], "category": key["primary_category"]}
    lines += per_reviewer_section(a, b, llm, weights)
    lines += ["## 2. LLM 정확도 (최종 사람 라벨 기준)", ""]
    if len(open_disputes):
        lines += [f"> 합의 전이다: 두 검수자가 이미 일치한 항목만 들어 있어 쉬운 항목에 치우친다 (높게 나온다).",
                  "> 인용할 수치는 위 '검수자별 LLM 정확도'의 범위다.", ""]

    wrong = []
    for field in FIELDS:
        acc, per = accuracy_table(final[field], llm[field].reindex(final.index))
        n = int(final[field].notna().sum())
        wacc = weighted_accuracy(final[field], llm[field].reindex(final.index), weights) if weights is not None else None
        lines += [f"### {field}: 표본 정확도 {acc} (n={n}) / 모집단 가중 추정 {wacc}", "",
                  "| 사람 라벨 | n | 맞춤 | 분류별 정확도 |", "|---|---|---|---|",
                  *[f"| {cls} | {int(r.n)} | {int(r.correct)} | {r.accuracy} |" for cls, r in per.iterrows()], ""]
        bad = final.index[final[field].notna() & (final[field] != llm[field].reindex(final.index))]
        wrong += [{"sample_id": s, "field": field, "human": final.at[s, field], "llm": llm[field][s],
                   "title": a.at[s, "title"]} for s in bad]
    if check is not None:
        fact = pd.to_numeric(check["summary_factual"], errors="coerce").dropna()
        share = round(float(fact.mean()), 3) if len(fact) else None
        lines += ["## 3. 이슈 요약 사실 일치", "", f"- 사실과 일치: {share} (n={len(fact)})", ""]
    lines += ["## 4. 해석 시 주의", "",
              "- 측정값만 기록했다. 표본 크기가 작은 분류의 정확도는 오차가 크다 (n을 함께 인용한다).",
              "- 표본은 LLM 감성×카테고리로 층화했다. 전체 기사에 대한 정확도는 '모집단 가중 추정'을 인용한다.",
              "- 오분류 사례(제목 포함)는 로컬 `data/processed/llm_review/misclassified.csv`에만 둔다.", ""]

    if len(open_disputes):
        open_disputes.assign(final_sentiment="", final_category="").to_csv(
            DIR / "adjudication.csv" if adj is None else DIR / "adjudication_open.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(wrong).to_csv(DIR / "misclassified.csv", index=False, encoding="utf-8-sig")
    EVALUATION_DIR.mkdir(exist_ok=True)
    (EVALUATION_DIR / "llm_validation_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
