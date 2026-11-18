"""Big4 전문서비스 산업 이슈 대시보드 — entry point.

Run from the project root:  streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

# Explicit navigation so the menu shows our own names (file names would give "app", "DART QA").
PAGES = Path(__file__).parent / "pages"
st.navigation([
    st.Page(Path(__file__).parent / "home.py", title="App", default=True),
    st.Page(PAGES / "01_Industry_Overview.py", title="Industry Overview", url_path="Industry_Overview"),
    st.Page(PAGES / "02_Issue_Analysis.py", title="Issue Analysis", url_path="Issue_Analysis"),
    st.Page(PAGES / "03_Big4_Comparison.py", title="Big4 Comparison", url_path="Big4_Comparison"),
    st.Page(PAGES / "04_QA.py", title="QA", url_path="QA"),
]).run()
