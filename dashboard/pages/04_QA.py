"""04 QA: grounded answers over collected filings. The ONLY page that calls an LLM,
and only through src.rag.rag_pipeline (guide 7, dashboard/CLAUDE.md)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st  # noqa: E402

from dashboard import theme, data, filters, render  # noqa: E402
from src.common.config import mask_secret  # noqa: E402
from src.rag.rag_pipeline import REFUSAL, load_pipeline  # noqa: E402

st.set_page_config(page_title="QA", layout="wide")
theme.apply()
theme.eyebrow("Dashboard · QA")
st.title("QA")
f = filters.sidebar()
firms = data.firm_labels()
ROLES = {None: "전체", "own": "법인 자체 공시", "audit_client": "Big4가 제출한 고객사 감사보고서"}

st.markdown(
    "수집한 **DART 공시 원문만** 근거로 답합니다. 답변마다 근거 공시(공시명·제출일·접수번호·링크·원문 구절)를 보여주고, "
    "근거를 찾지 못하면 답하지 않습니다. 뉴스·전망·비공개 정보는 답의 근거가 되지 않습니다."
)


@st.cache_resource(show_spinner="검색 인덱스(벡터DB·키워드)를 불러오는 중…")
def pipeline():
    return load_pipeline()


ss = st.session_state
ss.setdefault("qa_firm", f.firms[0] if len(f.firms) == 1 else None)
ss.setdefault("qa_role", None)
ss.setdefault("qa_question", "")

st.markdown("**예시 질문**")
examples = data.settings()["dashboard"]["example_questions"]
cols = st.columns(2)
for i, ex in enumerate(examples):
    if cols[i % 2].button(ex["question"], width="stretch", key=f"ex{i}"):
        ss.qa_question, ss.qa_firm, ss.qa_role = ex["question"], ex.get("company_id"), ex.get("filing_role")
        ss.qa_run = True

with st.form("qa"):
    c1, c2 = st.columns(2)
    firm = c1.selectbox("제출 법인", [None, *firms], index=[None, *firms].index(ss.qa_firm),
                        format_func=lambda k: "전체" if k is None else firms[k])
    role = c2.radio("공시 종류", list(ROLES), index=list(ROLES).index(ss.qa_role), format_func=ROLES.get,
                    horizontal=True)
    question = st.text_area("질문", value=ss.qa_question, height=80,
                            placeholder="예: 삼일회계법인 사업보고서에 기재된 공인회계사 수는?")
    asked = st.form_submit_button("질문하기", type="primary")
    st.caption(f"제출일 범위는 사이드바 기간({f.start:%Y-%m-%d} – {f.end:%Y-%m-%d})을 따릅니다. "
               "법인 자체에 대한 질문은 '법인 자체 공시'를 고르면 고객사 감사보고서가 섞이지 않습니다.")
if asked:
    ss.qa_question, ss.qa_firm, ss.qa_role, ss.qa_run = question, firm, role, True

if ss.pop("qa_run", False) and ss.qa_question.strip():
    try:
        with st.spinner("근거 공시를 찾는 중…"):
            ss.qa_answer = pipeline().ask(ss.qa_question.strip(), company_id=ss.qa_firm, filing_role=ss.qa_role,
                                          date_from=f"{f.start:%Y-%m-%d}", date_to=f"{f.end:%Y-%m-%d}")
    except Exception as e:  # noqa: BLE001 — show a masked, readable error instead of a traceback
        ss.pop("qa_answer", None)
        st.error(f"답변을 만들지 못했습니다: {mask_secret(str(e))[:300]}")

ans = ss.get("qa_answer")
if ans is not None:
    st.divider()
    st.markdown(f"**질문:** {ans.question}")
    if ans.typo_fixed:
        st.info(f"질문의 '{ans.typo_fixed[0]}'을(를) '{ans.typo_fixed[1]}'(으)로 보고 찾았습니다.")
    if ans.replaced_company_id:
        st.info(f"질문에 '{firms.get(ans.auto_company_id, ans.auto_company_id)}'이(가) 있어, 선택한 제출 법인 "
                f"'{firms.get(ans.replaced_company_id, ans.replaced_company_id)}' 대신 "
                f"'{firms.get(ans.auto_company_id, ans.auto_company_id)}'의 공시에서 찾았습니다.")
    elif ans.auto_company_id:
        st.caption(f"질문에서 '{firms.get(ans.auto_company_id, ans.auto_company_id)}'을(를) 인식해 "
                   "이 법인이 제출한 공시에서만 찾았습니다.")
    if ans.refused:
        st.warning(f"**답변 거절** — {REFUSAL}")
        st.caption(f"사유: {ans.reason} · 검색 {ans.retrieved}건, 최고 유사도 "
                   f"{'-' if ans.top_score is None else f'{ans.top_score:.3f}'}")
        if ans.rejected_quotes:
            with st.expander("원문 대조에 실패한 인용 (진단용)"):
                st.caption("모델이 근거로 든 구절이 검색된 청크 원문과 글자 단위로 맞지 않아 표시하지 않았습니다.")
                for q in ans.rejected_quotes:
                    st.code(q, language=None)
    else:
        st.success(ans.answer)
        groups: dict[int, list] = {}
        for c in ans.citations:  # one card per retrieved chunk; all its verified quotes in bold
            groups.setdefault(c.source, []).append(c)
        mode = {"vector": "벡터 검색", "hybrid": "벡터 + 키워드 검색",
                "hybrid_rerank": "벡터 + 키워드 검색, LLM 재정렬"}.get(pipeline().retrieval, pipeline().retrieval)
        st.caption(f"검색 방식: {mode} · 검색 {ans.retrieved}건 · 원문 대조를 통과한 인용 {len(ans.citations)}개 "
                   f"(근거 청크 {len(groups)}개)만 표시")
        for i, cs in enumerate(groups.values(), 1):
            md = cs[0].metadata
            with st.container(border=True):
                st.markdown(f"**[공시 근거 {i}] {md.get('report_nm')}** · 인용 {len(cs)}개")
                if md.get("filing_role") == "audit_client":
                    st.markdown(f"고객사(감사 대상): **{md.get('client_name') or '미확인'}** · 감사인(제출): {md.get('filer')}")
                    st.caption("고객사의 공시입니다. 감사인의 실적이 아닙니다.")
                else:
                    st.markdown(f"제출 법인: {md.get('filer')}")
                st.markdown(f"제출일 {md.get('rcept_dt')} · 접수번호 {md.get('rcept_no')} · "
                            f"[DART 원문]({md.get('source_url')}) · 절: {filters.md(md.get('section_title'))}")
                spans = [c.span for c in cs if c.span]
                if cs[0].chunk_text and spans:
                    st.markdown("> " + render.highlight(cs[0].chunk_text, spans))
                    st.caption("근거 청크 원문 전체 · 굵은 부분 = 모델이 인용하고 원문 대조를 통과한 구절")
                else:
                    for c in cs:
                        st.markdown(f"> {render.escape_md(c.quote)}")
