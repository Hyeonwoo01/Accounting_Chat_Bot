from __future__ import annotations

import json

import pandas as pd

from src.analysis import eda
from src.database import db_manager
from src.preprocessing.company_matcher import CompanyMatcher, filer_identification

MAPPING = {
    "context_words": ["회계법인", "감사"],
    "companies": [
        {"company_id": "pwc", "aliases": ["삼일PwC", "삼일회계법인"], "weak_aliases": ["삼일"],
         "kr_entities": [{"name": "삼일회계법인"}], "member_firms": [{"member_firm_id": "pwc_korea"}]},
        {"company_id": "kpmg", "aliases": ["삼정KPMG", "삼정회계법인"], "weak_aliases": [],
         "kr_entities": [{"name": "삼정회계법인"}], "member_firms": [{"member_firm_id": "kpmg_korea"}]},
    ],
}
M2C = {"pwc_korea": "pwc", "kpmg_korea": "kpmg"}


def _scope_rows(df):
    """Every relevant article tagged as Korean member firm, except s1 which is global news."""
    rows = []
    for r in df.itertuples():
        scope = "global_network" if r.doc_id == "s1" else "member_firm"
        rows.append({"doc_id": r.doc_id, "company_scope": scope, "exclude_reason": None if r.is_relevant else "no_brand",
                     "member_firm_ids": [f"{c}_korea" for c in r.company_ids] if scope == "member_firm" else []})
    return rows


# ---------------------------------------------------------------- DB

def test_update_relevance(tmp_path):
    doc = {"doc_id": "N1", "source_type": "news", "title": "t", "content": "c", "published_at": "2024-01-01",
           "url": "u", "company_ids": ["pwc"], "content_hash": "h", "collected_at": "now"}
    with db_manager.connect(tmp_path / "t.db") as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, [doc])
        db_manager.update_relevance(conn, {"N1": False})
        assert db_manager.fetch_documents(conn, "news")[0]["is_relevant"] is False


# ---------------------------------------------------------------- EDA

def _news():
    rows = []
    for i in range(12):  # weekly baseline: 2 relevant articles per week
        for k in range(2):
            rows.append({"doc_id": f"b{i}{k}", "title": f"평시 {i}-{k}", "published_at": (pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=i)).date().isoformat(),
                         "company_ids": ["pwc"], "search_keyword": "회계법인 규제", "dup_group_id": f"b{i}{k}",
                         "is_relevant": True})
    for k in range(10):  # spike week 12: one big duplicate group + others
        rows.append({"doc_id": f"s{k}", "title": "삼일 감리 제재" if k < 7 else f"기타 {k}",
                     "published_at": (pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=12)).date().isoformat(),
                     "company_ids": ["pwc", "kpmg"] if k == 0 else ["pwc"], "search_keyword": "회계법인 규제",
                     "dup_group_id": "s0" if k < 7 else f"s{k}", "is_relevant": True})
    rows.append({"doc_id": "x", "title": "무관", "published_at": "2024-02-01", "company_ids": [],
                 "search_keyword": "회계법인 AI", "dup_group_id": "x", "is_relevant": False})
    return eda.news_frame(rows)


def test_attach_firm_scope_counts_only_korean_member_firm():
    df = _news()
    tagged = eda.attach_firm_scope(df, _scope_rows(df), M2C)
    assert tagged.set_index("doc_id").loc["s1", "kr_company_ids"] == []  # global news not attributed
    assert tagged.set_index("doc_id").loc["s0", "kr_company_ids"] == ["kpmg", "pwc"]
    assert eda.scope_summary(tagged).loc["global_network", "articles"] == 1
    # DB holds the copy marked exact_duplicate -> attributed via duplicate_of
    rows = _scope_rows(df) + [{"doc_id": "s0", "exclude_reason": "exact_duplicate", "duplicate_of": "b00"}]
    dup = eda.attach_firm_scope(df, rows, M2C).set_index("doc_id")
    assert dup.loc["s0", "kr_company_ids"] == ["pwc"] and dup.loc["s0", "company_scope"] == "member_firm"


def test_news_tables():
    df = eda.attach_firm_scope(_news(), _scope_rows(_news()), M2C)
    kw = eda.keyword_summary(df)
    assert kw.loc["회계법인 AI", "irrelevant_share"] == 1.0
    cm = eda.company_monthly(df)
    assert set(cm["company_id"]) == {"pwc", "kpmg"}
    assert eda.dup_summary(df)["articles_in_multi_article_groups"] == 7


def test_spike_candidates_titles_ordered_by_group_size():
    sp = eda.spike_candidates(_news(), baseline_weeks=8, sigma=2.0)
    assert len(sp) == 1
    assert sp.iloc[0]["count"] == 10 and sp.iloc[0]["titles"][0] == "삼일 감리 제재"


def test_audit_share_and_news_vs_audit():
    monthly = pd.DataFrame([
        {"month": "2024-03", "pblntf_ty": "F", "filer": "pwc", "count": 30},
        {"month": "2024-03", "pblntf_ty": "F", "filer": "kpmg", "count": 10},
        {"month": "2024-03", "pblntf_ty": "F", "filer": "other", "count": 60},
        {"month": "2024-12", "pblntf_ty": "F", "filer": "pwc", "count": 999},  # outside Jan-Oct
    ])
    share = eda.audit_share_by_year(monthly)
    assert share.loc["2024", "big4_share"] == 0.4
    assert share.loc["2024", "pwc_share_within_big4"] == 0.75
    news = eda.attach_firm_scope(_news(), _scope_rows(_news()), M2C)
    vs = eda.news_vs_audit_yearly(news, monthly)
    row = vs[(vs["year"] == "2024") & (vs["company_id"] == "pwc")].iloc[0]
    assert row["audit_filings"] == 30 and row["articles"] == 33  # 34 relevant pwc minus the global one


def test_filer_identification():
    docs = [
        {"content": "감사인 삼 일 회 계 법 인 대표이사", "corp_code": "C1", "company_ids": ["pwc"], "title": "감사보고서"},
        {"content": "서명 없음", "corp_code": "C2", "company_ids": ["kpmg"], "title": "감사보고서", "rcept_no": "2"},
        {"content": "자체 보고서", "corp_code": "OWN", "company_ids": ["pwc"], "title": "회계법인사업보고서"},
        {"content": "", "corp_code": "C3", "company_ids": ["pwc"], "title": "목록만"},
    ]
    r = filer_identification(docs, CompanyMatcher(MAPPING), {"OWN"})
    assert (r["checked"], r["found"], r["rate"]) == (2, 1, 0.5) and r["misses"][0]["rcept_no"] == "2"


def test_run_eda_end_to_end(tmp_path, monkeypatch):
    import run_eda

    db = tmp_path / "industry.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setattr(run_eda, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(run_eda, "OUT", tmp_path / "eda")
    df = _news()
    docs = [{**r, "content": "c", "url": r["doc_id"], "source_type": "news", "content_hash": r["doc_id"],
             "collected_at": "now", "published_at": r["published_at"].date().isoformat()}
            for r in df.drop(columns=["month", "n_companies"]).to_dict("records")]
    with db_manager.connect(db) as conn:
        db_manager.init_db(conn)
        db_manager.insert_news(conn, docs)
        db_manager.update_relevance(conn, {d["doc_id"]: d["is_relevant"] for d in docs})
    pd.DataFrame([{"month": "2024-03", "pblntf_ty": "F", "filer": "pwc", "count": 3}]).to_csv(
        tmp_path / "audit_filings_monthly.csv", index=False)
    with (tmp_path / "news_firm_scope.jsonl").open("w", encoding="utf-8") as f:
        for r in _scope_rows(df):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    monkeypatch.setattr(run_eda, "load_company_mapping", lambda: MAPPING)

    assert run_eda.main() == 0
    report = (tmp_path / "eda" / "eda_report.md").read_text(encoding="utf-8")
    assert "이슈 급증 후보" in report and "삼일 감리 제재" in report
    assert "global_network" in report and "news_firm_scope.jsonl 없음" not in report


def test_refined_relevance_from_firm_scope(tmp_path, monkeypatch):
    import process_data

    path = tmp_path / "news_firm_scope.jsonl"
    monkeypatch.setattr(process_data, "FIRM_SCOPE_FILE", path)
    assert process_data.refined_relevance([{"doc_id": "a"}]) is None  # file missing -> leave DB as is
    rows = [{"doc_id": "a", "exclude_reason": None},
            {"doc_id": "b", "exclude_reason": "mention_only"},
            {"doc_id": "d", "exclude_reason": None},
            {"doc_id": "d2", "exclude_reason": "exact_duplicate", "duplicate_of": "d"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    stored = [{"doc_id": "a"}, {"doc_id": "b"}, {"doc_id": "c"}, {"doc_id": "d2"}]  # DB kept the copy d2
    got = process_data.refined_relevance(stored)
    assert got == {"a": True, "b": False, "c": False, "d2": True}


def test_spike_notes_are_never_overwritten(tmp_path):
    import run_eda

    sp = pd.DataFrame([{"week": "2024-03-25", "count": 10, "threshold": 4.0, "titles": ["삼일 감리 제재"]}])
    path = tmp_path / "spike_notes.md"
    assert run_eda.update_spike_notes(path, [("전체", sp)]) == 1
    path.write_text(path.read_text(encoding="utf-8").replace("- 배경: ", "- 배경: 감리 결과 발표"), encoding="utf-8")

    sp2 = pd.concat([sp, pd.DataFrame([{"week": "2024-06-03", "count": 9, "threshold": 5.0, "titles": ["새 이슈"]}])])
    assert run_eda.update_spike_notes(path, [("전체", sp2)]) == 1  # only the new week
    text = path.read_text(encoding="utf-8")
    assert "- 배경: 감리 결과 발표" in text and text.count("### 전체 2024-03-25") == 1 and "### 전체 2024-06-03" in text


def test_summarize_spike_notes_drops_titles():
    import summarize_spike_notes as s

    notes = """# 이슈 급증 배경 기록

### 전체 2024-01-08
- 12건 (기준 8.5)
  - 가나회계법인, 'AI 감사 세미나' 개최
- 유형: 법인 행사·보도자료
- 주 법인: 가나회계법인
- 관련 카테고리: 규제 및 정책
- 배경: 한 법인의 행사 홍보가 겹쳤다
- 수집 영향: 없음

### kpmg 2024-03-11
- 9건 (기준 4.0)
  - 다른 제목
- 유형: 
- 주 법인: 
"""
    entries = s.parse_notes(notes)
    assert [(e["scope"], e["week"], e["count"]) for e in entries] == [("전체", "2024-01-08", 12), ("kpmg", "2024-03-11", 9)]
    assert entries[0]["type"] == "법인 행사·보도자료" and entries[1]["type"] == ""
    md = s.render(entries)
    assert "AI 감사 세미나" not in md and "다른 제목" not in md  # no article titles
    assert "| 법인 행사·보도자료 | 1 |" in md and "유형 미기록 1개: kpmg 2024-03-11" in md


def test_spike_types_parse_detail_and_flag_unknown():
    import summarize_spike_notes as s

    notes = """### 전체 2024-12-02
- 30건 (기준 20.0)
- 유형: 인사 (정기 인사 및 포럼)
### 전체 2025-01-06
- 25건 (기준 18.0)
- 유형: 법인 인사·조직 관련 보도 및 경영 전망
"""
    entries = s.parse_notes(notes)
    assert entries[0]["type"] == "인사" and entries[0]["type_detail"] == "정기 인사 및 포럼"
    assert s.invalid_types(entries) == ["전체 2025-01-06: 법인 인사·조직 관련 보도 및 경영 전망"]
