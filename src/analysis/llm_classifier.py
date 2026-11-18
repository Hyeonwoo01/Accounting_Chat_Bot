"""LLM sentiment / category / issue extraction for news (guide 6). Batch only — never called by the dashboard.

Input is the title + Naver summary of refined articles (is_relevant). Output is validated against a
fixed schema; one retry on a malformed answer, then the article is left unclassified and reported.
Every stored row carries model_version, prompt_version and created_at.

Bump PROMPT_VERSION whenever SYSTEM_PROMPT changes and record why in evaluation/evaluation_report.md.
Prompt examples are synthetic: review-sample articles must never be reused as examples (guide 6-5).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

PROMPT_VERSION = "v1"
SENTIMENTS = ("positive", "neutral", "negative")
CATEGORIES = ("regulation_policy", "technology_trend", "market_competition", "supply_chain_infrastructure")

SYSTEM_PROMPT = """당신은 회계·감사·세무·컨설팅(전문서비스) 산업 뉴스를 분류하는 분석가다.
기사 제목과 요약만 보고 판단하며, 제목·요약에 없는 사실을 추가하지 않는다.

[감성] 기사 논조가 해당 산업·기업의 사업 환경에 우호적인지
- positive: 수주, 성장, 신규 서비스 출시, 우호적 정책, 수상 등
- negative: 제재, 징계, 소송, 실적 악화, 규제 강화, 사고 등
- neutral: 단순 사실 전달, 인사·행사, 판단하기 어려운 경우

[카테고리] 아래 4개 중 하나만 고른다. 애매하면 가장 가까운 것을 고르고 요약에 불확실함을 적는다.
- regulation_policy: 감사 기준, 회계·세무 규정, 감리·제재, 감사인 지정제, AI·개인정보 규제
- technology_trend: 생성형 AI, AI 감사, 업무 자동화, 디지털 전환, 새로운 서비스 모델
- market_competition: 수임·수주 경쟁, 실적, 제휴, M&A 자문, 인재 확보·채용, 경영진 인사
- supply_chain_infrastructure: 클라우드·LLM 공급자, 데이터 플랫폼, 정보보호·사이버보안

[출력] 아래 JSON 객체 하나만 출력한다. 다른 글은 쓰지 않는다.
{"sentiment": "positive|neutral|negative",
 "primary_category": "regulation_policy|technology_trend|market_competition|supply_chain_infrastructure",
 "issue_keywords": ["이슈 키워드 1~3개"],
 "issue_summary": "한 문장 요약"}

[예시 — 실제 기사가 아닌 가상의 문장]
제목: 가나회계법인, 생성형 AI 감사 도구 전면 도입
요약: 가나회계법인은 감사 업무 전반에 생성형 AI 도구를 도입해 문서 검토 시간을 줄였다고 밝혔다.
{"sentiment": "positive", "primary_category": "technology_trend",
 "issue_keywords": ["생성형 AI", "AI 감사"], "issue_summary": "가나회계법인이 감사 업무에 생성형 AI 도구를 도입했다."}

제목: 금융당국, 다라회계법인에 감사업무 제한 조치
요약: 증권선물위원회는 감사 절차를 소홀히 한 다라회계법인에 일부 감사업무 제한 조치를 의결했다.
{"sentiment": "negative", "primary_category": "regulation_policy",
 "issue_keywords": ["감리", "감사업무 제한"], "issue_summary": "증선위가 다라회계법인에 감사업무 제한 조치를 의결했다."}
"""


class ClassificationError(ValueError):
    """The model's answer is not a valid object for the schema."""


@dataclass(frozen=True)
class LLMReply:
    text: str
    model: str  # model name the API reports (stored as model_version)


# A chat function: (messages) -> LLMReply. Injected so tests and other providers need no SDK.
ChatFn = Callable[[list[dict[str, str]]], LLMReply]


def build_messages(title: str, summary: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"제목: {title}\n요약: {summary}"},
    ]


_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def parse_result(text: str) -> dict[str, Any]:
    """Validate the model answer. Raises ClassificationError with the reason."""
    m = _JSON_OBJECT.search(text or "")
    if not m:
        raise ClassificationError("no JSON object in answer")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ClassificationError(f"invalid JSON: {e.msg}") from e
    if data.get("sentiment") not in SENTIMENTS:
        raise ClassificationError(f"bad sentiment: {data.get('sentiment')!r}")
    if data.get("primary_category") not in CATEGORIES:
        raise ClassificationError(f"bad primary_category: {data.get('primary_category')!r}")
    keywords = data.get("issue_keywords")
    if not isinstance(keywords, list) or not 1 <= len(keywords) <= 3 or not all(
        isinstance(k, str) and k.strip() for k in keywords
    ):
        raise ClassificationError("issue_keywords must be 1-3 non-empty strings")
    summary = data.get("issue_summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ClassificationError("issue_summary must be a non-empty string")
    return {
        "sentiment": data["sentiment"],
        "primary_category": data["primary_category"],
        "issue_keywords": [k.strip() for k in keywords],
        "issue_summary": summary.strip(),
    }


def classify(chat: ChatFn, doc_id: str, title: str, summary: str, retries: int = 1) -> dict[str, Any]:
    """One article -> analysis_results row. Retries a malformed answer `retries` times, then raises."""
    messages = build_messages(title, summary)
    last_error = ""
    for _ in range(retries + 1):
        reply = chat(messages)
        try:
            result = parse_result(reply.text)
        except ClassificationError as e:
            last_error = str(e)
            continue
        return {
            "doc_id": doc_id,
            **result,
            "model_version": reply.model,
            "prompt_version": PROMPT_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    raise ClassificationError(last_error)


def openai_chat(api_key: str, model: str, temperature: float | None = None, timeout: float = 60.0) -> ChatFn:
    """ChatFn backed by the OpenAI Chat Completions API (JSON mode). Imported lazily.

    temperature=None omits the parameter: some models (e.g. gpt-6-luna) accept only their default.
    """
    from openai import OpenAI

    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=3)

    def chat(messages: list[dict[str, str]]) -> LLMReply:
        extra = {} if temperature is None else {"temperature": temperature}
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            response_format={"type": "json_object"},
            **extra,
        )
        return LLMReply(text=resp.choices[0].message.content or "", model=resp.model)

    return chat


def group_representatives(relevant_news: list[dict]) -> dict[str, list[str]]:
    """{representative doc_id: [all relevant doc_ids in its near-duplicate group]} (guide 6-1).

    The representative is the earliest-published relevant article of the group (dup_group_id,
    or the article itself when ungrouped). Only representatives are sent to the LLM; the other
    members get a copy of its result, so one event gets one label and volume counts stay intact.
    """
    groups: dict[str, list[dict]] = {}
    for d in relevant_news:
        groups.setdefault(d.get("dup_group_id") or d["doc_id"], []).append(d)
    out = {}
    for members in groups.values():
        members.sort(key=lambda d: (d["published_at"], d["doc_id"]))
        out[members[0]["doc_id"]] = [m["doc_id"] for m in members]
    return out


def propagate(results: dict[str, dict], groups: dict[str, list[str]]) -> list[dict]:
    """Copies of each representative's result for the other members of its group."""
    copies = []
    for rep, members in groups.items():
        if rep not in results:
            continue
        for m in members:
            if m != rep and m not in results:
                copies.append({**results[rep], "doc_id": m})
    return copies


def estimate_tokens(text: str, chars_per_token: float) -> int:
    """Rough token count for cost planning only (no tokenizer dependency)."""
    return max(1, round(len(text) / chars_per_token))
