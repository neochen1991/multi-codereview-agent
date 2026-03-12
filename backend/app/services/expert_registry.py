from __future__ import annotations

from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.repositories.file_expert_repository import FileExpertRepository


class ExpertRegistry:
    def __init__(self, root: Path) -> None:
        self._repository = FileExpertRepository(root)

    def list_enabled(self) -> list[ExpertProfile]:
        return [expert for expert in self._repository.list() if expert.enabled]

    def list_all(self) -> list[ExpertProfile]:
        return self._repository.list()

    def create(self, payload: dict[str, object]) -> ExpertProfile:
        payload = self._normalize_llm_overrides(payload)
        expert = ExpertProfile.model_validate(payload | {"custom": True})
        return self._repository.save(expert)

    def update(self, expert_id: str, payload: dict[str, object]) -> ExpertProfile:
        current = next((item for item in self._repository.list() if item.expert_id == expert_id), None)
        if current is None:
            raise KeyError(expert_id)
        normalized = self._normalize_llm_overrides(payload)
        expert = current.model_copy(update=normalized)
        return self._repository.save(expert)

    def _normalize_llm_overrides(self, payload: dict[str, object]) -> dict[str, object]:
        normalized = dict(payload)
        for key in ("provider", "api_base_url", "api_key", "api_key_env", "model"):
            value = normalized.get(key)
            if isinstance(value, str) and not value.strip():
                normalized[key] = None
        return normalized
