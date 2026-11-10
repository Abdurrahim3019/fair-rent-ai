"""App configuration: env vars and tunable constants."""
import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "edaletli-kiraye"
    langsmith_endpoint: str = "https://api.smith.langchain.com"  # EU: https://eu.api.smith.langchain.com

    database_url: str = f"sqlite:///{DATA_DIR / 'app.db'}"


settings = Settings()


def _export_env() -> None:
    """LangChain/LangSmith read os.environ, so mirror .env values there."""
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    tracing = settings.langsmith_tracing and bool(settings.langsmith_api_key)
    os.environ["LANGSMITH_TRACING"] = "true" if tracing else "false"
    if tracing:
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)


_export_env()


def llm_available() -> bool:
    return bool(settings.openai_api_key)


# USD per 1M tokens (input, output). Update when OpenAI prices change.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}
DEFAULT_MODEL_PRICE = (0.15, 0.60)

# Supported property types (bina.az rent categories). Extend here (e.g. "qaraj", "torpaq").
PROPERTY_TYPES: list[str] = ["mənzil", "həyət evi", "ofis", "obyekt"]
RESIDENTIAL_TYPES: set[str] = {"mənzil", "həyət evi"}
COMMERCIAL_TYPES: set[str] = {"ofis", "obyekt"}

# Verdict thresholds (fraction of median), per property type.
FAIR_THRESHOLDS: dict[str, float] = {
    "mənzil": 0.07,
    "həyət evi": 0.07,
    "ofis": 0.10,
    "obyekt": 0.10,
}
DEFAULT_FAIR_THRESHOLD = 0.07

# Sanity bounds for monthly rent per m² (AZN). Outside -> likely a sale price or typo.
MIN_PRICE_PER_M2 = 1.0
MAX_PRICE_PER_M2 = 150.0

# Minimum approved comparables / listings per district for a reliable result.
MIN_COMPARABLES = 5
TARGET_COMPARABLES = 8

# Scraping politeness.
REQUEST_DELAY_SEC = 2.0
MAX_RETRIES = 3
CACHE_TTL_HOURS = 24
MAX_SEARCH_PAGES = 3
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Agent.
AGENT_RECURSION_LIMIT = 8
MAX_AREA_DISTRICTS = 5
MAX_MATCHING_LISTINGS = 15


def fair_threshold(property_type: str) -> float:
    return FAIR_THRESHOLDS.get(property_type, DEFAULT_FAIR_THRESHOLD)
