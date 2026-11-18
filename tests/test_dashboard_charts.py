"""Chart color helpers."""

from __future__ import annotations

from dashboard.charts import shades


def test_shades_spreads_evenly_and_keeps_endpoints():
    assert shades(["#000000", "#FFFFFF"], 3) == ["#000000", "#808080", "#FFFFFF"]
    assert shades(["#123456"], 2) == ["#123456", "#123456"]
    assert shades(["#000000", "#FFFFFF"], 0) == []
    assert len(shades(["#F299B9", "#F1A3B5", "#DCAFBE"], 15)) == 15


def test_hover_highlight_builds_self_contained_html(monkeypatch):
    import plotly.graph_objects as go

    from dashboard import charts

    captured = {}
    monkeypatch.setattr(charts, "_embed_html", lambda html, height: captured.update(html=html, height=height))
    monkeypatch.setattr(charts, "_plotlyjs", lambda: "/*plotlyjs*/")
    fig = go.Figure(go.Scatter(x=[1, 2], y=[3, 4], name="</script><b>x"))
    charts.hover_highlight(charts.style(fig, height=300))

    html = captured["html"]
    assert captured["height"] == 304
    assert "/*plotlyjs*/" in html and "plotly_hover" in html
    assert "</script><b>" not in html  # trace names cannot close the script tag
    assert not any(tok in html for tok in ("__FIG__", "__PLOTLYJS__", "__LIFTED__", "__DIM__"))
    assert fig.layout.hovermode == "closest"
