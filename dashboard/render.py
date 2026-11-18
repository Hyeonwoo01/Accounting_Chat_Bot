"""Pure text helpers for the Q&A page and conclusion lines (kept free of Streamlit so they can be tested)."""

from __future__ import annotations

import html
import re

_MD_SPECIAL = re.compile(r"([\\`*_~\[\]<>#|])")


def escape_md(text: str) -> str:
    """Escape characters Streamlit markdown would interpret (table text has '*', '|', '~', '#')."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def strip_context_line(chunk_text: str) -> tuple[str, int]:
    """Drop the '[filer report | section]' line the index adds before each chunk (not filing text).
    Returns (text, offset removed) so quote spans can be shifted."""
    if chunk_text.startswith("[") and "\n" in chunk_text:
        cut = chunk_text.index("\n") + 1
        return chunk_text[cut:], cut
    return chunk_text, 0


def highlight(chunk_text: str, spans: list[tuple[int, int]]) -> str:
    """Chunk text as markdown with every quoted span in bold (overlapping spans merged).
    Whitespace runs collapse to one space so table rows read as one paragraph."""
    text, off = strip_context_line(chunk_text)
    merged: list[list[int]] = []
    for s, e in sorted((s - off, e - off) for s, e in spans if e - off > 0):
        s = max(s, 0)
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, pos = [], 0
    for s, e in merged:
        out.append(escape_md(text[pos:s]))
        quoted = text[s:e]
        lead, trail = quoted[: len(quoted) - len(quoted.lstrip())], quoted[len(quoted.rstrip()):]
        out.append(f"{lead}**{escape_md(quoted.strip())}**{trail}")  # bold must hug the text
        pos = e
    out.append(escape_md(text[pos:]))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def html_text(text: str) -> str:
    """HTML-escape, and turn characters Streamlit markdown would still read inside HTML into entities."""
    out = html.escape(text, quote=True)
    return out.replace("~", "&#126;").replace("*", "&#42;").replace("_", "&#95;").replace("`", "&#96;")


def emphasize_outside_parens(text: str, css_class: str = "concl-strong") -> str:
    """HTML for a conclusion line: text outside parentheses wrapped in <span class=css_class>,
    parentheses and what they hold left as is (nested and unbalanced parentheses are handled)."""
    parts: list[tuple[bool, str]] = []   # (inside parentheses?, text)
    depth, buf = 0, ""
    for ch in text:
        if ch in "(（" and depth == 0:
            if buf:
                parts.append((False, buf))
            buf = ch
            depth = 1
        elif ch in "(（":
            buf += ch
            depth += 1
        elif ch in ")）" and depth > 0:
            buf += ch
            depth -= 1
            if depth == 0:
                parts.append((True, buf))
                buf = ""
        else:
            buf += ch
    if buf:
        parts.append((depth > 0, buf))
    return "".join(html_text(t) if inside or not t.strip() else f'<span class="{css_class}">{html_text(t)}</span>'
                   for inside, t in parts)
