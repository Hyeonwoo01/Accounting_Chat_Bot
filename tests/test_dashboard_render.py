"""Q&A citation rendering: one chunk, every verified quote in bold, filing text escaped."""

from __future__ import annotations

from dashboard.render import escape_md, highlight, strip_context_line
from src.rag.rag_pipeline import quote_span

CHUNK = "[안진회계법인 보고서 | 가. 총괄표]\n유가증권시장 상장법인 86 72\n코스닥시장 상장법인 60 49 합계 791 768"


def test_highlight_bolds_all_spans_and_drops_index_header():
    spans = [quote_span("유가증권시장상장법인 86 72", CHUNK), quote_span("합계 791 768", CHUNK)]
    out = highlight(CHUNK, spans)
    assert out == "**유가증권시장 상장법인 86 72** 코스닥시장 상장법인 60 49 **합계 791 768**"
    assert "안진회계법인 보고서" not in out


def test_overlapping_spans_merge_and_specials_are_escaped():
    text = "a*b 1~2 # 합계 10 20"
    out = highlight(text, [(0, 3), (2, 7)])
    assert out == r"**a\*b 1\~2** \# 합계 10 20"
    assert escape_md("x|y") == r"x\|y"
    assert strip_context_line("본문만") == ("본문만", 0)


def test_emphasize_outside_parens_keeps_parenthesised_text_plain_and_escapes():
    from dashboard.render import emphasize_outside_parens, html_text

    out = emphasize_outside_parens("보도는 '시장'이 많았고(50.7%), 기술이 뒤(32.1%).")
    assert out == ('<span class="concl-strong">보도는 &#x27;시장&#x27;이 많았고</span>(50.7%)'
                   '<span class="concl-strong">, 기술이 뒤</span>(32.1%)<span class="concl-strong">.</span>')
    assert emphasize_outside_parens("(해석) 문장 (a (b) c) 끝") == (
        '(해석)<span class="concl-strong"> 문장 </span>(a (b) c)<span class="concl-strong"> 끝</span>')
    assert "<script>" not in emphasize_outside_parens("<script>x</script>")
    assert html_text("1~10월 *a* _b_") == "1&#126;10월 &#42;a&#42; &#95;b&#95;"   # no markdown strike/italics
