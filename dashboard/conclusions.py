"""Conclusions card (insights table), shown on the App home page and at the top of Industry Overview.

One numbered line per conclusion so it reads at a glance; evidence links are folded below. Only
sentences both reviewers approved are in the table (scripts/load_insights.py).
"""

from __future__ import annotations

import streamlit as st

from dashboard import data, filters, render


def show() -> None:
    st.subheader("결론")
    insights = data.insights()
    if not insights:
        st.info("결론 문장은 아직 작성 전입니다. 근거 문서와 대조해 검토한 결론만 `insights` 테이블에 저장해 표시합니다.")
    else:
        # One line per conclusion so the header reads at a glance; evidence links are folded below.
        with st.container(border=True, key="conclusions"):  # one card (styled in theme.py)
            every_news, every_dart = data.news().set_index("doc_id"), data.dart().set_index("doc_id")
            for i, ins in enumerate(insights, 1):
                # text outside parentheses slightly heavier (theme.py .concl-strong); numbers in parentheses as is
                line = render.emphasize_outside_parens(ins["conclusion_text"])
                st.markdown(f"<b>{i}.</b> {line} <em>({render.html_text(ins['period'])})</em>",
                            unsafe_allow_html=True)
            with st.expander(f"근거 문서 (결론 {len(insights)}개)"):
                for i, ins in enumerate(insights, 1):
                    links = []
                    for doc_id in ins["evidence_doc_ids"]:
                        if doc_id in every_dart.index:
                            r = every_dart.loc[doc_id]
                            links.append(f"[공시] [{filters.md(r['report_nm'])} ({r['rcept_dt']:%Y-%m-%d})]({r['url']})")
                        elif doc_id in every_news.index:
                            r = every_news.loc[doc_id]
                            links.append(f"[뉴스] [{filters.md(r['title'])}]({r['url']})")
                    st.markdown(f"**{i}.** " + (" · ".join(links) if links else "연결된 근거 없음"))
                st.caption("두 사람이 근거 문서와 대조해 검토한 문장만 표시합니다. (해석)은 수치에서 추론한 문장입니다.")
