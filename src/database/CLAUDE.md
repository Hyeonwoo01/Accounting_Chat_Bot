# CLAUDE.md — database 계층

SQLite 스키마와 적재·조회 함수. 루트 `CLAUDE.md` 규칙이 우선한다. (가이드 3)

## 규칙

- **스키마는 두 담당자 사이의 계약이다.** 컬럼 추가·변경은 별도 PR로 하고, 영향받는 계층을 PR 설명에 적는다.
- 다른 `src` 계층을 import하지 않는다 (`common`만 허용).
- 적재 함수는 재실행에 안전해야 한다: 뉴스는 `INSERT OR IGNORE`(doc_id·url·content_hash), 공시는 `rcept_no` 기준 upsert.
- SQL에 값을 문자열로 이어 붙이지 않는다. 항상 `?` 파라미터를 쓴다.
- JSON 컬럼(`company_ids`, `issue_keywords`, `evidence_doc_ids`)은 저장 시 직렬화, 조회 시 역직렬화를 이 계층에서 처리한다.
- 벡터DB는 검색 인덱스일 뿐이다. 원문은 `documents`와 `data/raw/`에 보존한다.

## 대시보드 조회용 테이블 (Day 6)

대시보드는 DB만 읽으므로, 파일로 만들어지던 배치 산출물을 아래 테이블로 옮긴다. 채우는 곳은
`scripts/load_dashboard_tables.py` 하나이며, 테이블마다 **전체를 지우고 다시 넣는다**(재실행 안전).

| 테이블 | 원천 | 용도 |
|---|---|---|
| `news_firm_scope` | DB 뉴스 + `news_firm_scope.jsonl` (`eda.attach_firm_scope`) | 법인별 집계는 `kr_company_ids`(한국 회원 법인 기사)만 센다 (원칙 6) |
| `news_spikes` | DB 뉴스로 급증 후보 재계산(`run_eda.py`와 같은 규칙) + `spike_annotations.csv` | 급증 주 표시·배경. `scope`는 `all` 또는 company_id |
| `audit_filings_monthly` | `audit_filings_monthly.csv` (`process_data.py`) | 감사보고서 제출 건수(`filer` = Big4 company_id 또는 `other`) — 건수 기준, 매출 점유율 아님 |
| `dart_corp_names` | `data/raw/dart/market/` 목록의 corp_name | 감사보고서 행에 **감사받은 고객사명** 표시 (원칙 8) |
| `issue_summaries` | `scripts/load_issue_summaries.py` (두 사람이 승인한 LLM 기간별 요약만) | Issue Analysis의 기간별 핵심 이슈 패널. 뉴스 파생이라 종료 시 삭제 대상 |

실행 순서: `tag_news_firms.py` → `process_data.py` → `run_eda.py` → (`spike_notes.md` 작성) → `summarize_spike_notes.py`
→ `load_dashboard_tables.py`. 뉴스에서 나온 행은 종료 체크리스트의 삭제 대상이다 (루트 `CLAUDE.md`).
