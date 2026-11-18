## 변경 내용

- 

## 대상 계층

- [ ] common  - [ ] collectors  - [ ] preprocessing  - [ ] database(스키마)  - [ ] analysis  - [ ] rag  - [ ] dashboard  - [ ] config  - [ ] docs

## 체크리스트

### 보안
- [ ] `.env`, API 키, 키가 포함된 URL/로그/노트북 출력이 diff에 없다
- [ ] 새 키가 필요하면 `.env.example`에 이름만 추가했다
- [ ] 실제 키는 `.env`에만 넣었다 (`.env.example`에 값이 있으면 `pytest`가 실패한다)

### 계층 규칙
- [ ] 대시보드에서 외부 API 호출·실시간 LLM 분류를 하지 않는다
- [ ] `pytest` 통과 (계층 import 규칙 검사 포함)
- [ ] 계층 간 데이터는 저장소(`data/raw`, SQLite, 벡터DB)를 통해서만 주고받는다
- [ ] 수집·처리 스크립트는 재실행해도 중복 적재가 생기지 않는다

### 해당 시
- [ ] (database) 스키마 변경의 영향 계층을 설명에 적었다
- [ ] (analysis) `evaluation/evaluation_report.md`에 표본 정확도·처리 범위 근거를 기록했다 (실측값만)
- [ ] (rag) 답변에 근거 공시(공시명·제출일·접수번호·링크·원문 구절)가 표시되고, 근거 없는 질문은 거절된다
- [ ] (dashboard) 모든 차트가 공통 필터를 거친다

## 테스트 방법

- 
