"""Visual theme shared by every page: warm cream canvas, ink text, pill controls, thin orange arcs
in the background, and the 함초롬바탕 serif.

Font: 함초롬바탕 (HCR Batang) is used when installed on the viewer's PC (it ships with Hancom Office).
It is not bundled in the repository; Noto Serif KR (Google Fonts) is the fallback, then any serif.
Colors follow a warm editorial style (no brand logo colors): canvas #F3F0EE, lifted #FCFBFA,
ink #141413, muted #696969, accent orange #CF4500 (eyebrow dot) / #F37338 (background arcs).
"""

from __future__ import annotations

import re

import streamlit as st

FONT_STACK = "'HCR Batang', '함초롬바탕', 'HCRBatang', 'Noto Serif KR', serif"
INK, MUTED, CANVAS, LIFTED = "#141413", "#696969", "#F3F0EE", "#FCFBFA"
ORANGE, ARC = "#CF4500", "#F37338"
# The background-arc orange as it appears on the canvas (ARC #F37338 at 55%). Used for the filter-chip
# outline (chips are white) and the filled QA submit button.
ARC_SOFT = "#F3AB8A"
CHIP, CHIP_BORDER = "#FFFFFF", ARC_SOFT

# Two large thin arcs, fixed to the top-right of the canvas (decoration only, behind content).
_ARCS = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='1400' height='1400' viewBox='0 0 1400 1400'>"
    "<g fill='none' stroke='%23F37338' stroke-width='1.2' opacity='0.55'>"
    "<circle cx='900' cy='420' r='520'/><circle cx='1000' cy='760' r='430'/>"
    "</g></svg>"
)

_CSS = f"""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
html, body, .stApp, [data-testid="stSidebar"], .stMarkdown, p, li, label, input, textarea, button,
[data-baseweb="select"], [data-testid="stMetric"], [data-testid="stCaptionContainer"], .stDataFrame {{
    font-family: {FONT_STACK} !important;
}}
.stApp {{
    background-color: {CANVAS};
    background-image: url("{_ARCS}");
    background-repeat: no-repeat;
    background-position: right -320px top -260px;
    background-size: 1400px 1400px;
    background-attachment: fixed;
}}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{ background-color: {LIFTED}; border-right: 1px solid rgba(20,20,19,0.08); }}
h1, h2, h3 {{ font-family: {FONT_STACK} !important; font-weight: 500 !important; letter-spacing: -0.02em; color: {INK}; }}
h1 {{ font-size: 2.6rem !important; line-height: 1.1 !important; }}
[data-testid="stCaptionContainer"], .stCaption {{ color: {MUTED} !important; }}

/* eyebrow label: tiny orange dot + uppercase tracking */
.eyebrow {{ font-size: 0.82rem; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase;
           color: {INK}; margin: 0 0 -0.4rem 0; }}
.eyebrow::before {{ content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%;
                    background: {ORANGE}; margin-right: 10px; vertical-align: middle; }}

/* pill buttons: soft-orange primary (QA submit), outlined secondary */
.stButton > button, .stFormSubmitButton > button, .stDownloadButton > button {{
    border-radius: 20px !important; border: 1.5px solid {INK} !important; padding: 6px 24px !important;
    font-weight: 500 !important; letter-spacing: -0.02em;
}}
.stButton > button {{ background: #FFFFFF !important; color: {INK} !important; }}
.stFormSubmitButton > button, .stButton > button[kind="primary"] {{ background: {ARC_SOFT} !important;
    border-color: {ARC_SOFT} !important; color: {INK} !important; }}   /* filled soft orange, ink text for contrast */

/* inputs and chips as pills */
[data-baseweb="select"] > div, [data-baseweb="input"], .stTextArea textarea, .stDateInput > div > div {{
    border-radius: 20px !important;
}}
/* filter chips: [data-tag] in newer Streamlit, [data-baseweb="tag"] in older */
[data-tag], [data-baseweb="tag"] {{ border-radius: 999px !important; background-color: {CHIP} !important;
                                    border: 1.5px solid {CHIP_BORDER} !important; color: {INK} !important; }}
[data-tag] span, [data-tag] svg, [data-baseweb="tag"] span, [data-baseweb="tag"] svg {{
    color: {INK} !important; fill: {INK} !important; }}

/* rounded surfaces, borders instead of shadows */
[data-testid="stVerticalBlockBorderWrapper"], [data-testid="stForm"], [data-testid="stExpander"] details,
[data-testid="stAlert"], .stDataFrame, [data-testid="stPlotlyChart"] {{
    border-radius: 24px !important;
}}
[data-testid="stForm"], [data-testid="stVerticalBlockBorderWrapper"] {{ background: {LIFTED}; }}
/* newer Streamlit has no border wrapper testid: style keyed cards directly (solid, so arcs never cross text) */
.st-key-conclusions, .st-key-issue_summary, .st-key-spike_reps {{ background: {CANVAS}; border-radius: 24px !important; padding: 20px 24px !important; }}
/* conclusion text outside parentheses, ~1.5x stroke. A stroke instead of font-weight 600: 함초롬바탕 has only
   400/700, so 600 would render as full bold on PCs that have it. */
.concl-strong {{ -webkit-text-stroke: 0.35px currentColor; }}
[data-testid="stAlert"] > div {{ background-color: {LIFTED} !important; color: {INK} !important;
                                border: 1px solid rgba(20,20,19,0.12); border-radius: 24px; }}
[data-testid="stAlert"] p {{ color: {INK} !important; }}
[data-testid="stPlotlyChart"] {{ background: {LIFTED}; border: 1px solid rgba(20,20,19,0.08); overflow: hidden; }}
/* no padding here: Streamlit sizes the figure to this box, so padding would push the legend past the edge */
.stDataFrame {{ background: {LIFTED}; }}
[data-testid="stMetric"] {{ background: {LIFTED}; border: 1px solid rgba(20,20,19,0.08); border-radius: 24px;
                           padding: 16px 24px; }}
blockquote {{ border-left: 3px solid {ARC} !important; }}
a {{ color: #3860BE; }}
</style>
"""


def _compact(html: str) -> str:
    """Drop CSS comments and blank lines. Streamlit renders this through markdown, where an HTML block
    that starts with <link> ends at the first blank line and the rest would show up as page text."""
    html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
    return "\n".join(line for line in html.splitlines() if line.strip())


def apply() -> None:
    """Call right after st.set_page_config on every page."""
    st.markdown(_compact(_CSS), unsafe_allow_html=True)


def eyebrow(text: str) -> None:
    """Small section label with an orange dot, placed above a page title."""
    st.markdown(f"<p class='eyebrow'>{text}</p>", unsafe_allow_html=True)
