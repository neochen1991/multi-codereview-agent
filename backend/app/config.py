from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    APP_NAME: str = "Multi Code Review Agent"
    APP_VERSION: str = "0.1.0"
    API_PREFIX: str = "/api"
    STORAGE_ROOT: Path = Field(default=PROJECT_ROOT / "backend/app/storage")
    LOGS_ROOT: Path = Field(default=PROJECT_ROOT / "logs")
    DEFAULT_EXPERT_IDS: list[str] = Field(
        default_factory=lambda: [
            "correctness_business",
            "architecture_design",
            "security_compliance",
            "performance_reliability",
            "maintainability_code_health",
            "test_verification",
        ]
    )
    DEFAULT_LLM_PROVIDER: str = "dashscope-openai-compatible"
    DEFAULT_LLM_BASE_URL: str = "https://coding.dashscope.aliyuncs.com/v1"
    DEFAULT_LLM_MODEL: str = "kimi-k2.5"
    DEFAULT_LLM_API_KEY_ENV: str = "DASHSCOPE_API_KEY"

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            APP_NAME=os.getenv("APP_NAME", cls.model_fields["APP_NAME"].default),
            APP_VERSION=os.getenv("APP_VERSION", cls.model_fields["APP_VERSION"].default),
            API_PREFIX=os.getenv("API_PREFIX", cls.model_fields["API_PREFIX"].default),
            STORAGE_ROOT=Path(
                os.getenv("STORAGE_ROOT", str(cls.model_fields["STORAGE_ROOT"].default))
            ),
            LOGS_ROOT=Path(
                os.getenv("LOGS_ROOT", str(cls.model_fields["LOGS_ROOT"].default))
            ),
            DEFAULT_EXPERT_IDS=list(cls.model_fields["DEFAULT_EXPERT_IDS"].default_factory()),
            DEFAULT_LLM_PROVIDER=os.getenv(
                "DEFAULT_LLM_PROVIDER", cls.model_fields["DEFAULT_LLM_PROVIDER"].default
            ),
            DEFAULT_LLM_BASE_URL=os.getenv(
                "DEFAULT_LLM_BASE_URL", cls.model_fields["DEFAULT_LLM_BASE_URL"].default
            ),
            DEFAULT_LLM_MODEL=os.getenv(
                "DEFAULT_LLM_MODEL", cls.model_fields["DEFAULT_LLM_MODEL"].default
            ),
            DEFAULT_LLM_API_KEY_ENV=os.getenv(
                "DEFAULT_LLM_API_KEY_ENV", cls.model_fields["DEFAULT_LLM_API_KEY_ENV"].default
            ),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load()


settings = get_settings()
