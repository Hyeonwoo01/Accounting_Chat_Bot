# CLAUDE.md — preprocessing(전처리) 계층

`data/raw/`를 읽어 정제하고 `documents` 행을 만든다. 루트 `CLAUDE.md` 규칙이 우선한다. (가이드 5-1)

## 규칙

- 외부 API·LLM을 호출하지 않는다. 입력은 `data/raw/`와 `config/`뿐이다.
- 정규표현식 패턴마다 **무엇을 제거하는지 주석**을 단다 (`text_cleaning.py`).
- 정제 규칙을 바꾸면 테스트(`tests/test_preprocessing.py`)에 실제 사례를 추가한다.

## 중복 처리

- 완전 중복(정규화 제목+본문 해시 동일)은 한 번만 저장한다 (DB 유니크 인덱스).
- 유사 제목은 **삭제하지 않고** `dup_group_id`로 묶는다. 같은 사건의 보도량 자체가 분석 대상이다.
- 뉴스 `doc_id`는 URL 해시로 만든다 (재실행해도 같은 기사 = 같은 ID).

## 기업 식별

- 별칭은 `config/company_mapping.yaml`에서 관리한다. 짧은 별칭(`삼일`, `한영`, `EY` 등)은 `weak_aliases`로 두고 문맥어가 있을 때만 인정한다.
- 영문 별칭은 단어 경계를 건다 (`EY` ≠ `KEY`).
- 식별 실패율과 오탐 사례는 EDA에서 확인해 리포트에 기록한다.
- 이름이 나왔다고 그 회사의 행동으로 분류하지 않는다. 여러 기업이 나오면 모두 기록한다.

## 공시 텍스트

- DART 원문은 XML이 깨진 경우가 많아 정규식으로 태그를 제거한다.
- `<TITLE>`은 `## 제목` 줄로 보존한다 (RAG 청킹 기준). 인코딩은 UTF-8 → CP949 순으로 시도한다.

## Big4 제출 감사보고서 (분기 B-1, `evaluation/gate_decision.md`)

- `build_audit_report_docs`: 시장 전체 목록 중 제출인(`flr_nm`)이 Big4 법인인 공시만 `documents`에 넣는다.
  `company_ids` = 제출한 회계법인, `corp_code` = 감사받은 고객사.
- 이 문서들은 **고객사의 재무 정보**다. Big4 자체의 사실로 쓰지 않는다.
  Big4 자체 공시인지는 `corp_code ∈ get_big4_corp_codes()`로 구분한다.
- 원문이 없으면 `content`는 빈 문자열(목록 메타데이터만). RAG는 `content`가 있는 행만 쓴다.
- 비(非)Big4 포함 월별 제출 건수는 `data/processed/audit_filings_monthly.csv`로 저장한다 (점유 비중 EDA용, 건수 기준).
- 원문은 `select_audit_originals`로 고른 표본만 받는다 (설정: `dart_market.originals`, 시드 고정, 고객사당 1건,
  `[기재정정]` 제외). 표본 원문은 모두 같은 제출월이라 시계열 분석에는 쓰지 않는다 (시계열은 목록 건수로).
- DART 원문은 법인명을 `삼 일 회 계 법 인`처럼 띄어 쓰기도 한다. 4글자 이상 별칭은 글자 사이 공백을 허용한다.

## 뉴스 관련성 = 법인 범위 정제본 (`firm_scope.py`, `scripts/tag_news_firms.py`)

- 관련성은 별도 규칙을 두지 않고 **검수로 다듬은 정제본**(`exclude_reason`이 없는 기사)을 그대로 쓴다
  (기준·이력: `evaluation/news_review_guide.md`).
- `process_data.py`가 `data/processed/news_firm_scope.jsonl`을 읽어 `is_relevant`를 매 실행마다 다시 채운다.
  파일에 없는 기사는 무관으로 표시하고 경고한다 → `tag_news_firms.py`를 다시 실행한다.
