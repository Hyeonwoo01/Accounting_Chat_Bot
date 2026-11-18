"""02 Issue Analysis: category keywords and trends, spike weeks with background, news vs filings timing."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import theme, charts, data, filters  # noqa: E402

st.set_page_config(page_title="Issue Analysis", layout="wide")
theme.apply()
theme.eyebrow("Dashboard · Issues")
st.title("Issue Analysis")
f = filters.sidebar()
news, dart, spikes = filters.filtered("news"), filters.filtered("dart"), filters.filtered("spikes")
cats, firms = data.category_labels(), data.firm_labels()
labeled = news[news["category"].notna()]

# ---------------------------------------------------------------- per-quarter issue summary (stored, reviewed)
st.subheader("기간별 핵심 이슈")
summaries = [r for r in data.issue_summaries()
             if r["period_end"] >= f"{f.start:%Y-%m-%d}" and r["period_start"] <= f"{f.end:%Y-%m-%d}"]
if not summaries:
    st.info("검수된 기간별 요약이 없습니다. `scripts/draft_issue_summaries.py`로 초안을 만들고 두 사람이 검수한 뒤 "
            "`scripts/load_issue_summaries.py`로 적재합니다.")
else:
    labels = {r["period"]: r for r in summaries}
    pick = st.selectbox("분기", list(labels), index=len(labels) - 1,
                        format_func=lambda k: f"{k} ({labels[k]['period_start']} – {labels[k]['period_end']})")
    r = labels[pick]
    with st.container(border=True, key="issue_summary"):
        st.markdown(f"**{filters.md(r['headline'])}**")
        for i, issue in enumerate(r["issues"], 1):
            st.markdown(f"{i}. {filters.md(issue['text'])}")
        every = data.news().set_index("doc_id")
        with st.expander("근거 기사"):
            for i, issue in enumerate(r["issues"], 1):
                links = [f"[{filters.md(every.loc[d, 'title'])}]({every.loc[d, 'url']})"
                         for d in issue["evidence_doc_ids"] if d in every.index]
                st.markdown(f"**{i}.** " + (" · ".join(links) if links else "연결된 근거 없음"))
    st.caption(f"관련 기사 {r['article_count'] or '?'}건 중 기사가 많은 사건을 LLM(`{r['model_version']}`, "
               f"`{r['prompt_version']}`)이 요약하고 두 사람({filters.md(r['reviewed_by'])})이 근거 기사와 대조해 승인한 문장 · "
               "LLM에는 기사 제목이 아니라 저장된 분류 결과와 사람이 쓴 급증 배경만 입력 · 사이드바의 기간만 적용(기업·카테고리 필터와 무관)")

# ---------------------------------------------------------------- category trend
st.subheader("카테고리별 월간 기사량")
if labeled.empty:
    st.info("선택한 조건에 맞는 분류된 기사가 없습니다.")
else:
    m = labeled.groupby(["month", "category"]).size().rename("count").reset_index()
    m["label"] = m["category"].map(cats)
    fig = px.line(m, x="month", y="count", color="label", markers=True,
                  color_discrete_map={cats[k]: v for k, v in charts.CATEGORY_LINE_COLORS.items()},
                  category_orders={"label": list(cats.values())}, labels={"month": "", "count": "기사 수", "label": ""})
    fig.update_traces(marker=dict(size=8))
    charts.hover_highlight(charts.style(fig, "기사 수(건)"))  # lines pale until the pointer is on one
    st.caption(filters.describe(len(labeled), "월") + " · 카테고리는 LLM 분류 결과 (정확도는 Industry Overview 페이지 하단)")

# ---------------------------------------------------------------- keywords
st.subheader("카테고리별 이슈 키워드")
if labeled.empty:
    st.info("분류된 기사가 없습니다.")
else:
    present = [c for c in cats if c in set(labeled["category"])]
    cat = st.radio("카테고리", present, format_func=cats.get, horizontal=True)
    kw = (labeled[labeled["category"] == cat].explode("keywords").dropna(subset=["keywords"])
          .groupby("keywords")["dup_group_id"].nunique().sort_values(ascending=False).head(15)
          .rename("events").reset_index())
    if kw.empty:
        st.info("키워드가 없습니다.")
    else:
        fig = px.bar(kw.iloc[::-1], x="events", y="keywords", orientation="h", labels={"events": "사건 수", "keywords": ""})
        # shades of the category's color family, darkest for the top keyword (bars are drawn bottom-up)
        fig.update_traces(marker_color=charts.shades(charts.CATEGORY_FAMILIES[cat], len(kw))[::-1])
        st.plotly_chart(charts.style(fig, height=420), width="stretch")
        n_cat = int((labeled["category"] == cat).sum())
        st.caption(filters.describe(n_cat, "키워드별 사건 수(유사 기사 그룹 기준 — 같은 보도자료의 반복 기사는 1건)")
                   + " · 키워드는 LLM이 제목·요약에서 뽑은 값")

# ---------------------------------------------------------------- spike weeks
st.subheader("급증 시점과 배경")
if spikes.empty:
    st.info("선택한 기간·기업에 급증 주가 없습니다.")
else:
    scope_label = {"all": "전체", **firms}
    table = spikes.assign(범위=spikes["scope"].map(scope_label), 주=spikes["week"].dt.strftime("%Y-%m-%d"),
                          유형=spikes["spike_type"].fillna("배경 미기록"))
    st.dataframe(table[["범위", "주", "count", "threshold", "유형", "leading_firm", "background"]]
                 .rename(columns={"count": "기사 수", "threshold": "기준", "leading_firm": "주 법인", "background": "배경"}),
                 hide_index=True, width="stretch")
    st.caption(filters.describe(len(spikes), "주", "개 주") + " · 배경은 사람이 대표 기사를 읽고 기록한 값 · "
               "업계 공통 사건인지, 한 법인의 보도자료인지 구분해 읽습니다")

    pick = st.selectbox("대표 기사를 볼 주", table.index,
                        format_func=lambda i: f"{table.at[i, '범위']} {table.at[i, '주']} ({table.at[i, '유형']})")
    row = spikes.loc[pick]
    week_news = data.news()
    week_news = week_news[week_news["week"] == row["week"]]
    if row["scope"] != "all":
        week_news = week_news[week_news["kr_company_ids"].map(lambda ids: row["scope"] in ids)]
    reps = (week_news.sort_values("published_at").groupby("dup_group_id")
            .agg(title=("title", "first"), url=("url", "first"), source=("source_name", "first"),
                 n=("doc_id", "size")).sort_values("n", ascending=False).head(8))
    with st.container(border=True, key="spike_reps"):
        if pd.notna(row["background"]):
            st.markdown(f"**배경:** {row['background']}")
        for r in reps.itertuples():
            st.markdown(f"- [{r.title}]({r.url}) · {r.source or ''} · 같은 사건 기사 {r.n}건")
        st.caption("대표 기사 = 유사 기사 그룹마다 1건, 기사가 많은 그룹 순. 제목과 링크만 표시합니다.")

# ---------------------------------------------------------------- news vs filings timing
st.subheader("뉴스와 공시 시점 비교")
if news.empty:
    st.info("선택한 조건에 맞는 기사가 없습니다.")
else:
    monthly = news.groupby(news["published_at"].dt.to_period("M").dt.start_time).size().rename("count").reset_index()
    fig = px.line(monthly, x="published_at", y="count", markers=True, labels={"published_at": "", "count": "기사 수"},
                  color_discrete_sequence=[charts.NEUTRAL_INK])
    own = dart[dart["filing_role"] == "own"]
    for r in own.itertuples():
        fig.add_vline(x=r.rcept_dt, line_width=1, line_dash="dot", line_color=charts.FIRM_COLORS.get(r.company_id, "#888"))
    st.plotly_chart(charts.style(fig, "기사 수(건)"), width="stretch")
    st.caption(filters.describe(len(news), "월") + f" · 점선 = 법인 자체 공시 제출일 ({len(own)}건, 법인별 색)")
    if own.empty:
        st.caption("선택한 조건의 자체 공시: 확인 자료 없음")
