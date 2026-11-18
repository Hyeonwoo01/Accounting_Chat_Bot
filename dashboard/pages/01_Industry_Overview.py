"""01 Industry Overview: conclusions with evidence, volume and spikes, categories, sentiment, filings."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import theme, charts, conclusions, data, filters  # noqa: E402

st.set_page_config(page_title="Industry Overview", layout="wide")
theme.apply()
theme.eyebrow("Dashboard · Overview")
st.title("Industry Overview")
f = filters.sidebar()
news, dart, spikes = filters.filtered("news"), filters.filtered("dart"), filters.filtered("spikes")
cats, firms = data.category_labels(), data.firm_labels()
val = data.settings()["dashboard"]["validation"]

# ---------------------------------------------------------------- conclusions (insights table)
conclusions.show()

# ---------------------------------------------------------------- volume + spikes
st.subheader("주간 기사량과 급증 시점")
if news.empty:
    st.info("선택한 조건에 맞는 기사가 없습니다.")
else:
    # Weeks with no article must be plotted as 0: spike detection (eda.weekly_counts) counts them as 0,
    # and a line that skips them makes quiet stretches look higher than they were.
    first = pd.Timestamp(f.start).to_period("W-SUN").start_time
    all_weeks = pd.date_range(first, pd.Timestamp(f.end), freq="W-MON", name="week")
    weekly = news.groupby("week").size().reindex(all_weeks, fill_value=0).rename("count").reset_index()
    fig = go.Figure(go.Scatter(x=weekly["week"], y=weekly["count"], mode="lines", name="기사 수",
                               line=dict(color=charts.VOLUME_LINE_COLOR)))
    if not spikes.empty:
        # Marker sits ON the plotted line (filtered count); the spike's own count/threshold go in the hover.
        # Display only: weeks barely over the threshold are drawn hollow, so a small rise after a quiet
        # stretch (e.g. 14 vs 11.8) does not read like a major peak. The detection rule is unchanged.
        sp = spikes.merge(weekly.rename(columns={"count": "shown"}), on="week", how="inner")
        sp["label"] = sp["spike_type"].fillna("배경 미기록")
        sp["ratio"] = sp["count"] / sp["threshold"].where(sp["threshold"] > 0)
        clear = sp["ratio"].isna() | (sp["ratio"] >= charts.CLEAR_SPIKE_RATIO)
        for mask, name, marker in [
            (clear, f"급증 (기준의 {charts.CLEAR_SPIKE_RATIO:g}배 이상)",
             dict(size=11, color=charts.SPIKE_COLOR, line=dict(width=2, color="white"))),
            (~clear, "급증 (기준 근처)",
             dict(size=9, color="rgba(0,0,0,0)", line=dict(width=2, color=charts.SPIKE_COLOR))),
        ]:
            part = sp[mask]
            if part.empty:
                continue
            fig.add_trace(go.Scatter(
                x=part["week"], y=part["shown"], mode="markers", name=name, marker=marker,
                customdata=part[["label", "count", "threshold", "ratio"]].to_numpy(),
                hovertemplate="%{x|%Y-%m-%d} 주<br>급증 판정: %{customdata[1]}건 (기준 %{customdata[2]}, "
                              "%{customdata[3]:.2f}배)<br>%{customdata[0]}<extra></extra>"))
    fig = charts.style(fig, "기사 수(건)")
    fig.update_traces(selector=dict(name="기사 수"), line=dict(width=charts.VOLUME_LINE_WIDTH))  # after style() (it sets 2)
    st.plotly_chart(fig, width="stretch")
    scope_note = "전체" if f.every_firm else ", ".join(firms[c] for c in f.firms) + " 각각의"
    st.caption(filters.describe(len(news), "주(월요일 시작, 기사 없는 주는 0)")
               + f" · 급증 = 직전 8주 평균 + 2σ 초과 ({scope_note} 관련 기사 기준, 카테고리 필터와 무관)")
    st.caption("급증은 '많은 주'가 아니라 '직전 8주보다 갑자기 늘어난 주'입니다. 조용하던 시기의 작은 증가도 급증이 되고, "
               "큰 주가 연달아 오면 앞의 큰 주가 기준을 올려 뒤의 주는 급증이 아닐 수 있습니다. "
               f"속이 빈 점은 기준을 {charts.CLEAR_SPIKE_RATIO:g}배 미만으로 넘은 주입니다(표시 구분일 뿐 판정 규칙은 같음). "
               "점에 마우스를 올리면 기준값이 보입니다.")

# ---------------------------------------------------------------- categories + sentiment
left, right = st.columns(2)
labeled = news[news["category"].notna()]
with left:
    st.subheader("카테고리 분포")
    if labeled.empty:
        st.info("분류된 기사가 없습니다.")
    else:
        cnt = (labeled["category"].value_counts(ascending=True)  # largest bar on top
               .rename_axis("category").reset_index(name="count"))
        cnt["label"] = cnt["category"].map(cats)
        fig = px.bar(cnt, x="count", y="label", orientation="h", color="category",
                     color_discrete_map=charts.CATEGORY_COLORS, labels={"count": "기사 수", "label": ""})
        fig.update_layout(showlegend=False)
        st.plotly_chart(charts.style(fig, height=280), width="stretch")
        st.caption(filters.describe(len(labeled), "기간 전체 합계"))
with right:
    st.subheader("월별 감성 비중")
    if labeled.empty:
        st.info("분류된 기사가 없습니다.")
    else:
        m = labeled.groupby(["month", "sentiment"]).size().rename("n").reset_index()
        m["share"] = m["n"] / m.groupby("month")["n"].transform("sum")
        m["label"] = m["sentiment"].map(charts.SENTIMENT_LABELS)
        fig = px.bar(m, x="month", y="share", color="label", barmode="stack",
                     color_discrete_map={charts.SENTIMENT_LABELS[k]: v for k, v in charts.SENTIMENT_COLORS.items()},
                     category_orders={"label": ["긍정", "중립", "부정"]}, labels={"share": "비중", "month": ""})
        fig.update_yaxes(tickformat=".0%")
        fig = charts.style(fig, height=280).update_traces(marker_line_width=0)  # thin monthly bars: no outline
        st.plotly_chart(fig, width="stretch")
        st.caption(filters.describe(len(labeled), "월") + " · LLM 분류 결과이며 표본 검수 정확도는 "
                   + filters.md(f"{val['sentiment_accuracy']} ({val['basis']})"))
        st.caption("기사 논조(사업 환경에 우호적인지) 기준이며 법인에 대한 평가가 아님 — 법인이 감사인·협력사로만 등장한 기사도 기사 논조를 따르고, 같은 사건의 반복 보도도 각각 셈")

# ---------------------------------------------------------------- filings
st.subheader("공시 추이")
own, client = dart[dart["filing_role"] == "own"], dart[dart["filing_role"] == "audit_client"]
left, right = st.columns(2)
with left:
    st.markdown("**법인 자체 공시 (회계법인사업보고서 등) 타임라인**")
    if own.empty:
        st.info("확인 자료 없음")
    else:
        t = own.assign(firm=own["company_id"].map(firms))
        fig = px.scatter(t, x="rcept_dt", y="firm", color="company_id", color_discrete_map=charts.FIRM_TIMELINE_PASTEL,
                         hover_data={"report_nm": True, "company_id": False}, labels={"rcept_dt": "", "firm": ""})
        fig.update_traces(marker=dict(size=11, line=dict(width=0.8, color=charts.BAR_OUTLINE)))  # pastel dots need an edge
        fig.update_layout(showlegend=False)
        st.plotly_chart(charts.style(fig, height=260), width="stretch")
        st.caption(filters.describe(len(own), "공시 1건 = 점 1개") + " · 건수가 적어 추이선 대신 타임라인으로 표시")
with right:
    st.markdown("**Big4가 제출한 고객사 감사보고서 (월별 건수)**")
    if client.empty:
        st.info("확인 자료 없음")
    else:
        mc = client.groupby(client["rcept_dt"].dt.to_period("M").dt.start_time).size().rename("count").reset_index()
        fig = px.bar(mc, x="rcept_dt", y="count", labels={"rcept_dt": "", "count": "제출 건수"},
                     color_discrete_sequence=[charts.AUDIT_MONTHLY_COLOR])
        st.plotly_chart(charts.style(fig, "제출 건수", height=260), width="stretch")
        st.caption(filters.describe(len(client), "월") + " · 제출 건수 기준(매출 점유율 아님), "
                   "3–5월 집중은 결산 일정에 따른 계절성 · 고객사의 공시이며 Big4의 실적이 아님")

# ---------------------------------------------------------------- validation (collapsed)
with st.expander("LLM 분류 검수 정확도와 처리 범위"):
    val = {k: filters.md(v) for k, v in val.items()}
    st.markdown(
        f"""
- 처리 범위: {val['scope']}
- 감성 정확도 **{val['sentiment_accuracy']}**, 카테고리 정확도 **{val['category_accuracy']}** — {val['basis']}
- 검수자 간 일치: {val['reviewer_agreement']}
- 이슈 요약 사실 일치: {val['summary_factual']}
- 상세: `evaluation/evaluation_report.md`, `evaluation/llm_validation_report.md`
"""
    )
