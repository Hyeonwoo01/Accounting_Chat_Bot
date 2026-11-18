from __future__ import annotations

import json

import pandas as pd
import pytest

from src.analysis import llm_classifier as lc
from src.analysis import validation as v
from src.database import db_manager

GOOD = json.dumps({"sentiment": "positive", "primary_category": "technology_trend",
                   "issue_keywords": ["생성형 AI"], "issue_summary": "AI 감사 도구를 도입했다."}, ensure_ascii=False)


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, messages):
        self.calls.append(messages)
        return lc.LLMReply(text=self.replies.pop(0), model="fake-model-2026")


# ---------------------------------------------------------------- classifier

def test_parse_result_validates_schema():
    assert lc.parse_result(f"```json\n{GOOD}\n```")["primary_category"] == "technology_trend"
    for bad in ['{"sentiment": "happy"}', "no json",
                GOOD.replace('"technology_trend"', '"tech"'),
                GOOD.replace('["생성형 AI"]', '["a", "b", "c", "d"]'),
                GOOD.replace('"AI 감사 도구를 도입했다."', '""')]:
        with pytest.raises(lc.ClassificationError):
            lc.parse_result(bad)


def test_classify_retries_once_then_fails():
    chat = FakeChat(["oops", GOOD])
    row = lc.classify(chat, "N1", "제목", "요약", retries=1)
    assert row["model_version"] == "fake-model-2026" and row["prompt_version"] == lc.PROMPT_VERSION
    assert len(chat.calls) == 2 and "제목: 제목\n요약: 요약" in chat.calls[0][1]["content"]
    with pytest.raises(lc.ClassificationError):
        lc.classify(FakeChat(["oops", "still bad"]), "N2", "t", "s", retries=1)


def test_prompt_examples_are_synthetic():
    # Guide 6-5: never reuse real (review) articles as examples. The examples use made-up firm names.
    assert "가나회계법인" in lc.SYSTEM_PROMPT and "삼일" not in lc.SYSTEM_PROMPT and "삼정" not in lc.SYSTEM_PROMPT


def test_analysis_results_roundtrip_and_skip(tmp_path):
    doc = {"doc_id": "N1", "source_type": "news", "title": "t", "content": "c", "published_at": "2024-01-01",
           "url": "u", "company_ids": ["pwc"], "content_hash": "h", "collected_at": "now"}
    row = lc.classify(FakeChat([GOOD]), "N1", "t", "c")
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, [doc])
        assert db_manager.insert_analysis_results(conn, [row]) == 1
        assert db_manager.insert_analysis_results(conn, [row]) == 0  # same prompt_version kept
        got = db_manager.fetch_analysis_results(conn, lc.PROMPT_VERSION)
    assert got[0]["issue_keywords"] == ["생성형 AI"]


def test_run_script_estimate_skips_classified(tmp_path, monkeypatch, capsys):
    import run_llm_classification as run

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    docs = [{"doc_id": f"N{i}", "source_type": "news", "title": f"제목{i}", "content": "요약 " * 20,
             "published_at": "2024-01-01", "url": f"u{i}", "company_ids": ["pwc"], "content_hash": f"h{i}",
             "collected_at": "now", "is_relevant": i < 3} for i in range(4)]
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, docs)
        db_manager.insert_analysis_results(conn, [lc.classify(FakeChat([GOOD]), "N0", "t", "c")])
    pending, total, groups = run.pending_articles(lc.PROMPT_VERSION)
    assert total == 3 and len(groups) == 3 and [d["doc_id"] for d in pending] == ["N1", "N2"]
    est = run.estimate(pending, {"chars_per_token": 1.3, "expected_output_tokens": 90,
                                 "price_per_1m_input": 1.0, "price_per_1m_output": 2.0})
    assert est["calls"] == 2 and est["output_tokens"] == 180 and est["cost_usd"] > 0
    monkeypatch.setattr("sys.argv", ["run_llm_classification.py"])
    assert run.main() == 0 and "estimate only" in capsys.readouterr().out  # no API call without --run


# ---------------------------------------------------------------- validation

def _results(n_pos_tech=40, n_neg_reg=3, n_neu_mkt=20):
    rows = ([("positive", "technology_trend")] * n_pos_tech + [("negative", "regulation_policy")] * n_neg_reg
            + [("neutral", "market_competition")] * n_neu_mkt)
    return pd.DataFrame([{"doc_id": f"N{i}", "sentiment": s, "primary_category": c} for i, (s, c) in enumerate(rows)])


def test_stratified_sample_spreads_and_is_deterministic():
    r = _results()
    s = v.stratified_sample(r, n=30, seed=42)
    cells = s.groupby(["sentiment", "primary_category"]).size()
    assert len(s) == 30 and cells[("negative", "regulation_policy")] == 3  # small cell fully used
    assert cells[("positive", "technology_trend")] in (13, 14) and cells[("neutral", "market_competition")] in (13, 14)
    assert s["doc_id"].tolist() == v.stratified_sample(r, n=30, seed=42)["doc_id"].tolist()


def test_kappa_accuracy_and_aliases():
    assert v.norm_sentiment("긍정") == "positive" and v.norm_category("규제") == "regulation_policy"
    assert v.norm_category("모름") is None
    a = pd.Series(["positive", "negative", "neutral", "positive"])
    assert v.cohen_kappa(a, a) == 1.0
    acc, per = v.accuracy_table(pd.Series(["positive", "negative", None]), pd.Series(["positive", "neutral", "x"]))
    assert acc == 0.5 and per.loc["negative", "accuracy"] == 0.0


def test_review_flow_end_to_end(tmp_path, monkeypatch):
    import make_llm_review_sample as mk
    import score_llm_review as sc

    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    for mod in (mk, sc):
        monkeypatch.setattr(mod, "OUT" if mod is mk else "DIR", tmp_path / "llm_review")
    monkeypatch.setattr(sc, "EVALUATION_DIR", tmp_path / "evaluation")
    docs = [{"doc_id": f"N{i}", "source_type": "news", "title": f"제목{i}", "content": "요약", "published_at": "2024-01-01",
             "url": f"u{i}", "company_ids": ["pwc"], "content_hash": f"h{i}", "collected_at": "now"} for i in range(6)]
    replies = [GOOD if i < 4 else GOOD.replace("positive", "negative") for i in range(6)]
    with db_manager.connect() as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, docs)
        db_manager.insert_analysis_results(conn, [lc.classify(FakeChat([replies[i]]), f"N{i}", "t", "c") for i in range(6)])
    monkeypatch.setattr("sys.argv", ["make_llm_review_sample.py", "--n", "6"])
    assert mk.main() == 0
    out = tmp_path / "llm_review"
    blind = pd.read_csv(out / "review_A.csv", encoding="utf-8-sig")
    assert "sentiment" not in blind.columns and "primary_category" not in blind.columns  # blind sheet
    assert {"url", "opened_original"} <= set(blind.columns)

    key = pd.read_csv(out / "llm_key.csv", encoding="utf-8-sig").set_index("sample_id")
    a = blind.copy()
    a["human_sentiment"] = ["긍정" if key.at[s, "sentiment"] == "positive" else "부정" for s in a["sample_id"]]
    a["human_category"] = "기술"
    b = a.copy()
    b.loc[0, "human_sentiment"] = "중립"  # one disagreement
    a.to_csv(out / "review_A.csv", index=False, encoding="utf-8-sig")
    b.to_csv(out / "review_B.csv", index=False, encoding="utf-8-sig")

    assert sc.main() == 0
    report = (tmp_path / "evaluation" / "llm_validation_report.md").read_text(encoding="utf-8")
    assert "sentiment: 표본 정확도 1.0 (n=5) / 모집단 가중 추정 1.0" in report and "합의되지 않은 항목: 1건" in report
    assert not any(f"제목{i}" in report for i in range(6))  # no article titles in the committed report
    assert (out / "adjudication.csv").exists()


def test_openai_chat_omits_temperature_when_none(monkeypatch):
    import sys
    import types

    sent = {}

    class Completions:
        def create(self, **kwargs):
            sent.update(kwargs)
            msg = types.SimpleNamespace(content=GOOD)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], model="gpt-6-luna")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = types.SimpleNamespace(completions=Completions())

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    reply = lc.openai_chat("k", "gpt-6-luna", temperature=None)([{"role": "user", "content": "x"}])
    assert "temperature" not in sent and reply.model == "gpt-6-luna"
    lc.openai_chat("k", "m", temperature=0.2)([{"role": "user", "content": "x"}])
    assert sent["temperature"] == 0.2


def test_run_stops_after_consecutive_api_errors(tmp_path, monkeypatch):
    import run_llm_classification as run

    calls = []

    def failing_chat(messages):
        calls.append(1)
        raise RuntimeError("Error code: 400 - Unsupported value")

    monkeypatch.setattr(run, "openai_chat", lambda *a, **k: failing_chat)
    monkeypatch.setattr(run, "get_openai_api_key", lambda: "k")
    monkeypatch.setattr(run, "FAIL_LOG", tmp_path / "failures.jsonl")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    articles = [{"doc_id": f"N{i}", "title": "t", "content": "c"} for i in range(100)]
    ok, failed = run.run(articles, {"temperature": None, "parse_retries": 1, "stop_after_consecutive_api_errors": 5},
                         workers=1)
    assert ok == 0 and failed == 5 and len(calls) < 100


def test_group_representatives_and_propagate():
    news = [
        {"doc_id": "a2", "published_at": "2024-01-02", "dup_group_id": "g1"},
        {"doc_id": "a1", "published_at": "2024-01-01", "dup_group_id": "g1"},
        {"doc_id": "b1", "published_at": "2024-01-05", "dup_group_id": None},
    ]
    groups = lc.group_representatives(news)
    assert groups == {"a1": ["a1", "a2"], "b1": ["b1"]}  # earliest relevant article represents the group
    row = lc.classify(FakeChat([GOOD]), "a1", "t", "c")
    copies = lc.propagate({"a1": row}, groups)
    assert [c["doc_id"] for c in copies] == ["a2"] and copies[0]["sentiment"] == row["sentiment"]
    assert lc.propagate({"a1": row, "a2": {**row, "doc_id": "a2"}}, groups) == []  # own result kept


def test_unclear_and_weighted_accuracy():
    assert v.is_unclear("판단불가") and v.norm_sentiment("판단불가") is None
    truth = pd.Series(["positive", "negative", "negative"])
    pred = pd.Series(["positive", "positive", "negative"])
    # item 0 stands for 8 articles, items 1-2 for 1 each: weighted (8 + 0 + 1) / 10
    assert v.weighted_accuracy(truth, pred, pd.Series([8.0, 1.0, 1.0])) == 0.9


def test_make_sample_refuses_to_overwrite_labelled_sheets(tmp_path, monkeypatch):
    import make_llm_review_sample as mk

    monkeypatch.setattr(mk, "OUT", tmp_path)
    pd.DataFrame([{"sample_id": "S001", "human_sentiment": "", "human_category": ""}]).to_csv(
        tmp_path / "review_A.csv", index=False, encoding="utf-8-sig")
    assert mk.labelled_sheets() == []
    pd.DataFrame([{"sample_id": "S001", "human_sentiment": "긍정", "human_category": ""}]).to_csv(
        tmp_path / "review_B.csv", index=False, encoding="utf-8-sig")
    assert mk.labelled_sheets() == ["review_B.csv"]
    monkeypatch.setattr("sys.argv", ["make_llm_review_sample.py"])
    assert mk.main() == 2  # stops before touching the DB or files


def test_score_rejects_unrecognised_labels():
    import pandas as pd

    import score_llm_review as sc

    a = pd.DataFrame({"human_sentiment": ["긍정", "판단불가", "", "관련없음"],
                      "human_category": ["트렌드", "인사", None, "관련없음"]},
                     index=["S1", "S2", "S3", "S4"])
    bad = sc.invalid_labels({"A": a})
    assert sorted(bad) == ["review_A.csv S2 human_category='인사'",
                           "review_A.csv S3 human_category is blank",
                           "review_A.csv S3 human_sentiment is blank"]


def test_final_labels_handle_irrelevant_and_disputes():
    import pandas as pd

    import score_llm_review as sc

    idx = ["S1", "S2", "S3", "S4"]
    a = pd.DataFrame({"human_sentiment": ["긍정", "관련없음", "중립", "부정"],
                      "human_category": ["기술", "관련없음", "시장", "규제"]}, index=idx)
    b = pd.DataFrame({"human_sentiment": ["긍정", "관련없음", "관련없음", "부정"],
                      "human_category": ["기술", "관련없음", "관련없음", "시장"]}, index=idx)
    final, disputes, irr = sc.final_labels(a, b, None)
    assert irr == {"S2"}
    assert final.loc["S1"].tolist() == ["positive", "technology_trend"]
    assert pd.isna(final.at["S2", "category"])
    got = {(r.sample_id, r.field) for r in disputes.itertuples()}
    assert got == {("S3", "relevance"), ("S4", "category")}

    adj = pd.DataFrame({"final_sentiment": ["", ""], "final_category": ["관련없음", "규제"]},
                       index=pd.Index(["S3", "S4"], name="sample_id"))
    final, disputes, irr = sc.final_labels(a, b, adj)
    assert irr == {"S2", "S3"} and disputes.empty
    assert final.at["S4", "category"] == "regulation_policy"


def test_read_excel_csv_accepts_excel_resaves(tmp_path):
    import pandas as pd

    from src.common.csv_io import read_excel_csv

    df = pd.DataFrame({"sample_id": ["S1"], "human_category": ["관련없음"], "note": ["a, b"]})
    for enc, sep in (("utf-8-sig", ","), ("cp949", ","), ("utf-16", "\t")):
        f = tmp_path / f"{enc}.csv"
        f.write_bytes(df.to_csv(index=False, sep=sep).encode(enc))
        got = read_excel_csv(f, dtype=str)
        assert got.to_dict("records") == df.to_dict("records"), enc


def test_per_reviewer_accuracy_excludes_special_labels():
    import pandas as pd

    import score_llm_review as sc

    idx = ["S1", "S2", "S3"]
    a = pd.DataFrame({"human_sentiment": ["긍정", "부정", "판단불가"],
                      "human_category": ["기술", "규제", "판단불가"]}, index=idx)
    b = pd.DataFrame({"human_sentiment": ["긍정", "관련없음", "중립"],
                      "human_category": ["시장", "관련없음", "시장"]}, index=idx)
    llm = {"sentiment": pd.Series(["positive", "negative", "neutral"], index=idx),
           "category": pd.Series(["technology_trend", "regulation_policy", "market_competition"], index=idx)}
    rows = [r for r in sc.per_reviewer_section(a, b, llm, None) if r.startswith("| sentiment") or r.startswith("| category")]
    assert rows == ["| sentiment | A | 2 | 1.0 | None |", "| sentiment | B | 2 | 1.0 | None |",
                    "| category | A | 2 | 1.0 | None |", "| category | B | 2 | 0.5 | None |"]
