from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.java_quality_signal_extractor import JavaQualitySignalExtractor


class CodeObservationExtractor:
    """语言无关的 observation 提取入口。"""

    def __init__(self) -> None:
        self._java_extractor = JavaQualitySignalExtractor()

    def extract(
        self,
        *,
        file_path: str,
        target_hunk: dict[str, Any] | None = None,
        repository_context: dict[str, Any] | None = None,
        full_diff: str = "",
    ) -> dict[str, object]:
        language = self._infer_language(file_path)
        if language == "java":
            payload = self._java_extractor.extract(
                file_path=file_path,
                target_hunk=target_hunk,
                repository_context=repository_context,
                full_diff=full_diff,
            )
            normalized = dict(payload or {})
            normalized["language"] = "java"
            return normalized
        return self._empty_payload(language)

    def _infer_language(self, file_path: str) -> str:
        suffix = Path(str(file_path or "")).suffix.lower()
        if suffix == ".java":
            return "java"
        return "text"

    def _empty_payload(self, language: str) -> dict[str, object]:
        return {
            "language": str(language or "text"),
            "signals": [],
            "summary": "",
            "matched_terms": [],
            "signal_terms": {},
            "observations": [],
        }
