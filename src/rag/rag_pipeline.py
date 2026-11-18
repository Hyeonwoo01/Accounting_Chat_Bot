"""Grounded Q&A over DART filings (guide 7). The ONLY rag entry point the dashboard may call.

Flow: retrieve top-k chunks (optional firm / date filters) -> refuse WITHOUT calling the LLM if the
best relevance is under the threshold -> ask the LLM to answer only from the numbered chunks and
quote them verbatim -> keep only citations whose quote is found in the chunk text -> refuse if none
survive. Every citation shows filing name, submission date, receipt number, filer and DART link,
and for a client audit report the audited company (principle 8).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

REFUSAL = "수집된 공시에서 근거를 확인할 수 없습니다."
# Bump on every SYSTEM_PROMPT change and note why (recorded in the eval report).
# rag-v1: initial. rag-v2 (2026-11-16): year unspecified -> answer from the latest filing
#   (Q02 answered from the oldest, 2022 correction filing).
# rag-v3 (2026-11-16): "latest" is per firm — v2 made Q05 drop 삼정 (FY ends 3월) because 한영·삼일 (6월) were later.
# rag-v4 (2026-11-17): rule for quoting tables (row label + numbers as written, no added units) — a dashboard
#   question on 안진 감사실적 was refused because no quote from the table chunks passed verification.
PROMPT_VERSION = "rag-v4"
MIN_QUOTE_CHARS = 8

SYSTEM_PROMPT = f"""당신은 DART 공시 원문만 근거로 답하는 분석가다.
- 아래 [근거 n] 청크에 있는 내용만으로 답한다. 청크에 없는 사실·전망·추측·뉴스 내용은 쓰지 않는다.
- 근거가 부족하면 답하지 말고 거절한다.
- 감사보고서(filing_role=audit_client)는 감사받은 '고객사'의 공시다. 그 내용을 회계법인의 실적·행동으로 쓰지 않고,
  답변에 고객사명과 감사인(제출 법인)을 밝힌다.
- 같은 법인의 여러 연도 공시가 섞여 있으면 어느 공시(사업연도)의 값인지 밝혀 답한다.
  질문에 연도가 없으면 법인(회사)별로 각자의 가장 최근 공시 기준으로 답한다. 법인마다 사업연도 말이 다르므로
  한 법인의 최신 공시보다 이르다는 이유로 다른 법인의 공시를 빼지 않는다.
- 인용(quote)은 청크 원문에서 그대로 복사한 구절이어야 한다 (요약·변형 금지).
- 표에서 인용할 때는 행 이름과 숫자를 원문에 적힌 순서 그대로 이어서 복사한다 (예: "합계 791 768").
  단위("사", "원")·쉼표·설명을 덧붙이지 않고, 숫자만 따로 떼어 인용하지 않는다. 인용은 8자 이상으로 한다.

아래 JSON 하나만 출력한다.
답할 수 있을 때: {{"answer": "한국어 답변", "citations": [{{"source": 근거번호, "quote": "청크 원문 그대로의 구절"}}]}}
답할 수 없을 때: {{"refuse": true, "reason": "이유"}}
"""


@dataclass
class Citation:
    source: int
    quote: str
    metadata: dict[str, Any]
    # Original chunk text around the quote: before / matched span / after. Lets a reader see which table
    # row a short quote such as "합계 791 768" belongs to. Still the chunk's own words, not a summary.
    context: tuple[str, str, str] = ("", "", "")
    chunk_text: str = ""                      # the whole retrieved chunk (for grouping quotes per chunk)
    span: tuple[int, int] | None = None       # quote position in chunk_text (original characters)


@dataclass
class Answer:
    question: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    refused: bool = False
    reason: str = ""
    top_score: float | None = None
    retrieved: int = 0
    auto_company_id: str | None = None  # firm recognised in the question and used as a filter
    replaced_company_id: str | None = None  # firm the caller selected but the question named another one
    typo_fixed: tuple[str, str] | None = None  # (as typed, read as) when a firm name typo was corrected
    rejected_quotes: list[str] = field(default_factory=list)  # model quotes that failed verification (diagnosis)


_HANGUL = re.compile(r"[가-힣]")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def firm_aliases(mapping: dict) -> dict[str, list[str]]:
    """company_id -> names that identify the firm in a question (brand, legal name, member-firm aliases).

    Weak aliases ("안진", "EY", ...) are left out: too short to be read as the firm on their own.
    """
    out: dict[str, list[str]] = {}
    for c in mapping["companies"]:
        names = set(c.get("aliases", [])) | set(c.get("search_aliases", []))
        for m in c.get("member_firms", []):
            names |= set(m.get("aliases", [])) | {m.get("display_name", ""), m.get("legal_name", "")}
        names |= {e.get("name", "") for e in c.get("kr_entities", [])}
        out[c["company_id"]] = sorted(n for n in names if n)
    return out


def detect_firms(question: str, aliases: dict[str, list[str]]) -> set[str]:
    """Firms named in the question, regardless of spacing ("딜로이트안진회계법인" = "딜로이트 안진 회계법인").

    Korean names are matched with all whitespace removed; Latin-only names ("KPMG", "Deloitte") must not
    sit inside a longer Latin word ("EY" never matches "key"), but may touch Korean ("삼정KPMG").
    """
    compact, lower = _compact(question), question.lower()
    found = set()
    for company_id, names in aliases.items():
        for name in names:
            if _HANGUL.search(name):
                hit = _compact(name) in compact
            else:
                hit = re.search(rf"(?<![a-z]){re.escape(name.lower())}(?![a-z])", lower) is not None
            if hit:
                found.add(company_id)
                break
    return found


def _jamo(text: str) -> str:
    """Decompose Hangul syllables into jamo so '이' vs '일' is one edit and '이' vs '정' is three."""
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out += [chr(0x1100 + code // 588), chr(0x1161 + (code % 588) // 28)]
            if code % 28:
                out.append(chr(0x11A7 + code % 28))
        else:
            out.append(ch.lower())
    return "".join(out)


def _within(a: str, b: str, limit: int) -> int | None:
    """Levenshtein distance if <= limit, else None (row minimum early exit)."""
    if abs(len(a) - len(b)) > limit:
        return None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return None
        prev = cur
    return prev[-1] if prev[-1] <= limit else None


def fuzzy_firm(question: str, aliases: dict[str, list[str]]) -> tuple[str, tuple[int, int], str] | None:
    """A firm name typed with a small typo ("삼이회계법인" -> 삼일회계법인): (company_id, span in question, alias).

    Only Korean names of 4+ characters are tried, at most 1 jamo edit (2 for 5+ characters), and the
    best match must belong to ONE firm — otherwise nothing is guessed. Used only when no firm name
    matched exactly.
    """
    keep = [i for i, ch in enumerate(question) if not ch.isspace()]
    compact = "".join(question[i] for i in keep)[:200]
    best: list[tuple[int, str, tuple[int, int], str]] = []
    for company_id, names in aliases.items():
        for name in names:
            target = _compact(name)
            if not _HANGUL.search(target) or len(target) < 4:
                continue
            limit, jt = (1 if len(target) < 5 else 2), _jamo(target)
            for size in (len(target) - 1, len(target), len(target) + 1):
                for start in range(0, len(compact) - size + 1):
                    d = _within(_jamo(compact[start:start + size]), jt, limit)
                    if d is not None:
                        span = (keep[start], keep[start + size - 1] + 1)
                        best.append((d, company_id, span, name))
    if not best:
        return None
    top = min(d for d, *_ in best)
    winners = {c for d, c, *_ in best if d == top}
    if len(winners) != 1:
        return None
    d, company_id, span, name = min((b for b in best if b[0] == top), key=lambda b: b[2][1] - b[2][0])
    return company_id, span, name


# (messages) -> raw model text. Injected so tests run without an API.
ChatText = Callable[[list[dict[str, str]]], str]
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def quote_in_chunk(quote: str, chunk_text: str) -> bool:
    """Verbatim check that ignores whitespace only: every other character (digits, words, punctuation) must
    appear in the chunk in the same order. Table text has cells joined by spaces, and models re-space
    them ("유가증권시장상장법인"). Short quotes are rejected: they verify nothing."""
    q = re.sub(r"\s+", "", quote)
    return len(q) >= MIN_QUOTE_CHARS and q in re.sub(r"\s+", "", chunk_text)


def quote_span(quote: str, chunk_text: str) -> tuple[int, int] | None:
    """(start, end) of a quote in the ORIGINAL chunk text, matching while ignoring whitespace."""
    keep = [i for i, ch in enumerate(chunk_text) if not ch.isspace()]
    compact = "".join(chunk_text[i] for i in keep)
    q = re.sub(r"\s+", "", quote)
    pos = compact.find(q) if q else -1
    return None if pos < 0 else (keep[pos], keep[pos + len(q) - 1] + 1)


def quote_context(quote: str, chunk_text: str, width: int = 120) -> tuple[str, str, str]:
    """(before, match, after) from the ORIGINAL chunk text for a quote found while ignoring whitespace."""
    span = quote_span(quote, chunk_text)
    if span is None:
        return ("", _norm(quote), "")
    start, end = span
    before = _norm(chunk_text[max(0, start - width):start])
    after = _norm(chunk_text[end:end + width])
    return (("… " if start > width else "") + before, _norm(chunk_text[start:end]),
            after + (" …" if end + width < len(chunk_text) else ""))


def build_messages(question: str, docs: list[tuple[str, dict]]) -> list[dict[str, str]]:
    blocks = []
    for i, (text, md) in enumerate(docs, 1):
        who = (f"고객사 감사보고서 — 고객사: {md.get('client_name') or '미확인'}, 감사인: {md.get('filer')}"
               if md.get("filing_role") == "audit_client" else f"회계법인 자체 공시 — 제출: {md.get('filer')}")
        blocks.append(f"[근거 {i}] {md.get('report_nm')} | 제출일 {md.get('rcept_dt')} | {who} | "
                      f"절: {md.get('section_title')}\n{text}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"질문: {question}\n\n" + "\n\n".join(blocks)}]


def _parse(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return {"refuse": True, "reason": "응답 형식 오류"}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"refuse": True, "reason": "응답 형식 오류"}


def _date_key(d: str | None) -> int | None:
    return int(d.replace("-", "")) if d else None


RETRIEVAL_MODES = ("vector", "hybrid", "hybrid_rerank")


class RagPipeline:
    def __init__(self, store: Any, chat: ChatText, k: int = 5, min_relevance: float = 0.35,
                 firm_aliases: dict[str, list[str]] | None = None, retrieval: str = "vector",
                 bm25: Any = None, candidates: int = 20, rrf_k: int = 60) -> None:
        """retrieval: 'vector' (embedding search only), 'hybrid' (+ BM25 keywords, RRF fusion),
        'hybrid_rerank' (hybrid, then the LLM orders `candidates` fused chunks and keeps k).
        Hybrid modes need `bm25` (src.rag.hybrid.BM25Index over the same chunks)."""
        if retrieval not in RETRIEVAL_MODES:
            raise ValueError(f"unknown retrieval mode: {retrieval}")
        if retrieval != "vector" and bm25 is None:
            raise ValueError(f"retrieval={retrieval} needs a BM25 index")
        self.store = store
        self.chat = chat
        self.k = k
        self.firm_aliases = firm_aliases or {}
        self.min_relevance = min_relevance
        self.retrieval, self.bm25, self.candidates, self.rrf_k = retrieval, bm25, candidates, rrf_k

    def retrieve(self, question: str, company_id: str | None = None, date_from: str | None = None,
                 date_to: str | None = None, filing_role: str | None = None) -> list[tuple[str, dict, float | None]]:
        """The k chunks the answer step sees: (text, metadata, vector relevance). In hybrid modes a chunk found
        only by keywords has relevance None. See search() for the refusal score."""
        return self.search(question, company_id=company_id, date_from=date_from, date_to=date_to,
                           filing_role=filing_role)[0]

    def search(self, question: str, company_id: str | None = None, date_from: str | None = None,
               date_to: str | None = None, filing_role: str | None = None
               ) -> tuple[list[tuple[str, dict, float | None]], float | None]:
        """(k chunks, best vector relevance). The refusal gate always uses the VECTOR relevance, in every
        mode, so the threshold chosen in settings keeps its meaning; keyword hits only reorder/add chunks.
        The LLM rerank is skipped when the question will be refused anyway (no extra call)."""
        n = self.k if self.retrieval == "vector" else max(self.candidates, self.k)
        vec = self._vector(question, n, company_id=company_id, date_from=date_from, date_to=date_to,
                           filing_role=filing_role)
        top = max((s for *_, s in vec), default=None)
        if self.retrieval == "vector":
            return [(t, md, s) for _, t, md, s in vec], top
        from src.rag.hybrid import fuse, llm_rerank

        kw = self.bm25.search(question, n, company_id=company_id, date_from=date_from, date_to=date_to,
                              filing_role=filing_role)
        if self.retrieval == "hybrid" or top is None or top < self.min_relevance:
            return fuse(vec, kw, self.k, self.rrf_k), top
        pool = fuse(vec, kw, n, self.rrf_k)
        order = llm_rerank(self.chat, question, [(t, md) for t, md, _ in pool], self.k)
        return ([pool[i] for i in order] if order else pool[:self.k]), top

    def _vector(self, question: str, n: int, company_id: str | None = None, date_from: str | None = None,
                date_to: str | None = None, filing_role: str | None = None) -> list[tuple[str, str, dict, float]]:
        """filing_role: 'own' (firm's own filings) | 'audit_client' (client audit reports) | None (both).
        company_id matches the FILING firm for both kinds, so a question about the firm itself should
        pass filing_role='own' or client reports (most chunks) crowd out the firm's own filings."""
        if filing_role not in (None, "", "own", "audit_client"):
            raise ValueError(f"unknown filing_role: {filing_role}")
        conds: list[dict] = []
        if company_id:
            conds.append({"company_id": company_id})
        if filing_role:
            conds.append({"filing_role": filing_role})
        if date_from:
            conds.append({"rcept_ymd": {"$gte": _date_key(date_from)}})
        if date_to:
            conds.append({"rcept_ymd": {"$lte": _date_key(date_to)}})
        flt = None if not conds else conds[0] if len(conds) == 1 else {"$and": conds}
        # Store uses cosine space (vector_store.open_store): relevance = 1 - cosine distance, in [-1, 1].
        hits = self.store.similarity_search_with_score(question, k=n, filter=flt)
        return [(d.metadata.get("chunk_id") or f"vec{i}", d.page_content, d.metadata, 1.0 - float(dist))
                for i, (d, dist) in enumerate(hits)]

    def ask(self, question: str, **filters: str | None) -> Answer:
        # All four firms file the same report sections, so a question that names ONE firm is searched
        # within that firm's filings; otherwise the firm name's spelling decides which firm's table
        # ranks first. If the caller selected a DIFFERENT firm, the question wins: a selector left over
        # from an earlier question must not silently search the wrong firm (2026-11-17: 삼일 question
        # with 삼정 selected was refused). The replaced selection is reported. Two or more named firms,
        # or none -> the caller's filter is kept as is.
        auto = replaced = typo = None
        query = question
        named = detect_firms(question, self.firm_aliases)
        if not named and self.firm_aliases:
            guess = fuzzy_firm(question, self.firm_aliases)
            if guess:  # search and answer with the corrected name; the page shows what was changed
                company_id, (a, b), alias = guess
                typo = (question[a:b], alias)
                query = question[:a] + alias + question[b:]
                named = {company_id}
        if len(named) == 1:
            only = next(iter(named))
            if filters.get("company_id") and filters["company_id"] != only:
                replaced = filters["company_id"]
            if filters.get("company_id") != only:
                auto = only
                filters = {**filters, "company_id": only}
        hits, top = self.search(query, **filters)
        if not hits or top is None or top < self.min_relevance:
            return Answer(question, REFUSAL, refused=True, top_score=top, retrieved=len(hits), auto_company_id=auto, replaced_company_id=replaced, typo_fixed=typo,
                          reason=f"검색 유사도 {top if top is None else round(top, 3)} < 기준 {self.min_relevance}")
        docs = [(t, md) for t, md, _ in hits]
        out = _parse(self.chat(build_messages(query, docs)))
        if out.get("refuse") or not out.get("answer"):
            return Answer(question, REFUSAL, refused=True, top_score=top, retrieved=len(hits), auto_company_id=auto, replaced_company_id=replaced, typo_fixed=typo,
                          reason=out.get("reason") or "모델이 거절")
        verified, rejected = [], []
        for c in out.get("citations") or []:
            quote = str(c.get("quote", ""))
            try:
                n = int(c.get("source"))
            except (TypeError, ValueError):
                rejected.append(f"(근거 번호 없음) {quote}"[:160])
                continue
            if 1 <= n <= len(docs) and quote_in_chunk(quote, docs[n - 1][0]):
                chunk = docs[n - 1][0]
                verified.append(Citation(n, _norm(quote), docs[n - 1][1], quote_context(quote, chunk),
                                         chunk, quote_span(quote, chunk)))
            else:
                rejected.append(f"[근거 {n}] {quote}"[:160])
        if not verified:
            return Answer(question, REFUSAL, refused=True, top_score=top, retrieved=len(hits), auto_company_id=auto, replaced_company_id=replaced, typo_fixed=typo,
                          rejected_quotes=rejected[:5],
                          reason=f"인용 구절을 원문에서 확인하지 못함 (인용 {len(rejected)}개 모두 불일치)")
        return Answer(question, out["answer"].strip(), verified, top_score=top, retrieved=len(hits),
                      auto_company_id=auto, replaced_company_id=replaced, typo_fixed=typo)


def format_citation(i: int, c: Citation) -> str:
    """Guide 7-3 citation block."""
    md = c.metadata
    rcept_dt = str(md.get("rcept_dt", ""))
    lines = [f"[공시 근거 {i}]",
             f"공시명: {md.get('report_nm')}",
             f"제출일: {rcept_dt}",
             f"접수번호: {md.get('rcept_no')}"]
    if md.get("filing_role") == "audit_client":
        lines.append(f"고객사(감사 대상): {md.get('client_name') or '미확인'} / 감사인(제출): {md.get('filer')}")
    else:
        lines.append(f"제출 법인: {md.get('filer')}")
    lines += [f"출처: {md.get('source_url')}", f'관련 근거: "{c.quote}"']
    return "\n".join(lines)


def openai_chat_text(api_key: str, model: str, timeout: float = 60.0) -> ChatText:
    """Chat Completions in JSON mode. Temperature is not sent (gpt-6-luna accepts only its default)."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=3)

    def chat(messages: list[dict[str, str]]) -> str:
        resp = client.chat.completions.create(model=model, messages=messages,
                                              response_format={"type": "json_object"})
        return resp.choices[0].message.content or ""

    return chat


def load_pipeline(retrieval: str | None = None) -> RagPipeline:
    """Pipeline from settings + the persisted vector store. Used by the dashboard and scripts.
    `retrieval` overrides settings.rag.retrieval.mode (evaluation compares the modes)."""
    from langchain_openai import OpenAIEmbeddings

    from src.common.config import (
        get_embedding_model, get_llm_model, get_openai_api_key, get_vectordb_path, load_company_mapping, load_settings,
    )
    from src.rag.vector_store import open_store

    cfg = load_settings()["rag"]
    rcfg = cfg.get("retrieval") or {}
    mode = retrieval or rcfg.get("mode", "vector")
    key = get_openai_api_key()
    model = get_embedding_model()
    store = open_store(get_vectordb_path(), OpenAIEmbeddings(model=model, api_key=key), model)
    bm25 = None
    if mode != "vector":
        from src.rag.hybrid import index_from_store

        bm25 = index_from_store(store)  # in memory, built once per process (the dashboard caches the pipeline)
    return RagPipeline(store, openai_chat_text(key, get_llm_model()), k=cfg["k"], min_relevance=cfg["min_relevance"],
                       firm_aliases=firm_aliases(load_company_mapping()), retrieval=mode, bm25=bm25,
                       candidates=rcfg.get("candidates", 20), rrf_k=rcfg.get("rrf_k", 60))
