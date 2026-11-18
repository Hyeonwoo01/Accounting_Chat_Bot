"""Shared dashboard filters: one pure function decides what every chart sees."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

pytest.importorskip("streamlit")
from dashboard.filters import Filters, apply_filters  # noqa: E402

FIRMS = ("pwc", "kpmg", "ey", "deloitte")
CATS = ("regulation_policy", "technology_trend", "market_competition", "supply_chain_infrastructure")


def F(firms=FIRMS, cats=CATS, start=date(2024, 1, 1), end=date(2024, 12, 31)):
    return Filters(start, end, tuple(firms), tuple(cats), FIRMS, CATS)


NEWS = pd.DataFrame({
    "doc_id": ["a", "b", "c", "d"],
    "published_at": pd.to_datetime(["2024-01-05", "2024-03-01", "2024-03-02", "2025-01-01"]),
    "kr_company_ids": [["pwc"], [], ["pwc", "ey"], ["kpmg"]],
    "category": ["market_competition", "regulation_policy", "technology_trend", "market_competition"],
})


def test_news_all_firms_keeps_industry_articles_and_period():
    assert apply_filters(NEWS, "news", F())["doc_id"].tolist() == ["a", "b", "c"]


def test_news_firm_filter_uses_member_firm_ids():
    assert apply_filters(NEWS, "news", F(firms=["ey"]))["doc_id"].tolist() == ["c"]
    assert apply_filters(NEWS, "news", F(firms=["pwc", "kpmg"]))["doc_id"].tolist() == ["a", "c"]


def test_news_category_filter():
    assert apply_filters(NEWS, "news", F(cats=["regulation_policy"]))["doc_id"].tolist() == ["b"]


def test_dart_spikes_and_audit_counts():
    dart = pd.DataFrame({"rcept_dt": pd.to_datetime(["2024-03-01", "2024-03-02"]), "company_id": ["pwc", "ey"]})
    assert apply_filters(dart, "dart", F(firms=["ey"]))["company_id"].tolist() == ["ey"]
    spikes = pd.DataFrame({"week": pd.to_datetime(["2024-01-01"] * 3), "scope": ["all", "pwc", "ey"]})
    assert apply_filters(spikes, "spikes", F())["scope"].tolist() == ["all"]
    assert apply_filters(spikes, "spikes", F(firms=["pwc", "ey"]))["scope"].tolist() == ["pwc", "ey"]
    audit = pd.DataFrame({"month_start": pd.to_datetime(["2023-12-01", "2024-01-01", "2025-01-01"]), "filer": ["x"] * 3})
    assert len(apply_filters(audit, "audit_monthly", F(firms=["pwc"]))) == 1  # firms never filter the denominators


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError):
        apply_filters(NEWS, "insights", F())
