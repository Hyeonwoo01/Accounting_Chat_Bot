"""Day 5 RAG: chunking, vector store idempotency, refusal and citation verification (no API calls)."""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from src.rag.document_processor import chunk_document, client_name, split_sections, windows
from src.rag.rag_pipeline import REFUSAL, RagPipeline, build_messages, format_citation, quote_in_chunk

BIG4 = {"00260295"}
NAMES = {"pwc": "삼일회계법인"}

OWN_TEXT = (
    "회계법인사업보고서\n"
    "## 1. 회계법인의 개요\n삼일회계법인은 1971년 설립되었다.\n"
    "## 2. 인력 현황\n공인회계사 수는 2,500명이다.\n"
)
AUDIT_TEXT = (
    "독립된 감사인의 감사보고서 주식회사 가나전자의 주주 및 이사회 귀중\n"
    "## 감사의견\n우리는 주식회사 가나전자의 재무제표를 감사하였습니다. 적정의견을 표명합니다.\n"
    "## 핵심감사사항\n수익인식의 기간귀속이 핵심감사사항입니다.\n"
)


def own_doc() -> dict:
    return {"doc_id": "DART_20260301000001", "content": OWN_TEXT, "corp_code": "00260295", "company_ids": ["pwc"],
            "rcept_no": "20260301000001", "report_nm": "회계법인사업보고서 (2025.03)", "rcept_dt": "2026-03-01",
            "pblntf_ty": "", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260301000001",
            "source_name": "삼일회계법인"}


def audit_doc() -> dict:
    return {"doc_id": "DART_20260315000002", "content": AUDIT_TEXT, "corp_code": "00999999", "company_ids": ["pwc"],
            "rcept_no": "20260315000002", "report_nm": "감사보고서 (2025.12)", "rcept_dt": "2026-03-15",
            "pblntf_ty": "F", "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000002",
            "source_name": "삼일회계법인"}


# ---------- document_processor ----------

def test_split_sections_keeps_preamble_and_titles():
    parts = split_sections(OWN_TEXT)
    assert [t for t, _ in parts] == ["(머리말)", "1. 회계법인의 개요", "2. 인력 현황"]


def test_windows_overlap_and_cover_everything():
    body = "\n".join(f"line {i:03d} " + "x" * 40 for i in range(60))
    parts = windows(body, size=300, overlap=60)
    assert len(parts) > 1 and all(len(p) <= 300 for p in parts)
    assert "line 000" in parts[0] and "line 059" in parts[-1]
    assert windows("short", 300, 60) == ["short"]


def test_client_name_from_addressee_line():
    assert client_name(AUDIT_TEXT) == "주식회사 가나전자"
    assert client_name("주식회사 다라 의 주 주 및 이 사 회 귀 중") == "주식회사 다라"
    assert client_name("no addressee") == ""


def test_chunk_metadata_role_and_scalar_values():
    own = chunk_document(own_doc(), BIG4, NAMES)
    client = chunk_document(audit_doc(), BIG4, NAMES)
    assert own[0].metadata["filing_role"] == "own" and own[0].metadata["client_name"] == ""
    assert client[0].metadata["filing_role"] == "audit_client"
    assert client[0].metadata["client_name"] == "주식회사 가나전자"
    md = client[0].metadata
    for key in ("doc_id", "chunk_id", "corp_code", "company_id", "rcept_no", "report_nm", "rcept_dt",
                "pblntf_ty", "section_title", "source_url", "filing_role"):
        assert key in md
    assert md["rcept_ymd"] == 20260315
    assert all(isinstance(v, (str, int, float, bool)) for c in own + client for v in c.metadata.values())
    assert [c.chunk_id for c in own] == [f"DART_20260301000001#{i:04d}" for i in range(len(own))]
    assert chunk_document({**own_doc(), "content": "  "}, BIG4, NAMES) == []


# ---------- rag_pipeline with a fake store ----------

class FakeStore:
    def __init__(self, hits):
        self.hits = hits
        self.filters = []

    def similarity_search_with_score(self, query, k, filter=None):  # returns cosine distance
        self.filters.append(filter)
        return [(SimpleNamespace(page_content=t, metadata=md), 1.0 - s) for t, md, s in self.hits][:k]


def hits_from(doc, score=0.8):
    return [(c.text, c.metadata, score) for c in chunk_document(doc, BIG4, NAMES)]


def chat_returning(payload, calls=None):
    def chat(messages):
        if calls is not None:
            calls.append(messages)
        return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return chat


def test_low_similarity_refuses_without_calling_llm():
    calls = []
    pipe = RagPipeline(FakeStore(hits_from(own_doc(), 0.1)), chat_returning({"answer": "x"}, calls), min_relevance=0.35)
    a = pipe.ask("질문")
    assert a.refused and a.answer == REFUSAL and calls == []
    empty = RagPipeline(FakeStore([]), chat_returning({"answer": "x"}, calls)).ask("질문")
    assert empty.refused and calls == []


def test_verified_quote_is_kept_with_citation_metadata():
    hits = hits_from(own_doc())
    n = next(i for i, (t, *_) in enumerate(hits, 1) if "2,500명" in t)
    pipe = RagPipeline(FakeStore(hits), chat_returning(
        {"answer": "2,500명입니다.", "citations": [{"source": n, "quote": "공인회계사 수는  2,500명이다."}]}))
    a = pipe.ask("공인회계사 수는?")
    assert not a.refused and a.answer == "2,500명입니다."
    assert len(a.citations) == 1 and a.citations[0].metadata["rcept_no"] == "20260301000001"
    block = format_citation(1, a.citations[0])
    for part in ("공시명:", "제출일: 2026-03-01", "접수번호: 20260301000001", "제출 법인: 삼일회계법인",
                 "rcpNo=20260301000001", '관련 근거: "공인회계사 수는 2,500명이다."'):
        assert part in block


def test_unverifiable_quotes_lead_to_refusal():
    hits = hits_from(own_doc())
    for cites in ([{"source": 1, "quote": "공인회계사 수는 3,000명이다."}],  # not in chunk
                  [{"source": 99, "quote": "공인회계사 수는 2,500명이다."}],  # bad index
                  [{"source": 3, "quote": "2,500"}],                         # too short to verify
                  []):
        a = RagPipeline(FakeStore(hits), chat_returning({"answer": "답", "citations": cites})).ask("q")
        assert a.refused and a.answer == REFUSAL


def test_model_refusal_and_bad_json_refuse():
    hits = hits_from(own_doc())
    assert RagPipeline(FakeStore(hits), chat_returning({"refuse": True, "reason": "없음"})).ask("q").refused
    assert RagPipeline(FakeStore(hits), chat_returning("not json")).ask("q").refused


def test_audit_client_citation_names_client_and_auditor():
    hits = hits_from(audit_doc())
    msgs = build_messages("q", [(t, md) for t, md, _ in hits])
    assert "고객사: 주식회사 가나전자" in msgs[1]["content"] and "감사인: 삼일회계법인" in msgs[1]["content"]
    n = next(i for i, (t, *_) in enumerate(hits, 1) if "수익인식" in t)
    a = RagPipeline(FakeStore(hits), chat_returning(
        {"answer": "가나전자 감사보고서", "citations": [{"source": n, "quote": "수익인식의 기간귀속이 핵심감사사항입니다."}]})).ask("q")
    assert "고객사(감사 대상): 주식회사 가나전자 / 감사인(제출): 삼일회계법인" in format_citation(1, a.citations[0])


def test_filters_are_passed_as_chroma_where():
    store = FakeStore([])
    pipe = RagPipeline(store, chat_returning({}))
    pipe.retrieve("q", company_id="pwc")
    pipe.retrieve("q", company_id="pwc", date_from="2026-03-01", date_to="2026-03-31")
    pipe.retrieve("q")
    pipe.retrieve("q", company_id="pwc", filing_role="own")
    with pytest.raises(ValueError):
        pipe.retrieve("q", filing_role="client")
    assert store.filters[0] == {"company_id": "pwc"}
    assert store.filters[3] == {"$and": [{"company_id": "pwc"}, {"filing_role": "own"}]}
    assert store.filters[1] == {"$and": [{"company_id": "pwc"}, {"rcept_ymd": {"$gte": 20260301}},
                                         {"rcept_ymd": {"$lte": 20260331}}]}
    assert store.filters[2] is None


def test_quote_in_chunk_ignores_whitespace_only():
    assert quote_in_chunk("공인회계사 수는\n2,500명이다", "공인회계사 수는 2,500명이다.")
    assert not quote_in_chunk("공인회계사 수는 2,600명이다", "공인회계사 수는 2,500명이다.")


# ---------- real Chroma with fake embeddings ----------

def test_vector_store_idempotent_filter_and_delete(tmp_path):
    pytest.importorskip("langchain_chroma")
    from langchain_core.embeddings import DeterministicFakeEmbedding

    from src.rag.vector_store import EmbeddingModelMismatch, add_chunks, delete_document, open_store

    store = open_store(tmp_path / "vdb", DeterministicFakeEmbedding(size=64), "fake-64")
    own, client = chunk_document(own_doc(), BIG4, NAMES), chunk_document(audit_doc(), BIG4, NAMES)
    assert add_chunks(store, own + client) == len(own) + len(client)
    assert add_chunks(store, own + client) == 0  # re-run embeds nothing

    pipe = RagPipeline(store, chat_returning({}), k=3)
    target = own[-1].text
    hits = pipe.retrieve(target)
    assert hits[0][0] == target and hits[0][2] == pytest.approx(1.0, abs=1e-3)  # cosine relevance
    dated = pipe.retrieve(target, date_from="2026-03-10")
    assert dated and all(md["rcept_ymd"] >= 20260310 for _, md, _ in dated)

    delete_document(store, own[0].doc_id)
    assert store.get(where={"doc_id": own[0].doc_id}, include=[])["ids"] == []
    assert add_chunks(store, own) == len(own)

    with pytest.raises(EmbeddingModelMismatch):
        open_store(tmp_path / "vdb", DeterministicFakeEmbedding(size=64), "another-model")


def test_replace_chunks_round_trip():
    from src.database.db_manager import fetch_chunk_texts, init_db, replace_chunks

    conn = sqlite3.connect(":memory:")
    init_db(conn)
    conn.execute("PRAGMA foreign_keys = OFF")
    chunks = [vars(c) for c in chunk_document(own_doc(), BIG4, NAMES)]
    replace_chunks(conn, "DART_20260301000001", chunks)
    replace_chunks(conn, "DART_20260301000001", chunks[:1])
    assert fetch_chunk_texts(conn, "DART_20260301000001") == [chunks[0]["text"]]


# ---------- boilerplate removal and client names ----------

STANDARD = ("우리의 결론은 감사보고서일까지 입수된 감사증거에 기초하나, 미래의 사건이나 상황이 회사의 "
            "계속기업으로서 존속을 중단시킬 수 있습니다.")


def test_boilerplate_found_across_reports_and_removed():
    from src.rag.document_processor import find_boilerplate, strip_boilerplate

    texts = [f"## 감사의견\n주식회사 회사{i}의 재무제표는 적정합니다. {STANDARD}\n- {STANDARD}" for i in range(3)]
    boiler = find_boilerplate(texts, min_docs=3, min_chars=30)
    assert len(boiler) == 1  # same sentence despite '-' bullet; counted once per document
    assert find_boilerplate(texts, min_docs=4, min_chars=30) == set()
    out = strip_boilerplate(texts[0], boiler)
    assert "계속기업" not in out and "## 감사의견" in out and "회사0의 재무제표는 적정합니다." in out
    # short headings are never boilerplate
    assert find_boilerplate(["감사의견\n"] * 5, min_docs=2, min_chars=30) == set()


def test_chunk_document_strips_boilerplate_only_from_client_reports():
    from src.rag.document_processor import find_boilerplate

    boiler = find_boilerplate([AUDIT_TEXT + STANDARD] * 3, min_docs=3, min_chars=30)
    client = chunk_document({**audit_doc(), "content": AUDIT_TEXT + STANDARD}, BIG4, NAMES, boilerplate=boiler)
    own = chunk_document({**own_doc(), "content": OWN_TEXT + STANDARD}, BIG4, NAMES, boilerplate=boiler)
    assert not any("계속기업" in c.text for c in client)
    assert any("계속기업" in c.text for c in own)


def test_client_name_prefers_listing_name_and_handles_line_break():
    named = chunk_document(audit_doc(), BIG4, NAMES, client_names={"20260315000002": "가나전자"})
    assert named[0].metadata["client_name"] == "가나전자"
    assert client_name("감사보고서\n주식회사 다라\n주주 및 이사회 귀중") == "주식회사 다라"


# ---------- short-section merge and context line ----------

def test_merge_short_sections_folds_cover_into_next_and_tail_into_previous():
    from src.rag.document_processor import merge_short_sections

    parts = [("(머리말)", "회계법인사업보고서 삼정회계법인"), ("4. 경력 현황", "x" * 250), ("5. 기타", "짧음")]
    out = merge_short_sections(parts, min_chars=200)
    assert len(out) == 1
    title, body = out[0]
    assert title == "(머리말) / 4. 경력 현황 / 5. 기타"
    assert body.startswith("회계법인사업보고서") and "[4. 경력 현황] x" in body and "[5. 기타] 짧음" in body
    assert merge_short_sections(parts, min_chars=0) == parts


def test_every_chunk_carries_context_line():
    own = chunk_document(own_doc(), BIG4, NAMES)
    client = chunk_document(audit_doc(), BIG4, NAMES, client_names={"20260315000002": "가나전자"})
    assert own[-1].text.startswith("[삼일회계법인 회계법인사업보고서 (2025.03) | 2. 인력 현황]\n")
    assert client[0].text.startswith("[가나전자 감사보고서 (2025.12) (감사인 삼일회계법인) |")
    merged = chunk_document(own_doc(), BIG4, NAMES, min_section_chars=200)
    assert len(merged) == 1 and "[2. 인력 현황] 공인회계사 수는 2,500명이다." in merged[0].text


# ---------------------------------------------------------------- firm named in the question

def test_detect_firms_ignores_spacing_and_rejects_partial_latin():
    from src.common.config import load_company_mapping
    from src.rag.rag_pipeline import detect_firms, firm_aliases

    al = firm_aliases(load_company_mapping())
    assert detect_firms("딜로이트안진회계법인 사업보고서의 사업부문별 인원은?", al) == {"deloitte"}
    assert detect_firms("딜로이트 안진 회계법인 사업보고서의 사업부문별 인원은?", al) == {"deloitte"}
    assert detect_firms("삼 정 회계법인의 감사실적", al) == {"kpmg"}
    assert detect_firms("삼정KPMG와 삼일PwC의 인원 비교", al) == {"kpmg", "pwc"}
    assert detect_firms("EY한영 품질관리", al) == {"ey"}
    assert detect_firms("key audit matters survey", al) == set()  # 'ey' inside a word is not EY
    assert detect_firms("회계법인 사업보고서의 감사실적은?", al) == set()


def test_ask_filters_by_the_single_firm_named_in_the_question():
    from src.rag.rag_pipeline import RagPipeline

    al = {"deloitte": ["안진회계법인", "딜로이트안진"], "kpmg": ["삼정회계법인"]}
    store = FakeStore([])
    pipe = RagPipeline(store, chat_returning({}), firm_aliases=al)
    a = pipe.ask("딜로이트안진회계법인 사업부문별 인원은?", filing_role="own")
    assert a.auto_company_id == "deloitte"
    assert store.filters[-1] == {"$and": [{"company_id": "deloitte"}, {"filing_role": "own"}]}
    # a selection left from an earlier question loses to the firm named in the question, and is reported
    b = pipe.ask("안진회계법인사업보고서에기재된인원은?", company_id="kpmg", filing_role="own")
    assert (b.auto_company_id, b.replaced_company_id) == ("deloitte", "kpmg")
    assert store.filters[-1] == {"$and": [{"company_id": "deloitte"}, {"filing_role": "own"}]}
    s = pipe.ask("안진회계법인 질문", company_id="deloitte")  # same firm selected: nothing to report
    assert (s.auto_company_id, s.replaced_company_id) == (None, None)
    n = pipe.ask("회계법인 감사실적은?", company_id="kpmg")  # no firm in the question: selection kept
    assert n.replaced_company_id is None and store.filters[-1] == {"company_id": "kpmg"}
    c = pipe.ask("안진회계법인과 삼정회계법인 비교")  # two firms -> no filter
    assert c.auto_company_id is None and store.filters[-1] is None


def test_split_sections_keeps_parent_heading_path():
    text = ("## II. 주요사업의 내용\n## 1. 사업의 개요\n감사·세무·자문.\n"
            "## 2. 외감법에 의한 감사실적\n## 가. 감사실적 총괄표\n상장 86 72\n"
            "## 나. 개별재무제표 감사실적\n(1) 자산규모별\n## 3. 국내자본시장 상장 외국법인\n없음\n"
            "## 독립된 감사인의 감사보고서\n의견\n")
    titles = [t for t, _ in split_sections(text)]
    assert titles == [
        "II. 주요사업의 내용 > 1. 사업의 개요",
        "II. 주요사업의 내용 > 2. 외감법에 의한 감사실적 > 가. 감사실적 총괄표",
        "II. 주요사업의 내용 > 2. 외감법에 의한 감사실적 > 나. 개별재무제표 감사실적",
        "II. 주요사업의 내용 > 3. 국내자본시장 상장 외국법인",
        "독립된 감사인의 감사보고서",
    ]


def test_quote_check_ignores_spacing_but_not_characters():
    chunk = "[안진회계법인 | 가. 감사실적 총괄표] 유가증권시장 상장법인 86 72 합계 791 768"
    assert quote_in_chunk("유가증권시장상장법인 86 72", chunk)
    assert quote_in_chunk("합계 791\n768", chunk)
    assert not quote_in_chunk("유가증권시장 상장법인 86사", chunk)  # unit added by the model
    assert not quote_in_chunk("합계 791", chunk)  # too short to verify anything


def test_rejected_quotes_are_reported_for_diagnosis():
    hits = hits_from(own_doc())
    a = RagPipeline(FakeStore(hits), chat_returning(
        {"answer": "답", "citations": [{"source": 1, "quote": "원문에 없는 긴 인용 구절입니다"}, {"source": "x", "quote": "q"}]})).ask("q")
    assert a.refused and "2개 모두 불일치" in a.reason
    assert a.rejected_quotes[0].startswith("[근거 1] 원문에 없는") and a.rejected_quotes[1].startswith("(근거 번호 없음)")


def test_quote_context_returns_original_text_around_a_respaced_quote():
    from src.rag.rag_pipeline import quote_context

    chunk = "개별재무제표감사보고서 상장법인 86 72 합계 791 768 연결재무제표감사보고서 상장법인 78 61 합계 245 230"
    before, match, after = quote_context("합계 245  230", chunk, width=30)
    assert match == "합계 245 230" and "연결재무제표감사보고서" in before and after == ""
    before, match, after = quote_context("합계791 768", chunk, width=20)
    assert match == "합계 791 768" and before.endswith("86 72") and after.startswith("연결재무제표")
    assert quote_context("없는 구절입니다", chunk) == ("", "없는 구절입니다", "")


def test_eval_row_keeps_citations_and_puts_answer_on_one_line():
    import json as _json
    import sys as _sys
    from pathlib import Path as _P

    _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "scripts"))
    import evaluate_rag as ev
    from src.rag.rag_pipeline import Answer, Citation

    md = {"rcept_no": "1", "report_nm": "보고서", "rcept_dt": "2026-01-01", "filing_role": "own", "client_name": ""}
    row = ev.row_for({"id": "Q01", "answerable": True, "question": "q"},
                     Answer("q", "첫 줄\n\n- 둘째 줄", [Citation(1, "인용 구절입니다", md)]))
    assert row["answer"] == "첫 줄 / - 둘째 줄"
    assert _json.loads(row["citations"])[0]["quote"] == "인용 구절입니다"
    assert set(ev.FIELDS) == set(row)


def test_score_rejects_labels_typed_one_row_off():
    import sys as _sys
    from pathlib import Path as _P

    _sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "scripts"))
    import evaluate_rag as ev

    rows = [{"id": "Q04", "answerable": "1", "refused": "1", "human_correct": "1", "human_note": ""},
            {"id": "Q06", "answerable": "1", "refused": "0", "human_correct": "", "human_note": ""},
            {"id": "Q09", "answerable": "0", "refused": "1", "human_correct": "1", "human_note": ""},
            {"id": "Q01", "answerable": "1", "refused": "0", "human_correct": "1", "human_note": ""}]
    problems = ev.label_problems(rows)
    assert [p.split(":")[0] for p in problems] == ["Q04", "Q06", "Q09"]


def test_fuzzy_firm_fixes_small_typos_without_guessing():
    import time

    from src.common.config import load_company_mapping
    from src.rag.rag_pipeline import fuzzy_firm, firm_aliases

    al = firm_aliases(load_company_mapping())
    q = "삼이회계법인사업보고서에기재된공인회계사수"
    company, (a, b), alias = fuzzy_firm(q, al)
    assert company == "pwc" and q[a:b] == "삼이회계법인" and alias == "삼일회계법인"  # 이->일 is 1 jamo, 이->정 is 3
    assert fuzzy_firm("삼정회게법인 감사실적", al)[0] == "kpmg"
    assert fuzzy_firm("한영 회계 범인 품질관리", al)[0] == "ey"  # spacing ignored, 법->범 one jamo
    for q in ["다른 회계법인의 감사실적은?", "회계법인 사업보고서의 감사실적", "내년 채용 계획은?"]:
        assert fuzzy_firm(q, al) is None, q
    t = time.perf_counter()
    fuzzy_firm("회계법인 사업보고서에 기재된 사업부문별 인원과 보수, 감사실적, 품질관리 체계는 어떻게 되는가?" * 2, al)
    assert time.perf_counter() - t < 1.0  # runs on every question without a firm name


def test_ask_uses_corrected_name_and_reports_it():
    from src.rag.rag_pipeline import RagPipeline

    al = {"pwc": ["삼일회계법인"], "kpmg": ["삼정회계법인"]}
    store, seen = FakeStore([]), []

    class Recording(FakeStore):
        def similarity_search_with_score(self, query, k, filter=None):
            seen.append(query)
            return super().similarity_search_with_score(query, k, filter)

    store = Recording([])
    a = RagPipeline(store, chat_returning({}), firm_aliases=al).ask("삼이회계법인 공인회계사 수", company_id="kpmg")
    assert a.typo_fixed == ("삼이회계법인", "삼일회계법인")
    assert (a.auto_company_id, a.replaced_company_id) == ("pwc", "kpmg")
    assert seen[-1] == "삼일회계법인 공인회계사 수" and a.question == "삼이회계법인 공인회계사 수"


def test_unsupported_numbers_flags_only_numbers_missing_from_evidence():
    from src.rag.rag_pipeline import unsupported_numbers

    from src.rag.rag_pipeline import metadata_numbers

    ev = ["공인회계사 수는 3,018명이다. 회계감사 33.03%", "표 합계 791 768"]
    meta = metadata_numbers({"rcept_dt": "2026-06-30", "rcept_no": "20260630000123", "report_nm": "회계법인사업보고서 (2026.06)"})
    assert {"2026", "2026.06", "20260630", "20260630000123"} <= meta and "30" not in meta and "06" not in meta
    assert unsupported_numbers("3,018명이고 회계감사 비중은 33.03%입니다.", ev, meta) == []
    assert unsupported_numbers("2026.06 보고서 기준 합계는 791개입니다.", ev, meta) == []  # filing date + table cell
    assert unsupported_numbers("3,018명이며 전년 대비 30% 늘었습니다. 30%는 큰 폭입니다.", ev, meta) == ["30"]
    assert unsupported_numbers("4개 법인 중 1곳입니다.", ev) == []                     # single digits skipped
    assert unsupported_numbers("약 5,686억 원입니다.", []) == ["5,686"]


def test_answer_numbers_not_in_cited_chunk_are_flagged():
    hits = hits_from(own_doc())
    n = next(i for i, (t, *_) in enumerate(hits, 1) if "2,500명" in t)
    cite = [{"source": n, "quote": "공인회계사 수는  2,500명이다."}]
    ok = RagPipeline(FakeStore(hits), chat_returning({"answer": "2,500명입니다.", "citations": cite})).ask("공인회계사 수는?")
    assert not ok.refused and ok.unverified_numbers == []
    bad = RagPipeline(FakeStore(hits), chat_returning(
        {"answer": "2,500명으로 전년보다 120명 늘었습니다.", "citations": cite})).ask("공인회계사 수는?")
    assert not bad.refused and bad.unverified_numbers == ["120"]
    asked = RagPipeline(FakeStore(hits), chat_returning(
        {"answer": "2025년 기준 2,500명입니다.", "citations": cite})).ask("2025년 공인회계사 수는?")
    assert asked.unverified_numbers == []                                               # number from the question


def test_malformed_model_output_refuses_instead_of_crashing():
    hits = hits_from(own_doc())
    n = next(i for i, (t, *_) in enumerate(hits, 1) if "2,500명" in t)
    good = {"source": n, "quote": "공인회계사 수는  2,500명이다."}
    for payload in ('["not", "an", "object"]',
                    {"answer": ["목록"], "citations": [good]},
                    {"answer": 2500, "citations": [good]},
                    {"answer": "   ", "citations": [good]},
                    {"answer": "답", "citations": "문자열"},
                    {"answer": "답", "citations": ["문자열", 3]}):
        a = RagPipeline(FakeStore(hits), chat_returning(payload)).ask("q")
        assert a.refused and a.answer == REFUSAL
    mixed = RagPipeline(FakeStore(hits), chat_returning({"answer": "2,500명입니다.", "citations": ["x", good]})).ask("q")
    assert not mixed.refused and len(mixed.citations) == 1                              # bad item skipped, not fatal
