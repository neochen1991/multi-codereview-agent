from __future__ import annotations

from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.repositories.file_expert_repository import FileExpertRepository


class ExpertRegistry:
    """封装专家配置的读取、创建和更新逻辑。"""

    def __init__(self, root: Path) -> None:
        """初始化专家仓储。"""

        self._repository = FileExpertRepository(root)

    def list_enabled(self) -> list[ExpertProfile]:
        """返回当前启用状态的专家列表。"""

        return [expert for expert in self._repository.list() if expert.enabled]

    def list_all(self) -> list[ExpertProfile]:
        """返回全部专家，包括预置和自定义专家。"""

        return self._repository.list()

    def create(self, payload: dict[str, object]) -> ExpertProfile:
        """创建一个自定义专家。"""

        payload = self._normalize_llm_overrides(payload)
        expert = ExpertProfile.model_validate(payload | {"custom": True})
        return self._repository.save(expert)

    def update(self, expert_id: str, payload: dict[str, object]) -> ExpertProfile:
        """更新指定专家的配置。"""

        current = next((item for item in self._repository.list() if item.expert_id == expert_id), None)
        if current is None:
            raise KeyError(expert_id)
        normalized = self._normalize_llm_overrides(payload)
        expert = current.model_copy(update=normalized)
        return self._repository.save(expert)

    def delete(self, expert_id: str) -> None:
        """删除一个自定义专家。"""

        self._repository.delete(expert_id)

    def _normalize_llm_overrides(self, payload: dict[str, object]) -> dict[str, object]:
        """把空白模型配置字段归一化为 None，表示继承系统配置。"""

        normalized = dict(payload)
        for key in ("provider", "api_base_url", "api_key", "api_key_env", "model"):
            value = normalized.get(key)
            if isinstance(value, str) and not value.strip():
                normalized[key] = None
        return normalized
