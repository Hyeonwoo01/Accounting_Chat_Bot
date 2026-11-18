# CLAUDE.md — analysis(분석) 계층

`documents`를 읽어 EDA, LLM 감성·카테고리 분류, 이슈 추출, 결론 초안을 **배치로** 만들고
`analysis_results`·`insights`에 저장한다. 루트 `CLAUDE.md` 규칙이 우선한다. (가이드 5, 6, 8)

## 규칙

- 배치 전용. 대시보드 요청으로 실행되는 코드를 두지 않는다. 외부 데이터 API를 호출하지 않는다.
- 결과마다 `model_version`, `prompt_version`, `created_at`을 저장한다. 처리한 문서는 건너뛴다 (재실행 시 비용 방지).
- LLM 응답은 JSON으로 받아 스키마 검증한다. 실패 시 1회 재시도, 다시 실패하면 미분류로 남기고 건수를 기록한다.

## 처리 범위 (가이드 6-1) — 근거를 기록

- 입력: **제목 + 요약**. 대상: 중복 그룹 대표 기사 중 `is_relevant = 1`.
- 많으면 월별·키워드별 층화 표본으로 줄인다.
- 기록: 전체/제외 후/처리 대상 건수, 입력 기준, 호출 수·배치 크기, 소요 시간, 예상·실제 비용, 선택 이유.

## 감성·카테고리 정의 (가이드 6-3, 6-4)

- 감성 = 기사 논조가 해당 산업·기업의 사업 환경에 우호적인지. `positive` / `neutral` / `negative`.
- 카테고리는 4개 코드 중 하나만: `regulation_policy`, `technology_trend`, `market_competition`, `supply_chain_infrastructure`.
- 공시에는 카테고리만 부여하고 감성은 부여하지 않는다.
- 기업별 감성 비교는 **단일 기업 기사로 한정**한다. 감성은 보조 지표다.
- 프롬프트에 정의·예시를 넣고, 제목·요약에 없는 사실을 추가하지 말라고 명시한다.
- 프롬프트를 바꿀 때마다 `prompt_version`을 올리고 변경 이유를 기록한다.

## 표본 검수 (가이드 6-5) — 필수

- 권장 100건, 최소 50건. 감성 × 카테고리가 고르게 들어가도록 층화 추출 (시드 고정).
- 두 사람이 **LLM 결과를 보지 않고** 각자 라벨링 → 불일치 합의. 결과는 `validation_samples`에 저장.
- 지표: 감성 정확도, 카테고리 정확도, 분류별 정확도, 검수자 간 일치율, 이슈 요약 사실 일치 비율.
- 검수 표본을 프롬프트 예시로 재사용하지 않는다 (정확도가 부풀려진다).
- 라벨 칸은 비우지 않는다. 특수값: `판단불가`(제목·요약으로 판단 불가), `관련없음`(산업·법인과 무관 = 뉴스 정제 오류).
  둘 다 LLM 정확도에서 빼고 건수를 따로 기록한다. 같은 사건의 다른 기사도 각각 라벨링하고 note에 표시한다.
- 한쪽만 `관련없음`이면 `adjudication.csv`의 relevance 행으로 합의한다. 관련 있다고 정하면 final_sentiment·final_category를 모두 채운다.
- 결과는 `evaluation/evaluation_report.md`에 기록한다.

## EDA·급증 탐지 (가이드 5-2, 5-3)

- 주 단위 기사량이 직전 8주 평균 + 2σ를 넘으면 후보 (`eda.detect_spikes`). 후보 주의 제목을 직접 읽어 배경을 기록한다.
- 네이버 1,000건 잘림 때문에 생긴 "가짜 급증"을 구분한다 (`data/raw/news/query_log.jsonl`의 `truncated`).
- 공시가 적으면 추이선 대신 타임라인 점으로 그리고 한계를 적는다.
- 감사보고서 제출 건수(`data/processed/audit_filings_monthly.csv`)는 계절성이 커서 월별 급증 탐지를 적용하지 않는다.
  연도별·사업연도별 비중(Big4 각 법인 / 전체)으로 비교한다.
- 자체 공시(회계법인사업보고서)의 수치(사업부문별 매출, 인력, 감사실적)는 연도별 비교에 쓰고, 표에서 값을 옮길 때는 원문 위치를 기록한다.

## 결론 문장 (가이드 8)

- LLM 초안은 배치로 만들고 두 사람이 근거 문서와 대조해 고친 뒤 `insights`에 저장한다.
- 결론마다 `evidence_doc_ids`를 연결한다. 근거 없는 문장은 삭제한다. 사실과 해석을 구분한다.

### Day 7 구현 (`conclusions.py`, `scripts/draft_insights.py`, `scripts/load_insights.py`)

1. `draft_insights.py`: DB의 집계를 번호 붙은 **사실 목록**(F01…)으로 만든다 — 연도별(매년 1~10월) 카테고리 비중,
   법인·연도별 감성 비중(단일 법인 기사, 20건 미만 제외), 연도별 감사보고서 제출 건수·비중, 배경이 기록된 급증 주.
   기본은 사실 목록(`data/processed/insights/facts.json`)과 비용 추정만, `--run`일 때 LLM 1회 호출.
   - LLM에는 **집계 수치와 사람이 쓴 급증 배경만** 보낸다. 기사 제목·요약은 보내지 않는다.
   - 근거 doc_id는 LLM이 인용한 fact_id로 **로컬에서** 붙인다 (모델이 출처를 지어낼 수 없다).
   - 결론 문장의 숫자가 인용한 사실에 없으면 `number_check`에 "확인 필요"로 표시한다.
   - 검수 시트 `insight_drafts.csv`가 이미 있으면 덮어쓰지 않는다 (`--force`면 `.bak`으로 남기고 다시 만든다).
2. 두 사람이 근거 링크(`evidence_preview`)를 열어 확인하고 `reviewer_A`·`reviewer_B`에 O/X.
   문장을 고치면 `final_text`, 근거를 바꾸면 `final_evidence_doc_ids`. 직접 쓴 결론은 행을 추가하고 `source=manual`.
3. `load_insights.py --reviewers "이름A,이름B"`: 둘 다 O이고 근거 doc_id가 모두 DB에 있는 행만 `insights`를
   **통째로 바꿔** 넣는다(재실행 안전). `--check`는 검증만. 해석은 문장 앞에 "(해석)"을 붙여 저장한다.
- `insights`에는 model/prompt 버전 컬럼이 없다. 초안의 `model_version`·`prompt_version`·`created_at`은 검수 시트에 남고,
  최종 문장은 사람이 검수한 결과다. 검수 시트·facts.json은 뉴스 제목이 들어 있어 로컬에만 둔다(종료 체크리스트 삭제 대상).

## 기간별 이슈 요약 (심화, `issue_summary.py`, `scripts/draft_issue_summaries.py`, `scripts/load_issue_summaries.py`)

- 분기마다 기사가 많은 사건(유사 기사 그룹) 상위 `top_events`개를 **저장된 분류 결과**(카테고리·감성·키워드·한 줄 요약)로
  묘사하고, 그 분기의 사람이 쓴 급증 배경을 더해 LLM이 headline 1문장과 이슈 1~3개를 쓴다(prompt `issue-v1`).
  LLM에는 기사 제목·URL을 보내지 않는다. 이슈는 사건 번호(E1…)를 인용하고 근거 doc_id는 로컬에서 붙인다.
- 흐름은 결론 문장과 같다: 기본은 입력·비용 추정만, `--run`으로 초안 → `summary_drafts.csv`에서 두 사람이
  headline·issue_n·evidence_n을 직접 고치고 O/X → `load_issue_summaries.py`가 둘 다 O인 분기만 `issue_summaries`에 통째로 넣는다.
- 입력은 네이버 뉴스 파생물(LLM 요약)이라 팀 결정(기간 중에만 사용)의 범위 안에서만 쓰고, 초안·저장 요약 모두 종료 시 삭제한다.

## Day 3 EDA 산출물 (`scripts/run_eda.py`)

- 뉴스 제목·파생 데이터가 들어가므로 **`data/processed/eda/`(git 미추적)에만** 쓴다. 저장소·발표 자료에 올리지 않는다
  (네이버 검색 API 약관 — 팀 결정은 `evaluation/gate_decision.md`).
- 관련 기사 = 정제본(`is_relevant`, `src/preprocessing/CLAUDE.md`). 정제·법인 판정 정확도는 `news_review_guide.md` 검수로 잰다.
- **법인별 집계·감사보고서 비교는 한국 회원 법인 기사(`company_scope == member_firm`, `kr_company_ids`)만** 센다.
  글로벌 네트워크·업계 전체 기사는 범위별 표로 따로 보여준다 (원칙 6).
- DART 원문의 법인 식별률은 제출 법인(`flr_nm`)을 정답으로 잰다.
- 급증 후보는 정제본 기준. 후보 주의 대표 제목(유사 기사 그룹당 1건)을 사람이 읽고 배경을 리포트에 적는다.

## Day 4 LLM 분류 (`llm_classifier.py`, `validation.py`)

- 대상: 정제본(`is_relevant`) 기사의 제목+요약 중 **유사 기사 그룹의 대표(가장 먼저 게시된 관련 기사)만** LLM에 보낸다
  (가이드 6-1). 그룹의 나머지 기사는 대표의 결과를 복사한다 — 한 사건에 한 라벨, 기사량 집계는 그대로.
  검수 표본도 대표에서만 뽑는다 (100건 = 서로 다른 사건 100개).
- 프롬프트 예시는 **가상 문장**만 쓴다 (실제·검수 기사 재사용 금지).
- `run_llm_classification.py`는 기본이 **비용 추정만** 한다. `--run`일 때만 API를 호출하고, 먼저 `--limit 20`으로 시험한다.
  이미 같은 `prompt_version`으로 분류된 기사는 건너뛴다. 실패는 `data/processed/llm/failures.jsonl`(doc_id·사유)에 남는다.
- 프롬프트를 바꾸면 `PROMPT_VERSION`을 올리고 이유를 `evaluation/evaluation_report.md`에 적는다.
- `gpt-6-luna`는 temperature 0을 지원하지 않아 기본값(1)으로 호출한다(`temperature: null`). 같은 기사도 실행마다
  결과가 달라질 수 있으므로 **재현성 한계**로 리포트에 적고, 검수는 저장된 결과(한 번 실행한 값)를 기준으로 한다.
- 검수: `make_llm_review_sample.py` → 두 사람이 `review_A/B.csv`를 **서로·LLM 결과를 보지 않고** 라벨링
  → `summary_check.csv`(요약 사실 여부) → `score_llm_review.py`. 불일치는 `adjudication.csv`에서 합의.
- `evaluation/llm_validation_report.md`는 수치만 담는다(커밋 가능). 오분류 사례(제목)는 `data/processed/llm_review/`에만.
- 뉴스를 LLM에 넣는 것은 네이버 검색 API 약관(특약 2.3) 금지 사항이다. 팀 결정으로 기간 중에만 하며,
  분류 결과도 종료 체크리스트의 삭제 대상이다 (루트 `CLAUDE.md`).

