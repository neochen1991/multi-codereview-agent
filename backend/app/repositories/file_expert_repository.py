from __future__ import annotations

import logging
from pathlib import Path

import yaml

from app.domain.models.expert_profile import ExpertProfile

logger = logging.getLogger(__name__)


class FileExpertRepository:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list(self) -> list[ExpertProfile]:
        items: list[ExpertProfile] = []
        roots = [self.root]
        packaged_root = Path(__file__).resolve().parents[1] / "builtin_experts"
        if packaged_root != self.root:
            roots.append(packaged_root)
        seen_ids: set[str] = set()
        for root in roots:
            if not root.exists():
                logger.warning("expert root missing: %s", root)
                continue
            for expert_yaml in sorted(root.glob("*/expert.yaml")):
                payload = yaml.safe_load(expert_yaml.read_text(encoding="utf-8")) or {}
                expert_id = str(payload.get("expert_id") or "")
                if expert_id in seen_ids:
                    continue
                prompt_path = expert_yaml.parent / "prompt.md"
                payload["system_prompt"] = (
                    prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else ""
                )
                payload["custom"] = bool(payload.get("custom", False))
                items.append(ExpertProfile.model_validate(payload))
                if expert_id:
                    seen_ids.add(expert_id)
        logger.info("loaded %s experts from roots=%s", len(items), [str(root) for root in roots])
        return items

    def save(self, expert: ExpertProfile) -> ExpertProfile:
        target_dir = self.root / expert.expert_id
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = expert.model_dump(mode="json")
        system_prompt = str(payload.pop("system_prompt", ""))
        (target_dir / "expert.yaml").write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        (target_dir / "prompt.md").write_text(system_prompt, encoding="utf-8")
        return expert
