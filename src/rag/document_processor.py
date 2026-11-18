"""DART filings -> chunks with citation metadata (guide 7-1). Batch only.

Splits extracted filing text on the '## <section title>' lines kept by preprocessing, and cuts long
sections into overlapping windows. Every chunk carries what the answer needs to cite it: filing
name, submission date, receipt number, DART link, filer, and whether it is the firm's OWN filing
or a CLIENT audit report (CLAUDE.md principle 8 — a client's report is not a fact about the firm).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SECTION = re.compile(r"^##\s*(.+)$", re.M)
# Korean audit reports open with "<회사명>의 주주 및 이사회 귀중" (addressee of the auditor's report).
# The name may be followed by "의" or sit on the line above "주주 및 이사회 귀중".
_ADDRESSEE = re.compile(r"([^\n]{2,60}?)\s*(?:의)?\s*주\s*주\s*및\s*이\s*사\s*회\s*귀\s*중")
_SPACES = re.compile(r"[ \t]+")
_SENTENCE_END = re.compile(r"(?<=다\.)\s*|(?<=[.!?])\s+")
_NON_WORD = re.compile(r"[^0-9A-Za-z가-힣]")


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    chunk_index: int
    section_title: str
    text: str
    metadata: dict = field(default_factory=dict)


def client_name(text: str) -> str:
    """Audited company named in the report's addressee line, or '' if not found."""
    m = _ADDRESSEE.search(text[:5000])
    if not m:
        return ""
    name = _SPACES.sub(" ", m.group(1)).strip(" ,.:")
    # The line may start with leftovers ("독립된 감사인의 감사보고서 주식회사 가나") — keep the tail.
    return name.split("감사보고서")[-1].strip() if "감사보고서" in name else name


def sentence_key(sentence: str) -> str:
    """Comparison key: letters/digits only, so bullet styles (ㆍ, -) and spacing do not matter."""
    return _NON_WORD.sub("", sentence)


def _sentences(line: str) -> list[str]:
    return [x.strip() for x in _SENTENCE_END.split(line) if x.strip()]


def find_boilerplate(texts: list[str], min_docs: int, min_chars: int) -> set[str]:
    """Sentence keys that appear in at least `min_docs` different documents.

    Audit reports share long standard paragraphs (auditor/management responsibilities) that mention
    words like 계속기업 in every report; left in, they win retrieval for most questions. Short
    sentences (headings such as '감사의견') are never treated as boilerplate.
    """
    counts: dict[str, int] = {}
    for text in texts:
        keys = {k for line in text.splitlines() if not line.startswith("## ")
                for k in map(sentence_key, _sentences(line)) if len(k) >= min_chars}
        for k in keys:
            counts[k] = counts.get(k, 0) + 1
    return {k for k, n in counts.items() if n >= min_docs}


def strip_boilerplate(text: str, boilerplate: set[str]) -> str:
    if not boilerplate:
        return text
    out = []
    for line in text.splitlines():
        if line.startswith("## "):
            out.append(line)
            continue
        kept = [x for x in _sentences(line) if sentence_key(x) not in boilerplate]
        if kept:
            out.append(" ".join(kept))
    return "\n".join(out)


# DART section numbering, outermost first: "II." / "2." / "가." / "(1)". Anything else counts as top level.
_HEADING_LEVELS = (
    (re.compile(r"^(?:[IVX]+|[\u2160-\u216b])\s*\."), 1),
    (re.compile(r"^\d+\s*\."), 2),
    (re.compile(r"^[가-하]\s*\."), 3),
    (re.compile(r"^\(\s*\d+\s*\)"), 4),
)


def heading_level(title: str) -> int:
    for pattern, level in _HEADING_LEVELS:
        if pattern.match(title):
            return level
    return 1


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(section_path, body)] in order. Text before the first title goes under '(머리말)'.

    The title is the full path of enclosing headings ("2. 외감법에 의한 감사실적 > 가. 감사실적 총괄표").
    Parent headings often have no body of their own, so without the path a table chunk would carry
    only its sub-title and a question phrased with the parent's words could not find it (2026-11-17:
    안진 '외감법에 의한 감사실적' was refused although the tables were indexed).
    """
    parts: list[tuple[str, str]] = []
    matches = list(_SECTION.finditer(text))
    head = text[: matches[0].start()] if matches else text
    if head.strip():
        parts.append(("(머리말)", head.strip()))
    stack: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        level = heading_level(title)
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        if body:
            parts.append((" > ".join(t for _, t in stack), body))
    return parts


def windows(body: str, size: int, overlap: int) -> list[str]:
    """Cut a long section into windows of `size` chars overlapping by `overlap`, preferring line breaks."""
    if len(body) <= size:
        return [body]
    out, start = [], 0
    while start < len(body):
        end = min(len(body), start + size)
        if end < len(body):
            nl = body.rfind("\n", start + size // 2, end)
            end = nl if nl > start else end
        out.append(body[start:end].strip())
        if end >= len(body):
            break
        start = max(end - overlap, start + 1)
    return [w for w in out if w]


def merge_short_sections(parts: list[tuple[str, str]], min_chars: int) -> list[tuple[str, str]]:
    """Fold sections shorter than `min_chars` into the following one (titles joined with ' / ').

    Cover pages and one-line sections ("회계법인사업보고서 1.8 삼정회계법인") are almost only the firm
    name, so on their own they outrank real content for any question that names the firm. A short
    tail is attached to the previous group. Folded sections keep their title inline as '[title] body'.
    """
    groups: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    for part in parts:
        current.append(part)
        if sum(len(b) for _, b in current) >= min_chars:
            groups.append(current)
            current = []
    if current:
        if groups:
            groups[-1].extend(current)
        else:
            groups.append(current)
    out = []
    for group in groups:
        title = " / ".join(t for t, _ in group)
        body = "\n".join(b if k == 0 else f"[{t}] {b}" for k, (t, b) in enumerate(group))
        out.append((title, body))
    return out


def context_line(md: dict, section: str) -> str:
    """Prefix embedded with every chunk so tables and fragments keep who/what they are about."""
    if md["filing_role"] == "audit_client":
        who = f"{md['client_name'] or '고객사 미확인'} {md['report_nm']} (감사인 {md['filer']})"
    else:
        who = f"{md['filer']} {md['report_nm']}"
    return f"[{who} | {section}]"


def chunk_document(doc: dict, big4_corp_codes: set[str], firm_names: dict[str, str],
                   size: int = 900, overlap: int = 150, client_names: dict[str, str] | None = None,
                   boilerplate: set[str] | None = None, min_section_chars: int = 0) -> list[Chunk]:
    """documents row (DART, with content) -> chunks.

    firm_names: company_id -> filer name fallback. client_names: rcept_no -> audited company name from
    the DART listing (preferred over parsing the addressee line). boilerplate: sentence keys removed
    from client audit reports before chunking (see find_boilerplate).
    """
    raw = doc.get("content") or ""
    own = doc.get("corp_code") in big4_corp_codes
    text = raw if own else strip_boilerplate(raw, boilerplate or set())
    if not text.strip():
        return []
    company_id = (doc.get("company_ids") or [""])[0]
    rcept_dt = doc.get("rcept_dt") or doc.get("published_at") or ""
    base = {
        "doc_id": doc["doc_id"],
        "corp_code": doc.get("corp_code") or "",
        "company_id": company_id,
        "rcept_no": doc.get("rcept_no") or "",
        "report_nm": doc.get("report_nm") or doc.get("title") or "",
        "rcept_dt": rcept_dt,
        "rcept_ymd": int(rcept_dt.replace("-", "")) if rcept_dt else 0,  # numeric copy for date filters
        "pblntf_ty": doc.get("pblntf_ty") or "",
        "source_url": doc.get("url") or "",
        "filer": doc.get("source_name") or firm_names.get(company_id, ""),
        "filing_role": "own" if own else "audit_client",
        # For a client audit report: the audited company (not the firm). Empty for own filings.
        "client_name": "" if own else ((client_names or {}).get(doc.get("rcept_no") or "") or client_name(raw)),
    }
    chunks: list[Chunk] = []
    for section, body in merge_short_sections(split_sections(text), min_section_chars):
        head = context_line(base, section)
        for piece in windows(body, size, overlap):
            idx = len(chunks)
            chunk_id = f"{doc['doc_id']}#{idx:04d}"
            chunks.append(Chunk(chunk_id, doc["doc_id"], idx, section, f"{head}\n{piece}",
                                {**base, "chunk_id": chunk_id, "section_title": section}))
    return chunks
