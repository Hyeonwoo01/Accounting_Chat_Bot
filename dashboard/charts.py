"""Chart styling shared by all pages: fixed entity colors and plain Plotly defaults.

Colors follow the entity, never its rank or the current filter. Category bars and the yearly
audit-report bars use one-hue palettes stepping from dark to light (`PURPLE_TONES`, `BLUE_TONES`).
Pale fills get a thin outline (see `style`) to stay visible on the near-white chart card.
Sentiment (team-chosen, 11-17): positive purple, neutral light gray, negative light pink.
One y-axis per chart; two measures of different scale go in two charts.
"""

from __future__ import annotations

from functools import lru_cache

import plotly.graph_objects as go
import streamlit as st
from plotly.offline import get_plotlyjs

from dashboard.theme import FONT_STACK, LIFTED

FIRM_COLORS = {"pwc": "#2a78d6", "kpmg": "#eb6834", "ey": "#1baf7a", "deloitte": "#eda100"}
# Team-chosen monochromatic palettes (11-17), dark to light, in pastel (OKLCH lightness 0.62-0.90,
# chroma 0.05-0.10; same hues as the source palettes).
# Purple: category bars (01 distribution, 03 per-firm mix). Blue-violet: 03 yearly audit-report counts.
PURPLE_TONES = ["#AE7EB8", "#C598CE", "#DAB6DF", "#EBD0EF"]
BLUE_TONES = ["#797FC2", "#979BD9", "#BCB7E8", "#DBD1F5"]
FIRM_PASTEL = dict(zip(["pwc", "kpmg", "ey", "deloitte"], BLUE_TONES))
CATEGORY_COLORS = dict(zip(["regulation_policy", "technology_trend", "market_competition",
                            "supply_chain_infrastructure"], PURPLE_TONES))
# Category trend lines (02): pastel versions of the earlier hues (pink, green, purple, red).
CATEGORY_LINE_COLORS = {"regulation_policy": "#F2B6CD", "technology_trend": "#73BB73",
                        "market_competition": "#A89FE9", "supply_chain_infrastructure": "#F09B9A"}
# Keyword bars (02): one color family per category, darkest shade for the top keyword.
CATEGORY_FAMILIES = {
    "regulation_policy": ["#F299B9", "#F1A3B5", "#DCAFBE", "#E0B7BA"],
    "technology_trend": ["#9FD2D8", "#ABD8D6", "#B9DDD5", "#C5E2D3", "#D3E7D2"],
    "market_competition": ["#C1B2C9", "#C8BAC6", "#CEC1C3", "#D6C9C0"],
    "supply_chain_infrastructure": ["#F2B6AC", "#F2BFA7", "#F2C9A4", "#F2D29E"],
}
SENTIMENT_COLORS = {"positive": "#C6E1FF", "neutral": "#E4E4E4", "negative": "#FDD1D9"}
SENTIMENT_LABELS = {"positive": "긍정", "neutral": "중립", "negative": "부정"}
NEUTRAL_INK = "#52514e"
INK = "#141413"
BAR_OUTLINE = "rgba(20,20,19,0.28)"
# Spike weeks at or above this multiple of their threshold are drawn as clear spikes (filled);
# below it, hollow. Display distinction only — detection stays "previous 8-week mean + 2 sigma".
CLEAR_SPIKE_RATIO = 1.25
# Spike markers on the weekly volume chart (filled = clear spike, hollow = near the threshold).
SPIKE_COLOR = "#FFC000"   # vivid yellow (still visible on the light card)
# Article-count line on the weekly volume chart (Industry Overview).
VOLUME_LINE_COLOR = "#F08929"   # orange
VOLUME_LINE_WIDTH = 1.6   # 0.8x the default line width (2) used by style()
# Filings trend (Industry Overview), pastel: firm hues for the own-filing timeline (same hue order as
# FIRM_COLORS) and a pastel blue-violet for the monthly client audit-report bars (as on Big4 Comparison).
FIRM_TIMELINE_PASTEL = {"pwc": "#88C9E2", "kpmg": "#F1B499", "ey": "#9AD4B4", "deloitte": "#ECD78A"}
AUDIT_MONTHLY_COLOR = "#979BD9"


def style(fig: go.Figure, y_title: str = "", height: int = 340) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=20, r=20, t=28, b=16), legend_title_text="",
                      hovermode="x unified", bargap=0.15, yaxis_title=y_title, xaxis_title="",
                      # solid lifted card: the decorative background arcs must never run through a plot
                      # area, where they could be read as data
                      paper_bgcolor=LIFTED, plot_bgcolor=LIFTED,
                      font=dict(family=FONT_STACK, color="#141413"),
                      hoverlabel=dict(font=dict(family=FONT_STACK)))
    fig.update_traces(selector=dict(type="scatter"), line=dict(width=2))
    fig.update_traces(selector=dict(type="bar"), marker_line=dict(width=0.8, color=BAR_OUTLINE))
    fig.update_yaxes(gridcolor="rgba(20,20,19,0.10)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


def shades(colors: list[str], n: int) -> list[str]:
    """n colors spread evenly along `colors` (linear RGB interpolation), first = colors[0]."""
    if n <= 0:
        return []
    rgb = [tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in colors]
    if n == 1 or len(rgb) == 1:
        return [colors[0]] * n
    out = []
    for k in range(n):
        pos = k * (len(rgb) - 1) / (n - 1)
        i = min(int(pos), len(rgb) - 2)
        t = pos - i
        out.append("#" + "".join(f"{round(a + (b - a) * t):02X}" for a, b in zip(rgb[i], rgb[i + 1])))
    return out


# Opacity of a line while the pointer is not on it (hover_highlight).
DIM_OPACITY = 0.25

_HIGHLIGHT_HTML = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400;500&display=swap" rel="stylesheet">
<style>
html, body { margin: 0; background: transparent; }
#card { background: __LIFTED__; border: 1px solid rgba(20,20,19,0.08); border-radius: 24px; overflow: hidden; }
/* Plotly writes opacity inline on each trace group, hence !important */
#chart .scatterlayer .trace { opacity: __DIM__ !important; transition: opacity 0.15s ease; }
#chart .scatterlayer .trace.hl { opacity: 1 !important; }
</style></head><body>
<div id="card"><div id="chart"></div></div>
<script>__PLOTLYJS__</script>
<script>
const fig = __FIG__;
const gd = document.getElementById("chart");
const draw = () => Plotly.newPlot(gd, fig.data, fig.layout, {displaylogo: false, responsive: true}).then(() => {
  const traces = () => gd.querySelectorAll(".scatterlayer .trace");
  gd.on("plotly_hover", e => {
    const c = e.points[0].curveNumber;
    traces().forEach((t, i) => t.classList.toggle("hl", i === c));
  });
  gd.on("plotly_unhover", () => traces().forEach(t => t.classList.remove("hl")));
});
// draw after the web font is in, so Plotly sizes the legend with the final font
(document.fonts ? document.fonts.ready : Promise.resolve()).then(draw);
</script></body></html>"""


@lru_cache(maxsize=1)
def _plotlyjs() -> str:
    return get_plotlyjs()


def hover_highlight(fig: go.Figure) -> None:
    """Render a line chart whose lines stay pale until the pointer is on one; that line goes full color.

    st.plotly_chart cannot run event handlers, so the figure is drawn by plotly.js inside an
    iframe (plotly.js is inlined, ~5 MB, so it also works offline). Every trace must be a scatter trace,
    drawn in data order. Hover is per point ("closest") so the event names one line.
    """
    fig.update_layout(hovermode="closest", autosize=True, width=None)
    # outside Streamlit's chart wrapper the y title would sit on the tick labels: let Plotly make room
    fig.update_yaxes(automargin=True, title_standoff=10)
    fig.update_traces(hovertemplate="%{fullData.name}<br>%{x|%Y-%m}: %{y}건<extra></extra>")
    fig_json = fig.to_json().replace("</", "<\\/")  # no "</script>" can close the tag early
    html = (_HIGHLIGHT_HTML.replace("__LIFTED__", LIFTED).replace("__DIM__", str(DIM_OPACITY))
            .replace("__FIG__", fig_json).replace("__PLOTLYJS__", _plotlyjs()))
    _embed_html(html, int(fig.layout.height) + 4)


def _embed_html(html: str, height: int) -> None:
    """st.iframe on current Streamlit (st.components.v1.html is deprecated and slated for removal);
    the old call on versions that do not have st.iframe yet. The HTML is generated here from our own
    figure (JSON escaped above), never from user or LLM text."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:  # pragma: no cover - older Streamlit
        import streamlit.components.v1 as components

        components.html(html, height=height)
