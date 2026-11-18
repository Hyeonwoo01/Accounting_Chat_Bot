"""Shared sidebar filters (guide 9-1). EVERY chart gets its data through `filtered()`.

Filters live in st.session_state under stable keys so they survive page switches. Streamlit
drops a widget's own key when the page that drew it is left, so each widget writes its value to
a separate state key (`f_*`) that pages read and that seeds the widget on the next page.

What each filter means per data set:
- period      news: published_at / DART: rcept_dt / spikes: week / audit counts: month
- firms       news: articles about that KOREAN member firm (kr_company_ids; principle 6). With all
              four firms selected no firm filter is applied, so industry-wide and global-network
              articles stay in. DART: the filing firm. Spikes: the firm's own spike series.
              Audit-report counts: not filtered (shares need every filer as the denominator).
- categories  news only (filings are not classified).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
import streamlit as st

from dashboard import data
from src.common.config import get_period

KINDS = ("news", "dart", "spikes", "audit_monthly")


@dataclass(frozen=True)
class Filters:
    start: date
    end: date
    firms: tuple[str, ...]
    categories: tuple[str, ...]
    all_firms: tuple[str, ...]
    all_categories: tuple[str, ...]

    @property
    def every_firm(self) -> bool:
        return set(self.firms) >= set(self.all_firms) or not self.firms

    @property
    def every_category(self) -> bool:
        return set(self.categories) >= set(self.all_categories) or not self.categories


def _between(s: pd.Series, f: Filters) -> pd.Series:
    return (s >= pd.Timestamp(f.start)) & (s <= pd.Timestamp(f.end))


def apply_filters(df: pd.DataFrame, kind: str, f: Filters) -> pd.DataFrame:
    """Pure filtering (no Streamlit) so it can be tested."""
    if kind not in KINDS:
        raise ValueError(f"unknown data set: {kind}")
    if df.empty:
        return df
    if kind == "news":
        out = df[_between(df["published_at"], f)]
        if not f.every_firm:
            wanted = set(f.firms)
            out = out[out["kr_company_ids"].map(lambda ids: bool(wanted & set(ids)))]
        if not f.every_category:
            out = out[out["category"].isin(f.categories)]
        return out
    if kind == "dart":
        out = df[_between(df["rcept_dt"], f)]
        return out if f.every_firm else out[out["company_id"].isin(f.firms)]
    if kind == "spikes":
        out = df[_between(df["week"], f)]
        return out[out["scope"] == "all"] if f.every_firm else out[out["scope"].isin(f.firms)]
    # audit_monthly: period only (see module docstring)
    month_end = df["month_start"] + pd.offsets.MonthEnd(0)
    return df[(month_end >= pd.Timestamp(f.start)) & (df["month_start"] <= pd.Timestamp(f.end))]


def _persist(widget_key: str, state_key: str) -> None:
    st.session_state[state_key] = st.session_state[widget_key]


def current() -> Filters:
    period = get_period()
    firms, cats = tuple(data.firm_labels()), tuple(data.category_labels())
    ss = st.session_state
    ss.setdefault("f_period", (period.start, period.end))
    ss.setdefault("f_firms", list(firms))
    ss.setdefault("f_categories", list(cats))
    start, end = (tuple(ss["f_period"]) + (period.end,))[:2]
    return Filters(start, end, tuple(ss["f_firms"]), tuple(ss["f_categories"]), firms, cats)


def sidebar() -> Filters:
    """Draw the shared filters in the sidebar and return the current selection."""
    f = current()
    period = get_period()
    firm_names, cat_names = data.firm_labels(), data.category_labels()
    with st.sidebar:
        st.header("필터")
        st.date_input("기간", value=(f.start, f.end), min_value=period.start, max_value=period.end,
                      key="_w_period", on_change=_persist, args=("_w_period", "f_period"))
        st.multiselect("기업 (한국 회원 법인)", list(firm_names), default=list(f.firms),
                       format_func=firm_names.get, key="_w_firms", on_change=_persist, args=("_w_firms", "f_firms"))
        st.multiselect("카테고리 (뉴스)", list(cat_names), default=list(f.categories),
                       format_func=cat_names.get, key="_w_categories", on_change=_persist,
                       args=("_w_categories", "f_categories"))
        st.caption("기업을 모두 고르면 업계 전체·글로벌 기사도 포함됩니다. 일부만 고르면 그 법인의 한국 회원 법인 기사만 셉니다.")
    return current()


def filtered(kind: str) -> pd.DataFrame:
    """The ONE entry point every chart uses: the data set `kind` with the shared filters applied."""
    loaders = {"news": data.news, "dart": data.dart, "spikes": data.spikes, "audit_monthly": data.audit_monthly}
    return apply_filters(loaders[kind](), kind, current())


def describe(n: int, unit: str, what: str = "건") -> str:
    """Chart caption: period, sample size and aggregation unit (guide 9-3)."""
    f = current()
    return f"기간 {f.start:%Y-%m-%d} – {f.end:%Y-%m-%d} · n = {n:,}{what} · 집계 단위: {md(unit)}"


def md(text: object) -> str:
    """Escape '~' for Streamlit markdown: two single tildes on one line render as strikethrough
    (e.g. '1~10월 … 3~5월'). Use for any text that comes from settings or data."""
    return str(text).replace("~", "\\~")
