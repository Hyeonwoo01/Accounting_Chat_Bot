# CLAUDE.md — collectors(수집) 계층

DART OpenAPI와 네이버 검색 API(뉴스)를 호출해 **응답 원본을 `data/raw/`에 그대로 저장**한다.
루트 `CLAUDE.md` 규칙이 우선한다. (가이드 2-2, 2-3)

## 규칙

- 외부 데이터 API 호출은 **이 계층에서만** 한다.
- 정제·DB 적재를 하지 않는다 (`preprocessing`의 책임). LLM을 호출하지 않는다.
- 모든 요청은 `http_client.RateLimitedClient`를 거친다 (딜레이, 재시도, 키 마스킹).
- 원본 저장 시 요청 파라미터의 `crtfc_key`를 제거한다. 키가 들어간 URL을 로그에 남기지 않는다.
- 재실행 안전: 이미 받은 공시 원문(`documents/<rcept_no>.zip`)은 다시 요청하지 않는다.
  원문 저장은 임시 파일 → rename으로 원자적으로 한다 (중간에 끊긴 zip이 "받은 것"으로 취급되지 않게).
- DART 치명 상태(키 오류 010/011/012/901, 일일 한도 020)는 즉시 중단한다. 그 외 실패는 `failures.jsonl`에 남기고 계속한다.

## DART

- `list.json`은 응답에 `pblntf_ty`를 주지 않는다 → 유형별로 조회하고 요청 파라미터를 원본과 함께 저장한다.
- 원문 링크: `src.common.dart.viewer_url(rcept_no)`.
- 대상 법인은 `config/company_mapping.yaml`에서 `verified: true`인 것만 수집한다.
- **시장 전체 목록(분기 B-1)**: `collect_market_listings`가 `corp_code` 없이 외부감사관련(F) 목록을
  분기 단위로 저장한다 (`market/`). 지난 분기 페이지는 캐시되어 중단 후 재실행하면 이어서 받는다.
  필터링(Big4 제출 여부)은 하지 않는다 — 원본은 전체를 보존하고 걸러내는 일은 `preprocessing`이 한다.

## 네이버 뉴스

- 검색 API는 **NAVER API HUB**(네이버 클라우드)로 이관됐다. 주소는 `settings.yaml`의 `news.api_url`,
  인증 헤더는 `X-NCP-APIGW-API-KEY-ID` / `X-NCP-APIGW-API-KEY`. 옛 개발자센터 주소·헤더를 쓰지 않는다.
- **약관(검색 API 특약 2.1~2.4)상 저장·가공·AI 입력은 금지 대상이다.** 팀 결정으로 프로젝트 기간에만 사용하며
  원문·파생 데이터는 로컬에만 둔다. 종료 시 처리 목록은 루트 `CLAUDE.md` 체크리스트 (`evaluation/gate_decision.md`).
- 검색어당 약 1,000건(`start` ≤ 1000)까지만 조회되고 기간 지정이 없다 → `기업 별칭 × 세부 주제`로 분할한다.
  기본 키워드 단독 검색은 끈다 (`include_base_query: false`, 1차 확인에서 몇 주치만 닿고 무관 기사가 많았음).
- 기업별 "완전 수집 시작일"(모든 검색어가 잘림 없이 닿는 날짜)을 `verify_news_gate.py`가 계산한다.
  기업 간 비교·급증 분석은 공통 시작일 이후로 한정한다.
- **주제 선택 규칙:** 한 주제의 모든 검색어(4개 법인 × 별칭 2개)가 기간 시작까지 닿을 때만 그 주제를 쓴다.
  리포트의 "주제별 완전 수집 여부" 표로 판정하고, 탈락 주제와 이유는 `settings.yaml` 주석에 남긴다.
- 검색어마다 `total`, 확보한 가장 오래된 `pubDate`, 잘림 여부를 `query_log.jsonl`에 남긴다.
- 기간 시작보다 오래된 기사에 도달하면 페이지 요청을 멈춘다 (호출 절약).
- 한도 수치는 공식 문서로 재확인하고 `config/settings.yaml`에서 조정한다.
