from __future__ import annotations

import json
from pathlib import Path

from app.domain.models.review_skill import ReviewSkillProfile


class ReviewSkillRegistry:
    """扫描并加载扩展 skill 目录。"""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    def list_all(self) -> list[ReviewSkillProfile]:
        if not self.root.exists():
            return []
        skills: list[ReviewSkillProfile] = []
        for skill_dir in sorted(path for path in self.root.iterdir() if path.is_dir()):
            skill = self._load_skill(skill_dir)
            if skill is not None:
                skills.append(skill)
        return skills

    def get(self, skill_id: str) -> ReviewSkillProfile | None:
        for skill in self.list_all():
            if skill.skill_id == skill_id:
                return skill
        return None

    def _load_skill(self, skill_dir: Path) -> ReviewSkillProfile | None:
        skill_md = skill_dir / "SKILL.md"
        metadata_json = skill_dir / "metadata.json"
        if not skill_md.exists() or not metadata_json.exists():
            return None
        metadata = json.loads(metadata_json.read_text(encoding="utf-8"))
        return ReviewSkillProfile.model_validate(
            {
                **metadata,
                "prompt_body": skill_md.read_text(encoding="utf-8"),
                "skill_path": str(skill_dir),
            }
        )
