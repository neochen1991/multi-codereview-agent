from __future__ import annotations

import json
import re
from pathlib import Path

from app.domain.models.review_skill import ReviewSkillProfile
from app.domain.models.review_tool_plugin import ReviewToolPlugin
from app.services.review_skill_registry import ReviewSkillRegistry
from app.services.tool_plugin_loader import ToolPluginLoader

ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class ExtensionEditorService:
    """负责 extensions/skills 与 extensions/tools 的读取与编辑。"""

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root)
        self._skills_root = self._project_root / "extensions" / "skills"
        self._tools_root = self._project_root / "extensions" / "tools"

    def list_skills(self) -> list[ReviewSkillProfile]:
        return ReviewSkillRegistry(self._skills_root).list_all()

    def list_tools(self) -> list[ReviewToolPlugin]:
        return ToolPluginLoader(self._tools_root).list_all()

    def upsert_skill(self, skill_id: str, payload: dict[str, object]) -> ReviewSkillProfile:
        normalized_id = self._normalize_id(skill_id)
        prompt_body = str(payload.get("prompt_body") or "").rstrip() + "\n"
        metadata = {
            "skill_id": normalized_id,
            "name": str(payload.get("name") or normalized_id),
            "description": str(payload.get("description") or ""),
            "bound_experts": list(payload.get("bound_experts") or []),
            "applicable_experts": list(payload.get("applicable_experts") or []),
            "required_tools": list(payload.get("required_tools") or []),
            "required_doc_types": list(payload.get("required_doc_types") or []),
            "activation_hints": list(payload.get("activation_hints") or []),
            "required_context": list(payload.get("required_context") or []),
            "allowed_modes": list(payload.get("allowed_modes") or ["standard", "light"]),
            "output_contract": dict(payload.get("output_contract") or {}),
        }
        skill_dir = self._skills_root / normalized_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        (skill_dir / "SKILL.md").write_text(prompt_body, encoding="utf-8")
        loaded = ReviewSkillRegistry(self._skills_root).get(normalized_id)
        if loaded is None:
            raise RuntimeError(f"failed to load saved skill: {normalized_id}")
        return loaded

    def upsert_tool(self, tool_id: str, payload: dict[str, object]) -> ReviewToolPlugin:
        normalized_id = self._normalize_id(tool_id)
        run_script = str(payload.get("run_script") or "").rstrip() + "\n"
        metadata = {
            "tool_id": normalized_id,
            "name": str(payload.get("name") or normalized_id),
            "description": str(payload.get("description") or ""),
            "runtime": str(payload.get("runtime") or "python"),
            "entry": str(payload.get("entry") or "run.py"),
            "timeout_seconds": int(payload.get("timeout_seconds") or 60),
            "allowed_experts": list(payload.get("allowed_experts") or []),
            "bound_skills": list(payload.get("bound_skills") or []),
            "input_schema": dict(payload.get("input_schema") or {}),
            "output_schema": dict(payload.get("output_schema") or {}),
        }
        tool_dir = self._tools_root / normalized_id
        tool_dir.mkdir(parents=True, exist_ok=True)
        (tool_dir / "tool.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        entry_file = tool_dir / metadata["entry"]
        entry_file.write_text(run_script, encoding="utf-8")
        loaded = ToolPluginLoader(self._tools_root).get(normalized_id)
        if loaded is None:
            raise RuntimeError(f"failed to load saved tool: {normalized_id}")
        return loaded

    def read_tool_script(self, tool_id: str, entry: str = "run.py") -> str:
        normalized_id = self._normalize_id(tool_id)
        script_path = self._tools_root / normalized_id / entry
        if not script_path.exists():
            return ""
        return script_path.read_text(encoding="utf-8")

    def _normalize_id(self, raw: str) -> str:
        candidate = str(raw or "").strip()
        if not candidate or not ID_PATTERN.match(candidate):
            raise ValueError("invalid id, only letters, digits, _ and - are allowed")
        return candidate
