from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from app.db.sqlite import SqliteDatabase
from app.domain.models.expert_profile import ExpertProfile

logger = logging.getLogger(__name__)


class FileExpertRepository:
    """负责加载和保存专家配置、提示词及规范文档。"""

    def __init__(self, root: Path) -> None:
        """初始化用户自定义专家目录。"""

        self.root = Path(root)
        self._db = SqliteDatabase(self.root.parent / "app.db")
        self._db.initialize()

    def list(self) -> list[ExpertProfile]:
        """合并内置专家与用户专家配置，并返回完整专家列表。"""

        items: list[ExpertProfile] = []
        packaged_root = Path(__file__).resolve().parents[1] / "builtin_experts"
        extension_skill_bindings = self._load_extension_skill_bindings(
            Path(__file__).resolve().parents[3] / "extensions" / "skills"
        )
        builtin_payloads = self._load_payloads(packaged_root, mark_custom=False)
        user_payloads = self._load_user_payloads_from_sqlite()

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
            manual_skill_bindings = self._merge_list_field(payload.get("skill_bindings", []), [])
            extension_bound_skills = extension_skill_bindings.get(expert_id, [])
            payload["skill_bindings_manual"] = manual_skill_bindings
            payload["skill_bindings_extension"] = extension_bound_skills
            payload["skill_bindings"] = self._merge_list_field(
                manual_skill_bindings,
                extension_bound_skills,
            )
            items.append(ExpertProfile.model_validate(payload))
        logger.info(
            "loaded %s experts from roots=%s",
            len(items),
            [str(root) for root in {self.root, packaged_root}],
        )
        return items

    def save(self, expert: ExpertProfile) -> ExpertProfile:
        """把专家可变配置保存到 SQLite（不改内置专家种子文件）。"""

        payload = expert.model_dump(mode="json")
        now = datetime.now(UTC).isoformat()
        with self._db.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO experts (
                    expert_id,
                    name,
                    payload_json,
                    updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    expert.expert_id,
                    expert.name_zh or expert.name_en or expert.expert_id,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            connection.commit()
        return expert

    def _load_extension_skill_bindings(self, root: Path) -> dict[str, list[str]]:
        """从 extensions/skills 中加载 skill -> expert 绑定关系。

        这样后续新增或调整 skill 绑定只需要修改 extension 目录，
        不必再去改内置专家源码目录。
        """
        bindings: dict[str, list[str]] = {}
        if not root.exists():
            return bindings
        for metadata_path in sorted(root.glob("*/metadata.json")):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("invalid skill metadata: %s", metadata_path)
                continue
            skill_id = str(metadata.get("skill_id") or "").strip()
            if not skill_id:
                continue
            for expert_id in [str(item).strip() for item in list(metadata.get("bound_experts") or []) if str(item).strip()]:
                bindings.setdefault(expert_id, [])
                if skill_id not in bindings[expert_id]:
                    bindings[expert_id].append(skill_id)
        return bindings

    def _merge_list_field(self, current: list[str] | object, extra: list[str]) -> list[str]:
        merged: list[str] = []
        for item in list(current or []) if isinstance(current, list) else []:
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        for item in extra:
            text = str(item).strip()
            if text and text not in merged:
                merged.append(text)
        return merged

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

    def _load_user_payloads_from_sqlite(self) -> dict[str, dict]:
        """读取用户专家覆盖配置（SQLite 持久化）。"""

        payloads: dict[str, dict] = {}
        with self._db.connect() as connection:
            rows = connection.execute(
                """
                SELECT expert_id, payload_json
                FROM experts
                ORDER BY updated_at ASC
                """
            ).fetchall()
        for row in rows:
            expert_id = str(row["expert_id"] or "").strip()
            if not expert_id:
                continue
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                logger.warning("invalid expert payload in sqlite for %s", expert_id)
                continue
            payload["expert_id"] = expert_id
            payload["custom"] = bool(payload.get("custom", True))
            payloads[expert_id] = payload
        return payloads
