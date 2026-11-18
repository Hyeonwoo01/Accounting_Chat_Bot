"""Review-sample selection and scoring for LLM results (guide 6-5). Pure functions, no I/O."""

from __future__ import annotations

import pandas as pd

from src.analysis.llm_classifier import CATEGORIES, SENTIMENTS

# Reviewers may type Korean short forms; everything is normalised to the schema codes.
SENTIMENT_ALIASES = {"긍정": "positive", "중립": "neutral", "부정": "negative",
                     "pos": "positive", "neu": "neutral", "neg": "negative"}
CATEGORY_ALIASES = {"규제": "regulation_policy", "정책": "regulation_policy", "기술": "technology_trend",
                    "트렌드": "technology_trend", "시장": "market_competition", "경쟁": "market_competition",
                    "인프라": "supply_chain_infrastructure", "공급망": "supply_chain_infrastructure"}


# "Cannot tell from title + summary": excluded from accuracy, counted separately as an input limit.
UNCLEAR = {"판단불가", "판단 불가", "모름", "?", "unclear", "n/a"}


def is_unclear(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in UNCLEAR


# "Not about the industry/firms at all": a refinement (relevance) error, not an LLM error.
# Excluded from LLM accuracy and reported separately as a precision problem of the news refinement.
IRRELEVANT = {"관련없음", "관련 없음", "무관", "irrelevant"}


def is_irrelevant(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in IRRELEVANT


def normalize(value: object, allowed: tuple[str, ...], aliases: dict[str, str]) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    v = str(value).strip().lower()
    v = aliases.get(v, v)
    return v if v in allowed else None


def norm_sentiment(v: object) -> str | None:
    return normalize(v, SENTIMENTS, SENTIMENT_ALIASES)


def norm_category(v: object) -> str | None:
    return normalize(v, CATEGORIES, CATEGORY_ALIASES)


def stratified_sample(results: pd.DataFrame, n: int = 100, seed: int = 42) -> pd.DataFrame:
    """Spread the sample over sentiment x category cells as evenly as the data allows.

    Equal quota per non-empty cell; cells with fewer rows give their slack to the others;
    any shortfall is filled at random. Deterministic for a given seed.
    """
    cells = {k: g for k, g in results.groupby(["sentiment", "primary_category"])}
    n = min(n, len(results))
    picked: list[pd.DataFrame] = []
    remaining = n
    pending = sorted(cells, key=lambda k: len(cells[k]))  # small cells first so their slack is reused
    for i, key in enumerate(pending):
        quota = remaining // (len(pending) - i)
        take = cells[key].sample(min(quota, len(cells[key])), random_state=seed)
        picked.append(take)
        remaining -= len(take)
    sample = pd.concat(picked) if picked else results.head(0)
    if remaining > 0:
        rest = results.drop(sample.index)
        sample = pd.concat([sample, rest.sample(min(remaining, len(rest)), random_state=seed)])
    return sample.sample(frac=1, random_state=seed)  # shuffle so cells are not grouped for reviewers


def cohen_kappa(a: pd.Series, b: pd.Series) -> float | None:
    """Chance-corrected agreement between two reviewers on the same items."""
    mask = a.notna() & b.notna()
    a, b = a[mask], b[mask]
    if a.empty:
        return None
    po = float((a == b).mean())
    labels = set(a) | set(b)
    pe = sum(float((a == k).mean()) * float((b == k).mean()) for k in labels)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def accuracy_table(truth: pd.Series, pred: pd.Series) -> tuple[float | None, pd.DataFrame]:
    """Overall accuracy and per-class accuracy (share of each TRUE class the LLM got right)."""
    mask = truth.notna() & pred.notna()
    t, p = truth[mask], pred[mask]
    if t.empty:
        return None, pd.DataFrame(columns=["n", "correct", "accuracy"])
    per = pd.DataFrame({"t": t, "ok": t == p}).groupby("t")["ok"].agg(n="size", correct="sum")
    per["accuracy"] = (per["correct"] / per["n"]).round(3)
    return round(float((t == p).mean()), 3), per


def weighted_accuracy(truth: pd.Series, pred: pd.Series, weight: pd.Series) -> float | None:
    """Accuracy re-weighted to the population: each item counts by (population cell size / sample cell size).

    The stratified sample over-represents rare cells, so the plain sample accuracy is not the
    accuracy over all classified articles.
    """
    mask = truth.notna() & pred.notna() & weight.notna()
    if not mask.any():
        return None
    w = weight[mask].astype(float)
    return round(float((w * (truth[mask] == pred[mask])).sum() / w.sum()), 3)

