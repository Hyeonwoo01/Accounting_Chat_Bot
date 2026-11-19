# CLAUDE.md — 프로젝트 공통 규칙

이 파일은 저장소 전체에 적용되는 최상위 규칙이다. 각 계층 디렉터리의 `CLAUDE.md`는
이 규칙을 **추가로 구체화**할 뿐, 완화할 수 없다. 충돌 시 이 파일이 우선한다.
설계 상세는 팀 개발 가이드(v2)를 따르며, 절 번호는 "가이드 X-Y"로 표기한다.

## 프로젝트 개요

PwC·Deloitte·EY·KPMG(및 한국 회원 법인)의 뉴스와 DART 공시를 수집하고,
LLM 감성·카테고리 분류, 이슈 추출, 공시 임베딩을 **배치로 사전 생성**한 뒤,
Streamlit 대시보드에서 저장된 결과를 조회하고 근거 기반 공시 Q&A를 제공한다.

> 핵심 질문: "이 산업에서 지금 무슨 일이 일어나고 있으며, 그 판단의 근거는 무엇인가."

**Day 1 게이트 결과: 분기 B-1** (`evaluation/gate_decision.md`). DART 공시는 두 종류다.

| 공시 | 식별 | 성격 |
|---|---|---|
| Big4 법인 **자체 공시** (회계법인사업보고서) | `corp_code ∈ get_big4_corp_codes()` | Big4 자체의 사실 (매출·인력·감리 등) |
| Big4가 **제출한 고객사 감사보고서** | 그 외 (`company_ids` = 제출 법인) | **고객사**의 재무 정보. 감사 수임 추이(건수)의 근거 |

## 계층 구조

```
src/collectors ──▶ data/raw ──▶ src/preprocessing ──▶ SQLite(documents)
                                                          │
                     ┌────────────────────────────────────┼──────────────────────┐
                     ▼                                    ▼                      ▼
               src/analysis (배치)                  src/rag (인덱스: 배치)     EDA
               analysis_results · insights          chunks · data/vectordb
                     │                                    │
                     └──────────────┬─────────────────────┘
                                    ▼
                    dashboard/ (조회 전용 + Q&A는 rag_pipeline만 호출)
```

| 계층 | 디렉터리 | 책임 | 세부 규칙 |
|---|---|---|---|
| 공통 | `src/common/` | 설정·키 로딩, 경로, DART 상수 | `src/common/CLAUDE.md` |
| 수집 | `src/collectors/` | DART·네이버 뉴스 API → `data/raw/` | `src/collectors/CLAUDE.md` |
| 전처리 | `src/preprocessing/` | 정규식 정제, 중복 처리, 기업 식별, 공시 텍스트 추출 | `src/preprocessing/CLAUDE.md` |
| DB | `src/database/` | SQLite 스키마(팀 계약), 적재·조회 | `src/database/CLAUDE.md` |
| 분석 | `src/analysis/` | EDA, LLM 분류·이슈 추출, 결론 초안 (배치) | `src/analysis/CLAUDE.md` |
| RAG | `src/rag/` | 공시 청킹·임베딩(배치), 근거 기반 Q&A | `src/rag/CLAUDE.md` |
| 대시보드 | `dashboard/` | 저장 결과 조회·시각화, Q&A 화면 | `dashboard/CLAUDE.md` |
| 실행 | `scripts/` | 단계별 실행 진입점 (계층을 조립만 함) | — |
| 평가 | `evaluation/` | 게이트 결과, 검수 표본, 평가 리포트 | — |

### 의존 방향 (`tests/test_layer_boundaries.py`가 검사)

- 계층 간 데이터 교환은 **저장소(`data/raw`, SQLite, 벡터DB)** 를 통해서만 한다.
- `collectors`는 저장만 하고 DB·전처리를 모른다. `preprocessing`은 외부 API를 호출하지 않는다.
- `analysis`·`rag`는 `database`를 통해 `documents`를 읽는다. 서로 import하지 않는다.
- `dashboard`는 `database`(조회)와 `src.rag.rag_pipeline`(Q&A)만 사용한다. LLM SDK·HTTP 클라이언트를 import하지 않는다.
- 모든 계층은 `src/common/`을 사용할 수 있다. `common`은 다른 계층을 import하지 않는다.
- `scripts/`는 여러 계층을 조립하는 유일한 곳이다.

## 핵심 원칙 (가이드 1-4)

1. **계층 분리.** 대시보드에서 DART·뉴스 API를 호출하거나 LLM 분류를 실시간으로 수행하지 않는다.
   대시보드에서 LLM이 호출되는 곳은 **공시 Q&A 질문 처리 한 곳**뿐이다.
2. **사전 생성.** LLM 분석 결과·임베딩·대시보드 결론은 배치로 만들어 저장한다.
3. **LLM 결과 검증.** 감성·카테고리·이슈 추출은 표본 검수(50~100건)로 정확도를 확인하고 기록한다.
   처리 범위(제목+요약, 표본 추출 등)와 근거도 기록한다. 측정하지 않은 수치는 쓰지 않는다.
4. **근거가 없으면 거절.** RAG 답변은 근거 공시(공시명·제출일·접수번호·링크)를 표시하고, 근거가 없으면 거절한다.
5. **뉴스와 공시의 역할 분리.** 뉴스는 변화 탐색, 공시는 사실 확인. 보도자료를 DART 공시로 표시하지 않는다.
6. **법인과 브랜드 구분.** 글로벌 브랜드 발표를 한국 회원 법인의 실적으로 연결하지 않는다.
7. **재실행 가능.** 모든 수집·처리 스크립트는 다시 실행해도 중복 적재가 생기지 않는다.
8. **고객사 감사보고서는 고객사 정보다.** 감사보고서 내용을 Big4의 실적·행동으로 표현하지 않는다.
   감사보고서 제출 건수는 **건수 기준**이며 매출 점유율이 아니고, 3~5월 집중은 결산 일정에 따른 계절성이다.

## 보안 (위반 시 PR 반려)

- `DART_API_KEY`, `OPENAI_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`은 **`.env`로만 관리**한다.
- `.env`는 `.gitignore`에 등록되어 있다. 새 키는 `.env.example`에 **이름만** 추가한다.
- 키는 `src/common/config.py`를 통해서만 읽는다. 로그·예외 메시지는 `mask_secret()`을 거친다.
- 원본 저장 시 요청 파라미터에서 `crtfc_key`를 제거한다.
- 코드·주석·로그·노트북 출력·커밋·PR·발표자료·화면 캡처에 키를 노출하지 않는다.
- 키가 노출되면 즉시 발급처에서 **폐기·재발급**한다. 커밋 삭제만으로는 해결되지 않는다.

## 데이터 관리

- `data/` 아래 실제 데이터(원본, DB, 벡터DB)는 커밋하지 않는다. 구조만 `.gitkeep`으로 유지한다.
- LLM 결과에는 `model_version`, `prompt_version`, `created_at`을 함께 저장한다.

## Git 협업 규칙 (상세: `CONTRIBUTING.md`)

- `main`(제출용) ← `develop`(통합) ← `feature/<계층>-<요약>`. `main`·`develop` 직접 push 금지.
- PR은 최소 1명 리뷰 후 merge. PR 전 `pytest` 통과.
- 스키마(`src/database/db_manager.py`) 변경은 별도 PR로, 영향 범위를 적는다.

## Claude 작업 지침

- 작업 중인 디렉터리의 `CLAUDE.md`를 먼저 읽고 따른다.
- 요청 범위 밖의 계층을 수정하지 않는다. 계층 경계를 넘는 변경이 필요하면 먼저 알린다.
- 실행·테스트하지 않은 코드를 "테스트했다"고 말하지 않는다. 실데이터로 확인하지 않은 동작은 그렇게 밝힌다.

## ⚠️ 프로젝트 종료·포트폴리오 공개 전 필수 체크리스트

팀 결정(2026-11-13): 네이버 검색 API 뉴스는 **프로젝트 기간에만** 사용하고, 종료 시 아래를 모두 처리한다.
근거와 경과는 `evaluation/gate_decision.md` "네이버 검색 API 데이터" 절.
**Claude는 사용자가 프로젝트 종료·제출·포트폴리오·저장소 공개(public)를 언급하면 이 목록을 먼저 안내한다.**

기간 중 조건: 뉴스 원문·파생 데이터는 **로컬에만** 두고 공유·업로드·발표 화면에 원문을 띄우지 않는다
(제목+링크 수준만). 추가 수집은 필요한 만큼만 한다.

- [ ] 각 PC에서 `data/raw/news/` 원본과 DB의 뉴스 행(`source_type='news'`) 삭제, 뉴스 기반 산출물(분류 결과·임베딩·캐시) 삭제
- [x] 뉴스 수집 비활성화: 설정 플래그 + 수집·적재 스크립트 차단 (이 저장소에 적용됨: `news.enabled: false`)
- [x] 저장소의 뉴스 파생 기록 정리: 이 공개 저장소는 이력 없이 새로 만들었고 `news_gate_report.md`·`spike_summary.md` 등은 제외 (README "데이터 출처와 제약").
      작업 이력이 있는 원래 저장소는 비공개로 유지한다 (README "협업 기록과 비공개 저장소")
- [ ] 리포트·대시보드·발표 자료에 네이버 뉴스 원문이나 그 분석 결과가 남아 있지 않은지 확인, README에 데이터 출처·제약 명시
- [ ] 저장소를 public으로 바꾸기 전에 `.env`·키·개인 이메일이 이력에 없는지 다시 확인

