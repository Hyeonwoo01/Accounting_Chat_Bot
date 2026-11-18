# CLAUDE.md — dashboard(대시보드) 계층

Streamlit. 저장된 결과를 조회·시각화하고 공시 Q&A 화면을 제공한다. 루트 `CLAUDE.md` 규칙이 우선한다. (가이드 9)

## 규칙 (위반 시 PR 반려)

- **조회 전용.** DART·뉴스 API 호출, LLM 분류·임베딩 생성, 결론 생성을 하지 않는다.
- `requests`, `openai`, `langchain` 등을 import하지 않는다. `src.collectors`, `src.preprocessing`, `src.analysis`도 import하지 않는다.
- LLM이 호출되는 곳은 **04 QA 페이지의 질문 처리** 한 곳이며, `src.rag.rag_pipeline`만 호출한다.
- 상단 결론은 `insights` 테이블에서 읽는다.
- 법인 범위·급증·감사보고서 월별 건수는 `news_firm_scope`·`news_spikes`·`audit_filings_monthly` 테이블에서 읽는다
  (`scripts/load_dashboard_tables.py`가 배치로 채움, `src/database/CLAUDE.md`). `data/processed/` 파일을 직접 읽지 않는다.
- `streamlit run dashboard/app.py`로 실행되므로 `app.py`에서 프로젝트 루트를 `sys.path`에 추가한다.
  `app.py`는 `st.navigation`으로 메뉴 이름을 정한다(App · Industry Overview · Issue Analysis · Big4 Comparison · QA).
  홈 화면 내용은 `home.py`. 새 페이지는 `pages/`에 두고 `app.py`의 목록에 추가해야 메뉴에 나온다.

## 필터 (가이드 9-1)

- 사이드바 **기간 · 기업 · 카테고리** 필터를 `session_state`로 공유한다.
- `filters.py`의 함수 하나가 필터 적용된 DataFrame을 반환하고, **모든 차트가 이 함수를 거친다.**

## 페이지 (가이드 9-2)

| 페이지 | 내용 |
|---|---|
| 01 Industry Overview | 결론 + 근거 링크, 월별 기사량·급증 주석, 카테고리 분포, 월별 감성 추이, 공시 추이/타임라인, 하단 접이식 검수 정확도·처리 범위 |
| 02 Issue Analysis | 카테고리별 이슈 키워드·추이, 급증 시점별 배경 기사, 뉴스·공시 시점 비교 |
| 03 Big4 Comparison | 기업별 기사량·카테고리·감성(단일 기업 기사), 자체 공시(사업보고서) 지표, 감사보고서 제출 건수 추이, 공시 목록·DART 링크, 근거 없으면 "확인 자료 없음" |
| 04 QA | 질문, 답변, 근거(공시명·제출일·접수번호·링크·원문 구절), 예시 질문 버튼, 거절은 거절로 명확히 표시 |

## 차트 규칙 (가이드 9-3)

- 차트마다 기간, 표본 수, 집계 단위를 표시한다.
- 기사량을 경쟁력 순위로 해석하지 않는다.
- 감성 차트에 "LLM 분류 결과이며 표본 검수 정확도는 ○○%" 주석 (실측값만).
- 감사보고서 제출 건수 차트에는 "제출 건수 기준(매출 점유율 아님), 3~5월 집중은 결산 일정" 주석을 단다.
- 공시 목록은 **자체 공시**와 **제출한 고객사 감사보고서**를 나눠 보여준다. 감사보고서 행에는 고객사명을 함께 표시한다.
- 화면·캡처에 키, `.env`, 키가 포함된 URL이 노출되지 않게 한다.

## 구현 메모 (Day 6)

- 실행: `python scripts/load_dashboard_tables.py` 후 `streamlit run dashboard/app.py`. 예시 질문·검수 수치는 `settings.dashboard`.
- 데이터는 `dashboard/data.py`(캐시, 10분)만 DB에서 읽고, 페이지는 `filters.filtered(kind)`로만 받는다.
  필터 의미: 기업을 **모두** 고르면 업계 전체·글로벌 기사 포함, 일부만 고르면 그 법인의 한국 회원 법인 기사만.
  감사보고서 월별 건수(비중의 분모)는 기업 필터를 적용하지 않는다.
- 색은 엔터티 고정(`dashboard/charts.py`): 법인 4색, 카테고리 4색, 감성은 연파랑 `#C6E1FF`–연회색 `#E4E4E4`–연분홍 `#FDD1D9`. 한 차트에 y축 하나.
  팀 결정(11-17): 카테고리 막대(01·03)는 보라 단색 4단계(`PURPLE_TONES`), 03 연도별 감사보고서는 청보라 단색 4단계(`BLUE_TONES`)
  — 둘 다 같은 색상의 파스텔 톤,
  02 추이선은 기존 색상의 파스텔(`CATEGORY_LINE_COLORS`),
  02 키워드 막대는 카테고리별 같은 계열 농담(`CATEGORY_FAMILIES`).
  옅은 막대는 `style()`이 얇은 테두리를 붙여 카드 배경과 구분한다.
- 화면 문구에 설정·데이터 문자열을 넣을 때는 `filters.md()`를 거친다 (`~`가 두 번 나오면 취소선으로 렌더링됨).
- 테마(`dashboard/theme.py`, `.streamlit/config.toml`): 따뜻한 크림 배경·잉크 텍스트·필(pill) 버튼·배경의 얇은 주황 원호,
  글꼴 함초롬바탕(설치된 PC에서만, 없으면 Noto Serif KR). 모든 페이지는 `set_page_config` 직후 `theme.apply()`를 부른다.
  차트는 단색 카드 위에 그린다(배경 원호가 데이터 위를 지나지 않게). 차트 카드에 CSS padding을 주지 않는다
  (Streamlit이 그 상자 폭으로 그림을 그려 범례가 잘림) — 여백은 `charts.style`의 margin으로 준다.
  특정 브랜드의 로고·이름·대표색은 쓰지 않는다.
- 02 카테고리별 월간 기사량·03 법인별 월간 기사량은 `charts.hover_highlight()`로 그린다: 모든 선이 연하게 있다가
  마우스를 올린 선만 진해진다. `st.plotly_chart`는 이벤트 처리를 못 해서 컴포넌트 iframe 안에서 plotly.js로 그린다
  (plotly.js 약 5MB를 인라인 — 오프라인에서도 동작). 막대 차트에는 쓰지 않는다(선 차트 전용).
- 01 월별 감성 비중, 03 법인별 카테고리 구성·법인별 감성 비중(누적 막대)은 윤곽선을 그리지 않는다.
- 결론(`insights`)은 한눈에 읽히도록 번호 붙은 한 줄씩 보여주고, 근거 문서 링크는 접힌 영역에 모은다. 결론은 3개 안팎으로 둔다.
  첫 화면(App, `home.py`)과 Industry Overview 맨 위에 같은 카드를 보여 준다(`dashboard/conclusions.py`의 `show()`).
  결론 카드(`st.container(key="conclusions")`)는 페이지 배경색, 괄호 밖 글자만 획을 약간 굵게(`render.emphasize_outside_parens`,
  `.concl-strong` — font-weight 대신 text-stroke: 함초롬바탕은 400/700뿐이라 600이 볼드로 바뀐다).
- Issue Analysis 맨 위 "기간별 핵심 이슈"는 `issue_summaries`(두 사람이 승인한 분기 요약)만 읽는다. 사이드바 기간만 적용한다.
- iframe으로 그리는 차트는 `st.iframe`을 쓴다(`st.components.v1.html`은 제거 예정 경고, 없는 버전에서만 대체).
