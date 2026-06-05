from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings


class ReviewCacheRepository:
    """Small persistent cache for deterministic review artifacts."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = Path(cache_path)
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, object] | None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return None
        with self._lock:
            payload = self._load()
            entry = payload.get(normalized_key)
            return dict(entry) if isinstance(entry, dict) else None

    def set(self, key: str, value: dict[str, object]) -> None:
        normalized_key = str(key or "").strip()
        if not normalized_key:
            return
        entry = dict(value)
        entry["cached_at"] = datetime.now(UTC).isoformat()
        with self._lock:
            payload = self._load()
            payload[normalized_key] = entry
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def build_execution_strategy_key(
        self,
        *,
        subject: ReviewSubject,
        runtime_settings: RuntimeSettings,
        enabled_experts: list[ExpertProfile] | None = None,
    ) -> str:
        settings_payload = runtime_settings.model_dump(mode="json")
        strategy_settings = {
            key: settings_payload.get(key)
            for key in (
                "review_execution_strategy",
                "review_quality_mode",
                "small_mr_changed_lines",
                "medium_mr_changed_lines",
                "max_agents_for_small_mr",
                "max_agents_for_medium_mr",
                "enable_static_tool_prefilter",
                "enable_agent_routing",
                "force_deep_review_for_security",
                "force_deep_review_for_auth",
                "force_deep_review_for_data_consistency",
            )
        }
        payload = {
            "scope": "execution_strategy",
            "repo_id": subject.repo_id,
            "project_id": subject.project_id,
            "source_ref": subject.source_ref,
            "target_ref": subject.target_ref,
            "changed_files": list(subject.changed_files or []),
            "unified_diff_hash": hashlib.sha256(str(subject.unified_diff or "").encode("utf-8")).hexdigest(),
            "tool_observation_hash": hashlib.sha256(
                json.dumps(
                    list((subject.metadata or {}).get("tool_observations") or []),
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "strategy_settings": strategy_settings,
            "expert_profile_hash": hashlib.sha256(
                json.dumps(
                    [
                        {
                            "expert_id": expert.expert_id,
                            "enabled": expert.enabled,
                            "role": expert.role,
                            "focus_areas": list(expert.focus_areas or []),
                            "activation_hints": list(expert.activation_hints or []),
                            "required_checks": list(expert.required_checks or []),
                            "system_prompt": expert.system_prompt,
                            "review_spec": expert.review_spec,
                        }
                        for expert in sorted(enabled_experts or [], key=lambda item: item.expert_id)
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _load(self) -> dict[str, object]:
        if not self.cache_path.exists():
            return {}
        try:
            parsed = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
