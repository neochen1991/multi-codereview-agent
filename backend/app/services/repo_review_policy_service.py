from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import yaml


class RepoReviewPolicyService:
    """Load repository-owned AI review policy from `.ai-review.yml`."""

    FILENAMES = (".ai-review.yml", ".ai-review.yaml")

    def load_for_files(self, repo_root: str | Path, changed_files: list[str]) -> dict[str, object]:
        root = Path(str(repo_root or "")).expanduser()
        normalized_files = [self._normalize_path(item) for item in list(changed_files or []) if self._normalize_path(item)]
        policy = self._load_policy(root)
        excluded_patterns = [
            self._normalize_path(item)
            for item in list(policy.get("excluded_paths") or [])
            if self._normalize_path(item)
        ]
        excluded_files = [
            file_path for file_path in normalized_files if self._matches_any(file_path, excluded_patterns)
        ]
        reviewable_files = [file_path for file_path in normalized_files if file_path not in set(excluded_files)]
        matched_rules = self._match_path_rules(policy.get("path_rules"), reviewable_files)
        required_experts = self._dedupe(
            expert_id
            for rule in matched_rules
            for expert_id in list(rule.get("required_experts") or [])
            if str(expert_id).strip()
        )
        return {
            "source": str(policy.get("source") or ""),
            "max_comments_per_review": self._positive_int(policy.get("max_comments_per_review")),
            "excluded_paths": excluded_patterns,
            "excluded_changed_files": excluded_files,
            "reviewable_changed_files": reviewable_files,
            "required_experts": required_experts,
            "path_rules": matched_rules,
        }

    def apply_to_selection_plan(
        self,
        selection_plan: dict[str, object],
        policy: dict[str, object],
        *,
        enabled_expert_ids: list[str],
    ) -> dict[str, object]:
        updated = dict(selection_plan or {})
        enabled = {str(item).strip() for item in list(enabled_expert_ids or []) if str(item).strip()}
        selected_ids = self._dedupe(str(item).strip() for item in list(updated.get("selected_expert_ids") or []))
        candidate_ids = self._dedupe(str(item).strip() for item in list(updated.get("candidate_expert_ids") or []))
        selected_experts = [
            dict(item) for item in list(updated.get("selected_experts") or []) if isinstance(item, dict)
        ]
        added: list[str] = []
        missing: list[str] = []
        for expert_id in self._dedupe(str(item).strip() for item in list(policy.get("required_experts") or [])):
            if expert_id not in enabled:
                missing.append(expert_id)
                continue
            if expert_id not in selected_ids:
                selected_ids.append(expert_id)
                added.append(expert_id)
                selected_experts.append(
                    {
                        "expert_id": expert_id,
                        "source": "repo_policy_required",
                    }
                )
            if expert_id not in candidate_ids:
                candidate_ids.append(expert_id)
        updated["selected_expert_ids"] = selected_ids
        updated["candidate_expert_ids"] = candidate_ids
        updated["selected_experts"] = selected_experts
        updated["review_policy"] = {
            "source": str(policy.get("source") or ""),
            "required_experts": list(policy.get("required_experts") or []),
            "added_required_experts": added,
            "missing_required_experts": missing,
            "excluded_changed_files": list(policy.get("excluded_changed_files") or []),
            "max_comments_per_review": int(policy.get("max_comments_per_review") or 0),
            "matched_path_rule_count": len(list(policy.get("path_rules") or [])),
        }
        return updated

    def _load_policy(self, root: Path) -> dict[str, Any]:
        if not root.exists() or not root.is_dir():
            return {}
        config_path = next((root / name for name in self.FILENAMES if (root / name).exists()), None)
        if config_path is None:
            return {}
        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        if not isinstance(raw, dict):
            return {}
        raw = dict(raw)
        raw["source"] = str(config_path.relative_to(root))
        return raw

    def _match_path_rules(self, raw_rules: object, changed_files: list[str]) -> list[dict[str, object]]:
        rules = self._normalize_path_rules(raw_rules)
        matched: list[dict[str, object]] = []
        for rule in rules:
            patterns = [
                self._normalize_path(item)
                for item in list(rule.get("paths") or [])
                if self._normalize_path(item)
            ]
            matched_files = [
                file_path for file_path in changed_files if not patterns or self._matches_any(file_path, patterns)
            ]
            if not matched_files:
                continue
            matched.append(
                {
                    "title": str(rule.get("title") or rule.get("name") or "path_rule").strip(),
                    "paths": patterns,
                    "matched_files": matched_files,
                    "required_experts": self._dedupe(
                        str(item).strip()
                        for item in list(rule.get("required_experts") or rule.get("experts") or [])
                        if str(item).strip()
                    ),
                    "comment_level": str(rule.get("comment_level") or "").strip(),
                    "max_comments": self._positive_int(rule.get("max_comments")),
                    "instructions": str(rule.get("instructions") or rule.get("instruction") or "").strip(),
                }
            )
        return matched

    def _normalize_path_rules(self, raw_rules: object) -> list[dict[str, object]]:
        if isinstance(raw_rules, dict):
            return [
                {
                    **(dict(value) if isinstance(value, dict) else {}),
                    "title": str((value or {}).get("title") or pattern).strip() if isinstance(value, dict) else str(pattern),
                    "paths": [str(pattern)],
                }
                for pattern, value in raw_rules.items()
            ]
        if isinstance(raw_rules, list):
            return [dict(item) for item in raw_rules if isinstance(item, dict)]
        return []

    def _matches_any(self, file_path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatch(file_path, pattern) for pattern in patterns)

    def _normalize_path(self, value: object) -> str:
        return str(value or "").strip().replace("\\", "/")

    def _dedupe(self, values) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _positive_int(self, value: object) -> int:
        try:
            number = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, number)
