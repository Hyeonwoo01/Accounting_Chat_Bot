"""03 Big4 Comparison: per-firm news, own filings, audit-report filing counts, filing lists."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from dashboard import theme, charts, data, filters  # noqa: E402

st.set_page_config(page_title="Big4 Comparison", layout="wide")
theme.apply()
theme.eyebrow("Dashboard · Comparison")
st.title("Big4 Comparison")
f = filters.sidebar()
news, dart, audit = filters.filtered("news"), filters.filtered("dart"), filters.filtered("audit_monthly")
firms, cats = data.firm_labels(), data.category_labels()
chosen = [c for c in firms if f.every_firm or c in f.firms]
firm_colors = {firms[k]: v for k, v in charts.FIRM_COLORS.items()}
firm_order = {"firm": [firms[k] for k in charts.FIRM_COLORS]}
cat_order = {"label": list(cats.values())}
st.caption("기사량은 보도 노출량이지 경쟁력 순위가 아닙니다. 법인별 수치는 한국 회원 법인을 다룬 기사만 셉니다 (글로벌 브랜드 발표 제외).")

# member-firm articles, one row per (article, firm)
per_firm = news.explode("kr_company_ids").dropna(subset=["kr_company_ids"])
per_firm = per_firm[per_firm["kr_company_ids"].isin(chosen)].rename(columns={"kr_company_ids": "firm_id"})
per_firm["firm"] = per_firm["firm_id"].map(firms)

# ---------------------------------------------------------------- volume
st.subheader("법인별 월간 기사량")
if per_firm.empty:
    st.info("확인 자료 없음")
else:
    m = per_firm.groupby(["month", "firm"]).size().rename("count").reset_index()
    fig = px.line(m, x="month", y="count", color="firm", markers=True, color_discrete_map=firm_colors, category_orders=firm_order,
                  labels={"month": "", "count": "기사 수", "firm": ""})
    fig.update_traces(marker=dict(size=8))
    charts.hover_highlight(charts.style(fig, "기사 수(건)"))  # lines pale until the pointer is on one
    st.caption(filters.describe(per_firm["doc_id"].nunique(), "월") + " · 여러 법인을 함께 다룬 기사는 각 법인에 1건씩 셈")

# ---------------------------------------------------------------- category mix + sentiment (single-firm)
left, right = st.columns(2)
lab = per_firm[per_firm["category"].notna()]
with left:
    st.subheader("법인별 카테고리 구성")
    if lab.empty:
        st.info("확인 자료 없음")
    else:
        c = lab.groupby(["firm", "category"]).size().rename("n").reset_index()
        c["share"] = c["n"] / c.groupby("firm")["n"].transform("sum")
        c["label"] = c["category"].map(cats)
        fig = px.bar(c, x="share", y="firm", color="label", orientation="h", barmode="stack",
                     color_discrete_map={cats[k]: v for k, v in charts.CATEGORY_COLORS.items()},
                     category_orders={**cat_order, **firm_order}, labels={"share": "비중", "firm": "", "label": ""})
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(charts.style(fig, height=300).update_traces(marker_line_width=0), width="stretch")
        st.caption(filters.describe(len(lab), "법인별 기간 합계 비중", "건(기사×법인)"))
with right:
    st.subheader("법인별 감성 비중 (단일 법인 기사)")
    # guide 6-3: compare sentiment only on articles about ONE firm
    single = lab[lab["doc_id"].isin(news.loc[news["kr_company_ids"].map(len) == 1, "doc_id"])]
    if single.empty:
        st.info("확인 자료 없음")
    else:
        s = single.groupby(["firm", "sentiment"]).size().rename("n").reset_index()
        s["share"] = s["n"] / s.groupby("firm")["n"].transform("sum")
        s["label"] = s["sentiment"].map(charts.SENTIMENT_LABELS)
        fig = px.bar(s, x="share", y="firm", color="label", orientation="h", barmode="stack",
                     color_discrete_map={charts.SENTIMENT_LABELS[k]: v for k, v in charts.SENTIMENT_COLORS.items()},
                     category_orders={"label": ["긍정", "중립", "부정"], **firm_order},
                     labels={"share": "비중", "firm": "", "label": ""})
        fig.update_xaxes(tickformat=".0%")
        st.plotly_chart(charts.style(fig, height=300).update_traces(marker_line_width=0), width="stretch")
        val = data.settings()["dashboard"]["validation"]
        st.caption(filters.describe(len(single), "법인별 기간 합계 비중") + " · 한 법인만 다룬 기사만 사용 · "
                   f"LLM 분류 결과이며 표본 검수 정확도는 {filters.md(val['sentiment_accuracy'])} · 감성은 보조 지표")
        st.caption("기사 논조(사업 환경에 우호적인지) 기준이며 법인에 대한 평가가 아님 — 법인이 감사인·협력사로만 등장한 기사도 기사 논조를 따르고, 같은 사건의 반복 보도도 각각 셈")

# ---------------------------------------------------------------- audit-report filing counts (all filers)
st.subheader("감사보고서 제출 건수 (연도별, 1–10월)")
if audit.empty:
    st.info("확인 자료 없음 — `scripts/load_dashboard_tables.py` 실행 필요")
else:
    a = audit[audit["month_start"].dt.month <= 10].assign(year=lambda d: d["month_start"].dt.year)
    yearly = a.groupby(["year", "filer"])["count"].sum().unstack(fill_value=0)
    total = yearly.sum(axis=1)
    big4 = [c for c in chosen if c in yearly.columns]
    long = yearly[big4].stack().rename("count").reset_index()
    long["firm"] = long["filer"].map(firms)
    long["share"] = long.apply(lambda r: r["count"] / total[r["year"]] if total[r["year"]] else None, axis=1)
    fig = px.bar(long, x="year", y="count", color="firm", barmode="group", category_orders=firm_order,
                 color_discrete_map={firms[k]: v for k, v in charts.FIRM_PASTEL.items()},
                 hover_data={"share": ":.1%"}, labels={"year": "", "count": "제출 건수", "firm": ""})
    fig.update_xaxes(type="category")
    st.plotly_chart(charts.style(fig, "제출 건수(건)"), width="stretch")
    share = (yearly[big4].div(total, axis=0) * 100).round(1).rename(columns=firms)
    share["전체 제출 건수"] = total
    share.index.name = "연도"
    st.dataframe(share, width="stretch")
    st.caption(filters.describe(int(a["count"].sum()), "연도(각 연도 1–10월만 합산해 같은 기간끼리 비교)")
               + " · 비중(%) = 법인 제출 건수 / 전체 제출 건수(비Big4 포함) · 제출 건수 기준(매출 점유율 아님), "
               "3–5월 집중은 결산 일정에 따른 계절성")

# ---------------------------------------------------------------- filing lists
st.subheader("공시 목록")
link = st.column_config.LinkColumn("DART", display_text="원문 보기")
own, client = dart[dart["filing_role"] == "own"], dart[dart["filing_role"] == "audit_client"]
tab_own, tab_client = st.tabs([f"법인 자체 공시 ({len(own)})", f"Big4가 제출한 고객사 감사보고서 ({len(client)})"])
with tab_own:
    if own.empty:
        st.info("확인 자료 없음")
    else:
        st.dataframe(own.assign(법인=own["company_id"].map(firms), 제출일=own["rcept_dt"].dt.date)
                     [["법인", "report_nm", "제출일", "rcept_no", "url"]].rename(columns={"report_nm": "공시명", "rcept_no": "접수번호"})
                     .sort_values("제출일", ascending=False),
                     hide_index=True, width="stretch", column_config={"url": link})
        st.caption("사업보고서의 수치(사업부문별 매출·인력·감사실적)는 자동 추출하지 않았습니다. 원문 링크나 QA 페이지로 확인하세요.")
with tab_client:
    if client.empty:
        st.info("확인 자료 없음")
    else:
        st.dataframe(client.assign(감사인=client["company_id"].map(firms), 제출일=client["rcept_dt"].dt.date,
                                   고객사=client["client_name"].fillna("(이름 미확인)"),
                                   원문=client["has_text"].map({True: "수집", False: "목록만"}))
                     [["고객사", "감사인", "report_nm", "제출일", "원문", "rcept_no", "url"]]
                     .rename(columns={"report_nm": "공시명", "rcept_no": "접수번호"}).sort_values("제출일", ascending=False),
                     hide_index=True, width="stretch", column_config={"url": link})
        st.caption("감사보고서는 **감사받은 고객사의 공시**입니다. 내용을 감사인(Big4)의 실적으로 해석하지 않습니다.")
