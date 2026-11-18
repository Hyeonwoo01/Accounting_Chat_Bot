"""Central access point for settings, paths and API keys.

All layers read secrets through this module, never via os.environ directly, so that
key handling (loading, validation, never printing values) stays in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RAW_NEWS_DIR = RAW_DIR / "news"
RAW_DART_DIR = RAW_DIR / "dart"
PROCESSED_DIR = DATA_DIR / "processed"
EVALUATION_DIR = PROJECT_ROOT / "evaluation"

SECRET_NAMES = ("DART_API_KEY", "OPENAI_API_KEY", "NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET")

# Real environment variables take precedence over .env (override=False).
load_dotenv(PROJECT_ROOT / ".env", override=False)


class MissingSecretError(RuntimeError):
    """Raised when a required key is not configured. Carries only the key name."""


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise MissingSecretError(
            f"{name} is not set. Copy .env.example to .env and fill in the value."
        )
    return value


def get_dart_api_key() -> str:
    return _require("DART_API_KEY")


def get_openai_api_key() -> str:
    return _require("OPENAI_API_KEY")


def get_naver_credentials() -> tuple[str, str]:
    return _require("NAVER_CLIENT_ID"), _require("NAVER_CLIENT_SECRET")


def mask_secret(text: str) -> str:
    """Replace any configured secret values in `text` (URL, error message) before logging."""
    for name in SECRET_NAMES:
        value = os.getenv(name, "").strip()
        if value:
            text = text.replace(value, "****")
    return text


def _load_yaml(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    return _load_yaml("settings.yaml")


@lru_cache(maxsize=1)
def load_company_mapping() -> dict[str, Any]:
    return _load_yaml("company_mapping.yaml")


@dataclass(frozen=True)
class Period:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"period.start {self.start} is after period.end {self.end}")


def get_period() -> Period:
    p = load_settings()["period"]
    return Period(date.fromisoformat(str(p["start"])), date.fromisoformat(str(p["end"])))


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_database_path() -> Path:
    return _resolve(os.getenv("DATABASE_PATH") or load_settings()["paths"]["database"])


def get_vectordb_path() -> Path:
    return _resolve(os.getenv("VECTOR_DB_PATH") or load_settings()["paths"]["vectordb"])


def get_llm_model() -> str:
    return os.getenv("LLM_MODEL") or load_settings()["models"]["llm"]


def get_embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL") or load_settings()["models"]["embedding"]


def get_verified_entities() -> list[dict[str, str]]:
    """Korean member entities whose corp_code a human has verified. Unverified ones are excluded."""
    entities = []
    for company in load_company_mapping()["companies"]:
        for entity in company.get("kr_entities", []):
            if entity.get("verified") and entity.get("corp_code"):
                entities.append(
                    {
                        "company_id": company["company_id"],
                        "name": entity["name"],
                        "corp_code": str(entity["corp_code"]),
                    }
                )
    return entities


NAVER_NEWS_DISABLED_REASON = (
    "Naver search API news is disabled (config/settings.yaml news.enabled: false). "
    "NAVER API 서비스 이용약관 (2026-10-07) 검색 API 특약 2.1-2.3 and 일반조건 7.3③ prohibit storing, "
    "processing or feeding search results to AI for analysis. See evaluation/gate_decision.md."
)


def naver_news_enabled() -> bool:
    return bool(load_settings()["news"].get("enabled", False))


def get_big4_corp_codes() -> set[str]:
    """corp_codes of the Big4 Korean entities. A DART row with one of these corp_codes is the firm's
    own filing; any other corp_code is a client filing submitted by the firm."""
    return {
        str(e["corp_code"])
        for c in load_company_mapping()["companies"]
        for e in c.get("kr_entities", [])
        if e.get("corp_code")
    }
