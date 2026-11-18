"""뉴스 기사의 법인 범위·언급 역할 1차 판정 (가이드 4-2). 규칙 기반이며 모든 결과는 검수 대상이다.

판정하는 두 가지
  company_scope  "어느 법인인가"   member_firm / global_network / industry_wide / unknown
                 (가이드의 local_entity 는 한국 내 별도 현지 조직의 공식 명칭을 확인해 매핑에 넣은 뒤 추가한다)
  mention_role   "기사에서 무슨 역할인가"  subject / advisor / speaker_event / research_cited / career_bio / other

원칙
  - 브랜드명(PwC, 딜로이트 ...)만으로는 한국 회원 법인으로 연결하지 않는다.
    company_mapping.yaml 의 member_firms[*].aliases 가 기사에 직접 나와야 member_firm 이다.
  - 이름이 나왔다고 그 회사의 행동으로 보지 않는다. 제목에 나오면 subject, 아니면 주변 문맥으로 역할을 고른다.
"""

from __future__ import annotations

import re
from typing import Any

from src.preprocessing.company_matcher import CompanyMatcher

# 업계 전체를 가리키는 말: "빅4", "Big 4", "4대 회계법인", "회계법인들", "대형 회계법인", "회계업계"
INDUSTRY_WORDS = re.compile(r"빅\s?4|Big\s?4|4대\s?(회계법인|법인)|회계법인들|대형\s?회계법인|회계업계", re.I)
# 한국 밖(글로벌 본사·해외)을 가리키는 말
GLOBAL_WORDS = re.compile(
    r"글로벌\s?(본사|네트워크|CEO|회장)|미국|영국|해외\s?본사|전\s?세계|인터내셔널|international", re.I
)
# 언급 역할 규칙. 기업명 주변 문맥(앞 60자~뒤 80자)에서 위에서부터 먼저 맞는 것으로 정한다.
ROLE_RULES = [
    # 인물 경력 소개: "삼정KPMG 출신", "딜로이트를 거쳐", "~업무를 수행한 경력", "한영회계법인에서 사회생활을 시작"
    # (검수 v1: "거쳐"·"경력"·"사회생활" 누락으로 경력 기사가 research_cited/advisor/speaker_event 로 오판됨)
    ("career_bio", re.compile(
        r"출신|거쳐|거쳤|거친|지냈|역임|근무했|재직했|경력(?!\s?(?:직|채용|회계사|사원|자\b))|사회생활을?\s?시작|첫\s?직장|전\s?대표|그룹장을|파트너를\s?지"
    )),
    # 보고서·조사 인용: "삼일PwC경영연구원 보고서에 따르면"
    ("research_cited", re.compile(r"보고서|분석에\s?따르면|조사에\s?따르면|연구원은|경영연구원|펴낸|발간한|설문")),
    # 행사·강연 등장. 단어 일부만 맞는 오탐을 막는다 (검수 v1: "전국 무대에서"의 "무대", "토론토대학교"의 "토론")
    ("speaker_event", re.compile(
        r"서밋|소개했|강연|특강|대담|연사|발표했|발표를|설명회|세미나|주최|시상|기업가상|패널|컨퍼런스|콘퍼런스|포럼|토론(?!토)"
    )),
    # 거래·사업의 자문사·평가기관
    ("advisor", re.compile(r"자문|주관|외부평가|평가기관|실사|매각\s?주관|맡았|맡고|참여한다|참여했|협업|선정")),
]
# 요약에서 그 회사가 주어로 나와 직접 행동한 경우: "<회사명>은/는/이/가 ... 출시했다/밝혔다"
# (회사명 바로 뒤 조사 + 60자 안의 행동 동사). 위 ROLE_RULES 에 안 걸렸을 때만 본다.
SUBJECT_PARTICLE = r"(?:은|는|이|가)\s"
ACTION_VERBS = re.compile(
    r"밝혔|출시|개최|발표|도입|선임|체결|출범|공개|발간|선정됐|수상|개소|협약|론칭|오픈|확대|강화|진출|신설|영입|채용|구축|개발"
)
# 글로벌 조직을 분명히 가리키는 말. member_firm 판정과 함께 나오면 판정 충돌로 본다 ("미국" 같은 지명은 제외)
STRONG_GLOBAL = re.compile(r"글로벌\s?(본사|네트워크|CEO|회장)|해외\s?본사|global\s?(CEO|chair)", re.I)
# 매각 주관사 역할: "매각 주관사인 삼일회계법인", "삼정KPMG가 매각을 주관". 검수 v2에서 관련 없음으로 정함 (2026-11-13)
SALE_LEAD = re.compile(r"매각\s?주관|매각을\s?주관")
# 보고서·조사 발간: "삼정KPMG, ~ 보고서 발간", "EY한영은 ~ 설문조사 결과를 발표". 검수 v3에서 관련 없음으로 정함 (2026-11-13)
# 감사보고서(감사 의견)는 Big4 감사 업무 자체라 제외하지 않는다
REPORT_PUB = re.compile(r"(?<!감사)보고서|리포트|발간|펴냈|펴낸|백서|핸드북|설문|조사\s?결과|서베이|survey|report", re.I)
TAG = re.compile(r"<[^>]+>")  # 네이버 응답의 강조 태그 <b>...</b>


def plain(text: str | None) -> str:
    """태그와 자주 나오는 HTML 엔티티만 지운다 (판정용. 저장용 정제는 text_cleaning)."""
    return TAG.sub("", text or "").replace("&quot;", '"').replace("&amp;", "&")


def _context(text: str, words: list[str]) -> str:
    """기업명이 처음 나오는 위치 주변만 잘라 역할 판정에 쓴다 (기사 다른 문장의 단어에 휘둘리지 않게)."""
    hits = [m.start() for w in words for m in [re.search(re.escape(w), text)] if m]
    if not hits:
        return text
    p = min(hits)
    return text[max(0, p - 60): p + 80]


class FirmScopeTagger:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self.matcher = CompanyMatcher(mapping)
        # company_id -> [(member_firm_id, aliases)]
        self.members: dict[str, list[tuple[str, list[str]]]] = {}
        # company_id -> 역할 판정 때 위치를 찾을 이름들 (브랜드 별칭 + 회원 법인 별칭)
        self.names: dict[str, list[str]] = {}
        for c in mapping.get("companies", []):
            cid = c["company_id"]
            firms = [(m["member_firm_id"], list(m.get("aliases", []))) for m in c.get("member_firms", [])]
            self.members[cid] = firms
            self.names[cid] = (list(c.get("aliases", [])) + list(c.get("weak_aliases", []))
                               + [a for _, aliases in firms for a in aliases])

    def tag(self, title_raw: str | None, desc_raw: str | None) -> dict[str, Any]:
        title, desc = plain(title_raw), plain(desc_raw)
        text = f"{title} {desc}"
        mentioned = self.matcher.match(text)
        subjects = self.matcher.match(title)
        members = sorted(mid for cid in mentioned for mid, aliases in self.members.get(cid, [])
                         if any(a in text for a in aliases))

        # 1) 어느 법인인가 — 별칭만으로 판정 (제목 여부와 무관)
        if not mentioned:
            scope, srule = ("industry_wide", "브랜드 없음 + 업계 표지어") if INDUSTRY_WORDS.search(text) \
                else ("unknown", "브랜드 없음")
        elif len(subjects) >= 2 or INDUSTRY_WORDS.search(title):
            scope, srule = "industry_wide", "제목에 복수 브랜드 또는 업계 표지어"
        elif subjects and GLOBAL_WORDS.search(title):
            scope, srule = "global_network", "제목에 브랜드 + 글로벌 표지어"
        elif members:
            scope, srule = "member_firm", "한국 회원 법인 별칭"
        elif GLOBAL_WORDS.search(text):
            scope, srule = "global_network", "요약에 글로벌 표지어, 한국 법인 별칭 없음"
        else:
            scope, srule = "unknown", "브랜드명만 있고 글로벌/한국 구분 근거 없음"
        region = {"member_firm": "KR", "global_network": "GLOBAL"}.get(scope, "unknown")

        # 2) 기사에서 무슨 역할인가
        if subjects:
            role, rrule = "subject", "제목에 브랜드"
        elif not mentioned:
            role, rrule = "other", "브랜드 없음"
        else:
            names = [w for cid in mentioned for w in self.names.get(cid, [])]
            ctx = _context(desc, names)
            role, rrule = "other", "규칙 미해당"
            for name, pattern in ROLE_RULES:
                m = pattern.search(ctx)
                if m:
                    role, rrule = name, f"기업명 주변 '{m.group(0)}'"
                    break
            else:
                hit = _subject_in_body(desc, names)
                if hit:
                    role, rrule = "subject", f"요약에서 주어+행동 '{hit}'"

        # 3) 매각 주관사로 나오는가 — 기업명 주변(제목 전체 + 요약의 기업명 앞뒤)에서 본다
        names = [w for cid in mentioned for w in self.names.get(cid, [])]
        sale_lead = bool(mentioned) and (_near(title, names, SALE_LEAD) or _near(desc, names, SALE_LEAD))
        # 4) 보고서·조사를 낸 것이 기사의 내용인가 — 제목에 브랜드와 함께, 또는 요약의 기업명 앞뒤에서 본다
        report_pub = bool(mentioned) and (bool(subjects and REPORT_PUB.search(title))
                                          or _near(desc, names, REPORT_PUB, before=30, after=60))

        return {
            "company_ids": mentioned, "subject_company_ids": subjects, "member_firm_ids": members,
            "company_scope": scope, "region": region, "scope_rule": srule,
            "mention_role": role, "role_rule": rrule,
            "scope_conflict": scope == "member_firm" and bool(STRONG_GLOBAL.search(text)),
            "sale_lead_advisor": sale_lead,
            "report_publication": report_pub,
            "needs_review": True,
        }


def _near(text: str, names: list[str], pattern: re.Pattern[str], before: int = 30, after: int = 20) -> bool:
    """기업명 바로 앞뒤(기본 앞 30자·뒤 20자)에 pattern 이 있는가. 같은 문장의 다른 회사 역할과 섞이지 않게 좁게 본다."""
    for n in set(names):
        for m in re.finditer(re.escape(n), text):
            left = text[max(0, m.start() - before): m.start()].rsplit(". ", 1)[-1]   # 같은 문장 안에서만
            right = text[m.end(): m.end() + after].split(". ", 1)[0]
            if pattern.search(left + n + right):
                return True
    return False


def _subject_in_body(desc: str, names: list[str]) -> str | None:
    for n in sorted(set(names), key=len, reverse=True):  # 긴 이름부터 ("삼일PwC" 가 "PwC" 보다 먼저)
        for m in re.finditer(re.escape(n) + SUBJECT_PARTICLE, desc):
            verb = ACTION_VERBS.search(desc, m.end(), m.end() + 60)
            if verb:
                return f"{m.group(0).strip()} … {verb.group(0)}"
    return None


def exclude_reason(tagged: dict[str, Any]) -> str | None:
    """정제본에서 뺄 이유. None 이면 남긴다. 뺀 기사도 지우지 않고 사유와 함께 전체 파일에 남긴다.

    1차 정제
    - no_brand        : 제목·요약에 Big4 이름이 없음 (네이버가 본문으로만 맞춘 무관 기사)
    - scope_unknown   : 이름은 있지만 한국 회원 법인인지 글로벌인지 근거가 없음
    2차 정제 (가이드 4-2 "브랜드명 언급과 실제 발표·행동을 구분", "글로벌 발표를 한국 법인으로 연결하지 않는다")
    - scope_conflict  : 한국 회원 법인으로 판정됐지만 글로벌 본사·CEO 등 글로벌 조직 표현이 함께 있음
    - career_bio      : 인물 경력 소개에 이름이 나올 뿐 그 회사의 행동이 아님
    - mention_only    : 이름은 나오지만 그 회사의 발표·행동·역할을 찾지 못함 (역할 other)
    3차 정제 (검수 v1 기준, 2026-11-13: Big4가 기사의 주인공이거나 거래 자문사인 경우만 관련 있음)
    - research_cited  : Big4 보고서·연구원 자료가 출처로 인용될 뿐 기사의 주제가 다른 산업·정책임
    - speaker_event   : Big4 인물이 다른 기관 행사에 발표자·참여자로 나올 뿐임
    4차 정제 (검수 v2 기준, 2026-11-13)
    - sale_lead_advisor : Big4가 매각 주관사로 나오는 기사 (매각 대상 회사의 거래가 주제)
    5차 정제 (검수 v3 기준, 2026-11-13: Big4가 직접 행동한 주인공 기사만 남긴다)
    - advisor            : 자문사·평가기관 등 다른 회사 거래·사업의 조력자로 나오는 기사 전체
    - report_publication : Big4가 보고서·조사·설문을 낸 것이 기사 내용 (감사보고서는 제외)
    """
    if not tagged["company_ids"]:
        return "no_brand"
    if tagged["company_scope"] == "unknown":
        return "scope_unknown"
    if tagged.get("scope_conflict"):
        return "scope_conflict"
    if tagged["mention_role"] == "career_bio":
        return "career_bio"
    if tagged["mention_role"] == "other":
        return "mention_only"
    if tagged["mention_role"] in ("research_cited", "speaker_event"):
        return tagged["mention_role"]
    if tagged.get("sale_lead_advisor"):
        return "sale_lead_advisor"
    if tagged["mention_role"] == "advisor":
        return "advisor"
    if tagged.get("report_publication"):
        return "report_publication"
    return None
