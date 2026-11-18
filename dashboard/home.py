"""App (home) page: overview counts, table status, and how to read the dashboard."""

from __future__ import annotations

import streamlit as st

from dashboard import theme, conclusions, data, filters  # noqa: E402

st.set_page_config(page_title="Big4 산업 이슈 대시보드", layout="wide")
theme.apply()
theme.eyebrow("Big4 Professional Services")
st.title("Big4 전문서비스 산업 이슈 대시보드")
st.markdown("> **이 산업에서 지금 무슨 일이 일어나고 있으며, 그 판단의 근거는 무엇인가.**")

if not data.db_ready():
    st.error("DB가 없습니다. `scripts/process_data.py`를 먼저 실행하세요.")
    st.stop()

filters.sidebar()
news, dart, spikes = data.news(), data.dart(), data.spikes()

conclusions.show()  # the decision-oriented conclusions come first (same card as Industry Overview)

c1, c2, c3, c4 = st.columns(4)
c1.metric("관련 뉴스 (정제본)", f"{len(news):,}")
c2.metric("LLM 분류된 뉴스", f"{news['category'].notna().sum():,}")
c3.metric("법인 자체 공시", f"{(dart['filing_role'] == 'own').sum():,}")
c4.metric("Big4 제출 감사보고서", f"{(dart['filing_role'] == 'audit_client').sum():,}")

missing = [name for name, empty in [("news_firm_scope", news["company_scope"].eq("untagged").all() and len(news) > 0),
                                     ("news_spikes", spikes.empty), ("audit_filings_monthly", data.audit_monthly().empty)]
           if empty]
if missing:
    st.warning(f"대시보드 테이블이 비어 있습니다: {', '.join(missing)} → `python scripts/load_dashboard_tables.py` 실행")

st.markdown(
    """
| 페이지 | 내용 |
|---|---|
| **Industry Overview** | 결론, 월별 기사량과 급증 시점, 카테고리·감성 분포, 공시 추이, 검수 정확도 |
| **Issue Analysis** | 카테고리별 이슈 키워드와 추이, 급증 주의 배경과 대표 기사, 뉴스·공시 시점 비교 |
| **Big4 Comparison** | 법인별 기사량·카테고리·감성, 자체 공시, 감사보고서 제출 건수, 공시 목록 |
| **QA** | 공시 원문을 근거로 한 질의응답 (근거가 없으면 거절) |

- **뉴스는 변화 탐색, 공시는 사실 확인**에 씁니다. 보도자료를 공시로 표시하지 않습니다.
- 법인별 수치는 **한국 회원 법인** 기사만 셉니다. 글로벌 브랜드 발표는 한국 법인의 실적으로 보지 않습니다.
- Big4가 제출한 **감사보고서는 고객사의 정보**입니다. Big4의 실적으로 해석하지 않습니다.
- 뉴스 출처: 네이버 검색 API (프로젝트 기간 한정 사용, 제목·링크만 표시). 공시 출처: DART OpenAPI.
"""
)
