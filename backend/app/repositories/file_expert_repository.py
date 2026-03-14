from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.domain.models.expert_profile import ExpertProfile

logger = logging.getLogger(__name__)


class FileExpertRepository:
    """负责加载和保存专家配置、提示词及规范文档。"""

    def __init__(self, root: Path) -> None:
        """初始化用户自定义专家目录。"""

        self.root = Path(root)

    def list(self) -> list[ExpertProfile]:
        """合并内置专家与用户专家配置，并返回完整专家列表。"""

        items: list[ExpertProfile] = []
        packaged_root = Path(__file__).resolve().parents[1] / "builtin_experts"
        builtin_payloads = self._load_payloads(packaged_root, mark_custom=False)
        user_payloads = self._load_payloads(self.root, mark_custom=True)

        merged_ids = sorted(set(builtin_payloads) | set(user_payloads))
        for expert_id in merged_ids:
            payload = dict(builtin_payloads.get(expert_id) or {})
            for key, value in (user_payloads.get(expert_id) or {}).items():
                if key in {"system_prompt", "review_spec"} and not value:
                    continue
                payload[key] = value
            if not payload:
                continue
            payload["custom"] = bool((user_payloads.get(expert_id) or {}).get("custom", payload.get("custom", False)))
            items.append(ExpertProfile.model_validate(payload))
        logger.info(
            "loaded %s experts from roots=%s",
            len(items),
            [str(root) for root in {self.root, packaged_root}],
        )
        return items

    def save(self, expert: ExpertProfile) -> ExpertProfile:
        """把专家配置、提示词和规范文档保存到用户目录。"""

        target_dir = self.root / expert.expert_id
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = expert.model_dump(mode="json")
        system_prompt = str(payload.pop("system_prompt", ""))
        review_spec = str(payload.pop("review_spec", ""))
        (target_dir / "expert.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (target_dir / "prompt.md").write_text(system_prompt, encoding="utf-8")
        if review_spec:
            (target_dir / "review_spec.md").write_text(review_spec, encoding="utf-8")
        return expert

    def _load_payloads(self, root: Path, *, mark_custom: bool) -> dict[str, dict]:
        """从给定目录读取 expert.yaml、prompt.md 和 review_spec.md。"""

        payloads: dict[str, dict] = {}
        if not root.exists():
            logger.warning("expert root missing: %s", root)
            return payloads
        for expert_yaml in sorted(root.glob("*/expert.yaml")):
            payload = yaml.safe_load(expert_yaml.read_text(encoding="utf-8")) or {}
            expert_id = str(payload.get("expert_id") or "")
            if not expert_id:
                continue
            prompt_path = expert_yaml.parent / "prompt.md"
            review_spec_path = expert_yaml.parent / "review_spec.md"
            payload["system_prompt"] = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
            payload["review_spec"] = review_spec_path.read_text(encoding="utf-8") if review_spec_path.exists() else ""
            payload["custom"] = bool(payload.get("custom", mark_custom))
            payloads[expert_id] = payload
        return payloads
