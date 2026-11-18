"""Hybrid retrieval: BM25 tokenisation/ranking/filters, RRF fusion, LLM rerank parsing, pipeline modes."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.rag.hybrid import BM25Index, fuse, llm_rerank, rrf, tokenize
from src.rag.rag_pipeline import RagPipeline


def md(cid, company="deloitte", role="own", ymd=20260630, section="본문"):
    return {"chunk_id": cid, "company_id": company, "filing_role": role, "rcept_ymd": ymd,
            "report_nm": "회계법인사업보고서", "rcept_dt": "2026-06-30", "section_title": section}


DOCS = [
    ("c1", "VI. 감리결과 및 조치 내용 증권선물위원회 조치 없음", md("c1", section="VI. 감리결과")),
    ("c2", "가. 인력 현황 공인회계사 수 3,018명", md("c2", company="pwc")),
    ("c3", "품질관리 및 독립성 규정 교육", md("c3")),
    ("c4", "감리결과 조치 내용 (고객사 보고서)", md("c4", role="audit_client", ymd=20260315)),
]


def test_tokenize_bigrams_hangul_and_keeps_numbers():
    assert tokenize("감리결과 조치") == ["감리", "리결", "결과", "조치"]
    assert tokenize("IFRS 3,018명 수") == ["ifrs", "3", "018", "명", "수"]


def test_bm25_ranks_shared_terms_and_applies_filters():
    idx = BM25Index(DOCS)
    top = idx.search("감독기관 조치 감리결과", 3)
    assert top[0][0] in {"c1", "c4"}
    own_only = idx.search("감리결과 조치", 3, filing_role="own")
    assert [h[0] for h in own_only][0] == "c1" and all(h[2]["filing_role"] == "own" for h in own_only)
    assert idx.search("감리결과", 3, company_id="pwc") == []           # pwc chunk has no shared term
    assert idx.search("감리결과", 3, date_to="2026-03-31")[0][0] == "c4"  # date filter on rcept_ymd


def test_rrf_and_fuse_merge_two_rankings():
    assert [cid for cid, _ in rrf([["a", "b"], ["b", "c"]])] == ["b", "a", "c"]
    vec = [("v1", "t1", {"chunk_id": "v1"}, 0.7), ("both", "tb", {"chunk_id": "both"}, 0.6)]
    kw = [("both", "tb", {"chunk_id": "both"}, 5.0), ("k1", "tk", {"chunk_id": "k1"}, 4.0)]
    out = fuse(vec, kw, 3)
    assert [o[1]["chunk_id"] for o in out] == ["both", "v1", "k1"]
    assert out[0][2] == 0.6 and out[2][2] is None          # keyword-only chunk has no vector relevance


def test_llm_rerank_parses_order_and_rejects_garbage():
    cands = [("a", {}), ("b", {}), ("c", {})]
    assert llm_rerank(lambda m: json.dumps({"order": [3, 1, 3, 9, "x"]}), "q", cands, 2) == [2, 0]
    assert llm_rerank(lambda m: "no json", "q", cands, 2) is None
    assert llm_rerank(lambda m: json.dumps({"order": []}), "q", cands, 2) is None


class Store:
    def __init__(self, hits):
        self.hits = hits

    def similarity_search_with_score(self, query, k, filter=None):
        return [(SimpleNamespace(page_content=t, metadata=m), 1.0 - s) for t, m, s in self.hits][:k]


def chat_log(answer, rerank=None):
    calls = []

    def chat(messages):
        calls.append(messages[0]["content"][:20])
        if "순서를 정하는 평가자" in messages[0]["content"]:
            return json.dumps(rerank or {"order": [1]})
        return json.dumps(answer, ensure_ascii=False)
    return chat, calls


def test_hybrid_adds_keyword_hit_vector_missed_and_gate_uses_vector_score():
    # vector search returns only the quality chunk; the 감리결과 chunk comes from BM25
    vec_hits = [(DOCS[2][1], DOCS[2][2], 0.62)]
    chat, calls = chat_log({"answer": "조치 없음", "citations": [{"source": 2, "quote": "증권선물위원회 조치 없음"}]})
    pipe = RagPipeline(Store(vec_hits), chat, k=2, min_relevance=0.5, retrieval="hybrid", bm25=BM25Index(DOCS))
    hits, top = pipe.search("감리결과 조치 내역", filing_role="own")
    assert top == 0.62 and {h[1]["chunk_id"] for h in hits} == {"c1", "c3"}
    a = pipe.ask("감리결과 조치 내역", filing_role="own")
    assert not a.refused and a.citations[0].metadata["chunk_id"] == "c1"


def test_rerank_called_only_when_not_refused():
    low = [(DOCS[2][1], DOCS[2][2], 0.30)]
    chat, calls = chat_log({"answer": "x"})
    pipe = RagPipeline(Store(low), chat, k=2, min_relevance=0.5, retrieval="hybrid_rerank", bm25=BM25Index(DOCS))
    assert pipe.ask("감리결과").refused and calls == []           # refused by vector gate: no rerank, no answer call
    ok = [(DOCS[2][1], DOCS[2][2], 0.70)]
    chat, calls = chat_log({"answer": "x", "citations": []}, rerank={"order": [2]})
    pipe = RagPipeline(Store(ok), chat, k=1, min_relevance=0.5, retrieval="hybrid_rerank", bm25=BM25Index(DOCS))
    hits, _ = pipe.search("감리결과")
    assert len(hits) == 1 and len(calls) == 1                     # one rerank call, k kept


def test_bad_mode_and_missing_index_rejected():
    with pytest.raises(ValueError):
        RagPipeline(Store([]), lambda m: "", retrieval="bm25only")
    with pytest.raises(ValueError):
        RagPipeline(Store([]), lambda m: "", retrieval="hybrid")


def test_compare_writes_per_mode_and_per_set_table(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import evaluate_rag as ev

    monkeypatch.setattr(ev, "OUT_DIR", tmp_path)
    base = {"reason": "", "top_score": "", "retrieved": "8", "audit_client_cited": "0", "answer": "", "citations": "[]",
            "human_note": "", "question": "q"}
    def rows(correct):
        return [{**base, "id": "Q04", "set": "base", "answerable": "1", "refused": "0", "refusal_ok": "1",
                 "verified_citations": "1", "human_correct": correct},
                {**base, "id": "H05", "set": "holdout", "answerable": "0", "refused": "1", "refusal_ok": "1",
                 "verified_citations": "0", "human_correct": ""}]
    ev.save_answers(rows("0"), tmp_path / "answers_vector.csv")
    ev.save_answers(rows("1"), tmp_path / "answers_hybrid.csv")
    out = tmp_path / "cmp.md"
    ev.compare(out)
    text = out.read_text(encoding="utf-8")
    assert "| vector | base | 0/0 | 1/1 | 0/1 |" in text and "| hybrid | base | 0/0 | 1/1 | 1/1 |" in text
    assert "| hybrid | holdout | 1/1 | 0/0 | 0/미판정 |" in text
    assert "| Q04 | 0 | 1 |" in text and "| H05 | 거 | 거 |" in text
    assert ev.paths_for("hybrid")[0].name == "answers_hybrid.csv" and ev.paths_for(None)[0].name == "answers.csv"


def test_compare_refuses_misplaced_labels(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import evaluate_rag as ev

    monkeypatch.setattr(ev, "OUT_DIR", tmp_path)
    row = {"id": "Q04", "set": "base", "answerable": "1", "refused": "1", "refusal_ok": "0", "reason": "", "top_score": "",
           "retrieved": "8", "verified_citations": "0", "audit_client_cited": "0", "answer": "", "citations": "[]",
           "human_correct": "1", "human_note": "", "question": "q"}
    ev.save_answers([row], tmp_path / "answers_vector.csv")
    with pytest.raises(SystemExit):
        ev.compare(tmp_path / "cmp.md")
    assert not (tmp_path / "cmp.md").exists()
