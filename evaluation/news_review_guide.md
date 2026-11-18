# 뉴스 법인 판정 검수 기준 (가이드 4-2)

검수표: `evaluation/news_review_sample.csv` (`python scripts/make_news_review_sample.py`로 생성)

- 모집단: `data/processed/news_refined.jsonl` (5차 정제본: no_brand·scope_unknown·scope_conflict·career_bio·mention_only·research_cited·speaker_event·sale_lead_advisor·advisor·report_publication 제외. 남은 역할은 subject 뿐)
- 추출: 검색어 묶음(카테고리) 4개 × 25건, 단순 무작위, seed 20261113
- 판단 근거: **제목과 요약(`title`, `content`)만** 본다. 수집 범위가 제목·요약이기 때문이다.
  링크를 열어 본문을 봤다면 `note`에 적는다.
- 자동 판정 칸은 고치지 않는다. 아래 `human_*` 칸만 채운다.
- 열 순서: 기사(제목·요약) → `human_is_relevant` → 자동 판정 / **사람 칸** / 판정 근거 순으로 붙어 있다.
- **자동 판정이 맞으면 `=`만 입력한다.** 틀리면 올바른 값을 적는다.
  사람 칸은 일부러 비워 두었다. 미리 채워 두면 틀린 값도 그대로 넘기기 쉬워 정확도가 부풀려진다.
  `=`를 넣기 전에 판정 근거(`scope_rule`, `role_rule`)가 기사와 실제로 맞는지 본다.

## 채울 칸

| 칸 | 값 | 기준 |
|---|---|---|
| `human_is_relevant` | `1` / `0` | Big4 또는 한국 회원 법인이 **직접 행동한 기사의 주인공**이면 1. 아래 "관련성 기준"의 0 사례에 해당하면 0 |
| `human_company_scope` | `member_firm` / `global_network` / `industry_wide` / `unknown` | 아래 표 |
| `human_member_firm_ids` | `=` 또는 `pwc_korea` 등, 쉼표 구분 | 기사가 실제로 다루는 한국 회원 법인. **빈 칸은 "해당 법인 없음"으로 계산**되므로 자동 값과 같으면 반드시 `=` |
| `human_mention_role` | `subject` / `advisor` / `speaker_event` / `research_cited` / `career_bio` / `other` | 아래 표 |
| `reviewer` | 이름 | |
| `note` | 자유 | 판단이 애매한 이유, 규칙 개선 아이디어 |

`human_is_relevant = 0`이면 나머지 칸은 비워도 된다.

### company_scope

| 값 | 기준 |
|---|---|
| `member_firm` | 한국 회원 법인(삼일PwC, 딜로이트안진, EY한영, 삼정KPMG 등)의 일 |
| `global_network` | 글로벌 본사·해외 법인의 발표나 일. **브랜드명이 같다고 한국 법인의 일로 보지 않는다** |
| `industry_wide` | Big4나 회계업계 전체를 다룸 (특정 1개 법인의 일이 아님) |
| `unknown` | 제목·요약만으로 판단할 수 없음 |

### mention_role

| 값 | 기준 |
|---|---|
| `subject` | 그 법인이 기사의 주인공 (발표·출시·채용·제재 대상 등) |
| `advisor` | 다른 회사 거래·사업의 자문사·평가기관·주관사 |
| `speaker_event` | 행사 주최·강연·발표자로 등장 |
| `research_cited` | 그 법인의 보고서·조사가 인용됨 |
| `career_bio` | 인물 경력 소개에 이름이 나옴 ("○○ 출신") |
| `other` | 그 밖의 언급 |

> **데이터 기준 주의 (2026-11-13):** 아래 검수 이력 v1~v4의 정제본 수치는 로컬에서 약 50개 주제(별칭 3개)로 수집한 기사 기준이다. 팀 확정 설정(14개 주제 × 별칭 2개, 112개 검색어, 잘림 0)으로 재수집한 뒤 `tag_news_firms.py` → `check_news_refined.py` → `make_news_review_sample.py --force` 를 다시 실행하고 그 표본으로 검수한다.

## 관련성 기준 (검수 v1에서 확정, 2026-11-13)

`1`: Big4·한국 회원 법인이 직접 행동한 기사의 주인공 (서비스 출시·투자·제휴·채용·Big4 단독 인사·제재·감리·수임 경쟁 등).

`0`:
- Big4 보고서·연구원 자료가 **출처로만** 인용되고 기사 주제는 다른 산업·정책 (보고서 발간 자체가 기사여도 0)
- Big4 인물이 다른 기관 행사에 **발표자·참여자로만** 나옴 (EY 최우수 기업가상처럼 Big4가 주최한 시상도 수상자가 주인공이면 0)
- 인물 경력 소개에만 나옴 ("○○ 출신", "○○를 거쳐", "○○에서 사회생활을 시작")
- 상 이름·제3자의 진로 등 무관한 문맥에만 나옴
- **매각 주관사**로만 나옴 — 기사 주제가 매각 대상 회사의 거래임 (검수 v2에서 추가, 홈플러스 매각 기사)
- **자문사·평가기관** 등 다른 회사 거래·사업의 조력자로 나옴 (검수 v3에서 추가)
- Big4가 **보고서·조사·설문을 발간**한 것이 기사 내용 (검수 v3에서 추가. 감사보고서·감사 의견은 Big4 감사 업무라 해당 없음)
- 여러 기관 인사 명단 속에 Big4 인사가 섞여 있음 (검수 v3: [인사] 과학기술정보통신부 외)
- Big4가 주최한 대회·시상이라도 수상자가 주인공이면 0 (검수 v2: KPMG 아이디어톤)

3~5차 정제에서 보고서 인용·행사 발표·경력·매각 주관·자문사·보고서 발간 유형은 규칙으로 뺐다. 규칙이 놓친 사례를 찾는 것이 이번 검수의 목적이다.

## 검수 이력

| 버전 | 검수표 | 결과 | 비고 |
|---|---|---|---|
| v1 | `news_review_sample_v1.csv` | `news_review_result_v1.md` | 2차 정제본(17,635건) 표본. 관련 없음 12건만 기록하고 중단 → **무작위 검수가 아니므로 정확도로 인용하지 않는다.** 발견한 오판 유형으로 규칙을 고침 |
| v2 | `news_review_sample_v2.csv` | — | 3차 정제본(12,684건) 표본. 관련 없음 5건 기록 후 중단 → 정확도로 인용하지 않음. 매각 주관사 기사 제외 기준을 추가함 |
| v3 | `news_review_sample_v3.csv` | — | 4차 정제본(10,624건) 표본. 관련 없음 1건 기록 후 중단 → 정확도로 인용하지 않음. 자문사·보고서 발간 제외 기준을 추가함 |
| v4 | `news_review_sample.csv` | `news_review_result.md` | 5차 정제본(6,143건)에서 새로 추출 (seed 동일) |

## 정확도 계산

검수를 마치면 `python scripts/score_news_review.py` → `evaluation/news_review_result.md`.
일부만 채운 상태로 돌려도 되고, 미검수 건수가 함께 표시된다.

- `human_is_relevant`가 빈 행은 미검수로 보고 뺀다. `human_company_scope`·`human_mention_role`이 빈 칸이면 그 항목만 뺀다.
- 항목별 정확도 = 자동 판정과 사람 판정이 같은 건수 ÷ `human_is_relevant = 1`인 건수
  (`company_scope`, `mention_role`, `member_firm_ids` 각각)
- 관련성 = `human_is_relevant = 1` 건수 ÷ 100
- 카테고리별로 따로 낸 뒤, 전체 값은 **정제본 기사 수로 가중**한다 (같은 수로 뽑았기 때문)
- 표본 25건이라 카테고리별 수치의 오차가 크다. 수치와 함께 표본 수를 적는다.
- 결과는 `evaluation/evaluation_report.md`에 적는다. 측정하지 않은 수치는 쓰지 않는다.
