"""Hybrid retrieval for the filings Q&A: BM25 keywords + vector search, fused by rank, optional LLM rerank.

Why: vector search misses a filing section when the question uses other words than the filing
(2026-11-17 Q04: '조치·제재' vs the section '감리결과'). Keyword matching catches shared terms the
embedding does not weigh. Both lists are fused with Reciprocal Rank Fusion (RRF), so no score
scaling between the two methods is needed. An optional LLM rerank orders the fused candidates.

Korean has no spaces inside compound words ('감리결과', '공인회계사수'), so text is tokenised into
character bigrams within each word (plus whole ASCII/number words). No morphological analyser needed.
The BM25 index is built in memory from the same chunks as the vector store (ids and metadata match).
"""

from __future__ import annotations

import json
import math
import re
from array import array
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

_WORD = re.compile(r"[가-힣]+|[a-z0-9]+(?:\.[0-9]+)?")


def tokenize(text: str) -> list[str]:
    """Hangul words -> character bigrams (a 1-char word stays as is); latin/number words kept whole."""
    out: list[str] = []
    for w in _WORD.findall((text or "").lower()):
        if "가" <= w[0] <= "힣":
            out.extend([w] if len(w) == 1 else [w[i:i + 2] for i in range(len(w) - 1)])
        else:
            out.append(w)
    return out


def _date_key(d: str | None) -> int | None:
    return int(d.replace("-", "")) if d else None


def matches(md: dict, company_id: str | None = None, filing_role: str | None = None,
            date_from: str | None = None, date_to: str | None = None) -> bool:
    """The same filter the vector search applies (rag_pipeline.RagPipeline.retrieve), on chunk metadata."""
    if company_id and md.get("company_id") != company_id:
        return False
    if filing_role and md.get("filing_role") != filing_role:
        return False
    ymd = md.get("rcept_ymd")
    if date_from and (ymd is None or ymd < _date_key(date_from)):
        return False
    if date_to and (ymd is None or ymd > _date_key(date_to)):
        return False
    return True


class BM25Index:
    """Okapi BM25 over chunks. `docs` = [(chunk_id, text, metadata)]."""

    def __init__(self, docs: Iterable[tuple[str, str, dict]], k1: float = 1.5, b: float = 0.75) -> None:
        self.ids: list[str] = []
        self.texts: list[str] = []
        self.metas: list[dict] = []
        self.lengths: list[int] = []
        # term -> (doc indices, term frequencies) as compact int arrays: ~10k chunks give millions of postings,
        # and dict-of-dict postings took ~240 MB (2026-11-18 measurement on synthetic text of the real size).
        build: dict[str, tuple[array, array]] = defaultdict(lambda: (array("I"), array("H")))
        for i, (cid, text, md) in enumerate(docs):
            toks = tokenize(text)
            self.ids.append(cid)
            self.texts.append(text)
            self.metas.append(md or {})
            self.lengths.append(len(toks))
            for tok, tf in Counter(toks).items():
                idx, tfs = build[tok]
                idx.append(i)
                tfs.append(min(tf, 65535))
        self.postings: dict[str, tuple[array, array]] = dict(build)
        n = len(self.ids)
        self.avgdl = (sum(self.lengths) / n) if n else 0.0
        self.idf = {t: math.log(1 + (n - len(p[0]) + 0.5) / (len(p[0]) + 0.5)) for t, p in self.postings.items()}
        self.k1, self.b = k1, b

    def __len__(self) -> int:
        return len(self.ids)

    def search(self, query: str, n: int, **filters: str | None) -> list[tuple[str, str, dict, float]]:
        """Top n (chunk_id, text, metadata, score) among chunks passing the filters; score > 0 only."""
        scores: dict[int, float] = defaultdict(float)
        for tok in set(tokenize(query)):
            post = self.postings.get(tok)
            if not post:
                continue
            idf = self.idf[tok]
            for i, tf in zip(*post):
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avgdl or 1))
                scores[i] += idf * tf * (self.k1 + 1) / norm
        ranked = sorted((i for i in scores if matches(self.metas[i], **filters)), key=lambda i: -scores[i])[:n]
        return [(self.ids[i], self.texts[i], self.metas[i], scores[i]) for i in ranked]


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: score(id) = sum over lists of 1 / (k + rank). Higher first; ties keep first-seen order."""
    score: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            score[cid] = score.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda kv: -kv[1])


RERANK_PROMPT = """당신은 검색 결과의 순서를 정하는 평가자다. 질문에 답하는 데 직접 필요한 내용을 담은 청크를 앞에 둔다.
- 질문의 법인·연도·항목과 맞는 청크를 우선한다. 같은 단어만 겹치고 질문과 무관한 청크는 뒤로 보낸다.
- 청크 내용을 바꾸거나 요약하지 않는다. 번호만 고른다.
아래 JSON 하나만 출력한다: {"order": [관련성이 높은 순서의 청크 번호, 최대 N개]}"""

ChatText = Callable[[list[dict[str, str]]], str]


def llm_rerank(chat: ChatText, question: str, candidates: list[tuple[str, dict]], top_n: int,
               preview_chars: int = 700) -> list[int] | None:
    """Indices into `candidates` in the model's order (at most top_n), or None if the answer is unusable.

    Unknown or repeated indices are dropped. The caller falls back to the fused order on None.
    """
    blocks = []
    for i, (text, md) in enumerate(candidates, 1):
        head = f"{md.get('report_nm')} | {md.get('rcept_dt')} | 절: {md.get('section_title')}"
        blocks.append(f"[청크 {i}] {head}\n{' '.join(text.split())[:preview_chars]}")
    messages = [{"role": "system", "content": RERANK_PROMPT.replace("N개", f"{top_n}개")},
                {"role": "user", "content": f"질문: {question}\n\n" + "\n\n".join(blocks)}]
    try:
        m = re.search(r"\{.*\}", chat(messages) or "", re.S)
        order = json.loads(m.group(0))["order"] if m else None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(order, list):
        return None
    seen: list[int] = []
    for x in order:
        try:
            i = int(x) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(candidates) and i not in seen:
            seen.append(i)
    return seen[:top_n] or None


def fuse(vector_hits: list[tuple[str, str, dict, float]], keyword_hits: list[tuple[str, str, dict, float]],
         k: int, rrf_k: int = 60) -> list[tuple[str, dict, float | None]]:
    """Top k (text, metadata, vector relevance or None) by RRF of the two ranked lists (chunk_id keyed)."""
    by_id: dict[str, tuple[str, dict]] = {}
    vec_score: dict[str, float] = {}
    for cid, text, md, s in vector_hits:
        by_id[cid] = (text, md)
        vec_score[cid] = s
    for cid, text, md, _ in keyword_hits:
        by_id.setdefault(cid, (text, md))
    fused = rrf([[h[0] for h in vector_hits], [h[0] for h in keyword_hits]], rrf_k)[:k]
    return [(by_id[cid][0], by_id[cid][1], vec_score.get(cid)) for cid, _ in fused]


def index_from_store(store: Any) -> BM25Index:
    """BM25 over every chunk in the Chroma collection (same ids/metadata as the vector search)."""
    got = store.get(include=["documents", "metadatas"])
    return BM25Index(zip(got["ids"], got["documents"], got["metadatas"]))
