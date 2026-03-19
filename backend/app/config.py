from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseModel):
    """定义项目默认路径、默认模型和全局常量设置。"""

    APP_NAME: str = "Multi Code Review Agent"
    APP_VERSION: str = "0.1.0"
    API_PREFIX: str = "/api"
    STORAGE_ROOT: Path = Field(default=PROJECT_ROOT / "backend/app/storage")
    SQLITE_DB_PATH: Path = Field(default=PROJECT_ROOT / "backend/app/storage/app.db")
    LOGS_ROOT: Path = Field(default=PROJECT_ROOT / "logs")
    CONFIG_PATH: Path = Field(default=PROJECT_ROOT / "config.json")
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

    @model_validator(mode="after")
    def _ensure_sqlite_db_path(self) -> "Settings":
        """Keep SQLite DB path aligned with storage root unless explicitly overridden."""

        default_db_path = PROJECT_ROOT / "backend/app/storage/app.db"
        if self.SQLITE_DB_PATH == default_db_path and self.STORAGE_ROOT != default_db_path.parent:
            self.SQLITE_DB_PATH = self.STORAGE_ROOT / "app.db"
        return self

    @classmethod
    def load(cls) -> "Settings":
        """从环境变量覆盖默认值，生成最终设置对象。"""

        return cls(
            APP_NAME=os.getenv("APP_NAME", cls.model_fields["APP_NAME"].default),
            APP_VERSION=os.getenv("APP_VERSION", cls.model_fields["APP_VERSION"].default),
            API_PREFIX=os.getenv("API_PREFIX", cls.model_fields["API_PREFIX"].default),
            STORAGE_ROOT=Path(
                os.getenv("STORAGE_ROOT", str(cls.model_fields["STORAGE_ROOT"].default))
            ),
            SQLITE_DB_PATH=Path(
                os.getenv("SQLITE_DB_PATH", str(cls.model_fields["SQLITE_DB_PATH"].default))
            ),
            LOGS_ROOT=Path(
                os.getenv("LOGS_ROOT", str(cls.model_fields["LOGS_ROOT"].default))
            ),
            CONFIG_PATH=Path(
                os.getenv("CONFIG_PATH", str(cls.model_fields["CONFIG_PATH"].default))
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
    """缓存 Settings，避免在每次导入时重复解析。"""

    return Settings.load()


settings = get_settings()
