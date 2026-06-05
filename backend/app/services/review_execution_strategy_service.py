from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings


CHANGE_IMPACT_EXPERT_ID = "change_impact_analysis"

SECURITY_SIGNALS = {
    "auth_related_path",
    "security_sensitive_change",
    "token_or_secret_change",
    "sql_injection_candidate",
}
DATA_CONSISTENCY_SIGNALS = {
    "query_boundary_changed",
    "transaction_boundary_changed",
    "repository_changed",
    "batch_boundary_changed",
}
HIGH_RISK_SIGNALS = SECURITY_SIGNALS | DATA_CONSISTENCY_SIGNALS | {
    "exception_handler_changed",
    "lock_or_concurrency_changed",
    "production_config_changed",
}


@dataclass(frozen=True)
class ReviewExecutionDecision:
    diff_profile: dict[str, object]
    risk_profile: dict[str, object]
    execution_strategy: str


class ReviewExecutionStrategyService:
    """Build deterministic review strategy before expensive LLM work."""

    def build_decision(
        self,
        subject: ReviewSubject,
        runtime_settings: RuntimeSettings,
        *,
        tool_observations: list[dict[str, object]] | None = None,
    ) -> ReviewExecutionDecision:
        diff_profile = self.build_diff_profile(subject, runtime_settings)
        risk_profile = self.build_risk_profile(diff_profile, tool_observations or [])
        execution_strategy = self.resolve_execution_strategy(diff_profile, risk_profile, runtime_settings)
        risk_profile = {
            **risk_profile,
            "llm_strategy": execution_strategy,
            "debate_required": execution_strategy == "deep_review",
        }
        return ReviewExecutionDecision(
            diff_profile=diff_profile,
            risk_profile=risk_profile,
            execution_strategy=execution_strategy,
        )

    def build_diff_profile(self, subject: ReviewSubject, runtime_settings: RuntimeSettings) -> dict[str, object]:
        changed_files = [str(item).strip() for item in list(subject.changed_files or []) if str(item).strip()]
        unified_diff = str(subject.unified_diff or "")
        changed_line_count = self._count_changed_lines(unified_diff)
        if changed_line_count <= 0:
            changed_line_count = len(changed_files)
        languages = sorted({self._language_for_path(path) for path in changed_files if self._language_for_path(path)})
        layers = sorted({layer for path in changed_files for layer in self._layers_for_path(path)})
        risk_signals = sorted(self._risk_signals(changed_files, unified_diff))
        small_limit = max(1, int(getattr(runtime_settings, "small_mr_changed_lines", 80) or 80))
        medium_limit = max(small_limit, int(getattr(runtime_settings, "medium_mr_changed_lines", 400) or 400))
        if changed_line_count <= small_limit and len(changed_files) <= 3:
            size_bucket = "small"
        elif changed_line_count <= medium_limit and len(changed_files) <= 12:
            size_bucket = "medium"
        else:
            size_bucket = "large"
        executable_files = [path for path in changed_files if not self._is_docs_only_path(path)]
        return {
            "changed_file_count": len(changed_files),
            "changed_line_count": changed_line_count,
            "languages": languages,
            "layers": layers,
            "risk_signals": risk_signals,
            "size_bucket": size_bucket,
            "requires_deep_review": bool(set(risk_signals) & HIGH_RISK_SIGNALS) or size_bucket == "large",
            "docs_only": bool(changed_files) and not executable_files,
            "changed_files": changed_files,
        }

    def build_risk_profile(
        self,
        diff_profile: dict[str, object],
        tool_observations: list[dict[str, object]],
    ) -> dict[str, object]:
        risk_signals = {str(item) for item in list(diff_profile.get("risk_signals") or []) if str(item).strip()}
        risk_domains: set[str] = set()
        must_review: list[str] = []
        optional: list[str] = []

        def add_required(expert_id: str, domain: str) -> None:
            risk_domains.add(domain)
            if expert_id not in must_review:
                must_review.append(expert_id)

        def add_optional(expert_id: str) -> None:
            if expert_id not in must_review and expert_id not in optional:
                optional.append(expert_id)

        if risk_signals & SECURITY_SIGNALS:
            add_required("security_compliance", "security")
            add_optional("architecture_design")
        if risk_signals & DATA_CONSISTENCY_SIGNALS:
            add_required("database_analysis", "database")
            add_optional("correctness_business")
        if "exception_handler_changed" in risk_signals:
            add_required("correctness_business", "correctness")
            add_optional("maintainability_code_health")
        if "lock_or_concurrency_changed" in risk_signals:
            add_required("performance_reliability", "performance")
            add_optional("correctness_business")
        if "frontend_change" in risk_signals:
            add_required("frontend_accessibility", "frontend")
            add_optional("correctness_business")
        if "production_config_changed" in risk_signals:
            add_required("architecture_design", "architecture")
            add_optional("security_compliance")
        for observation in tool_observations:
            severity = str(observation.get("severity") or "").strip().lower()
            rule_text = " ".join(
                str(observation.get(key) or "")
                for key in ("tool", "rule_id", "message", "category")
            ).lower()
            if severity in {"critical", "high"} or any(token in rule_text for token in ("security", "sql", "xss", "auth", "secret")):
                add_required("security_compliance", "security")
            if any(token in rule_text for token in ("query", "database", "repository", "n+1", "sql")):
                add_required("database_analysis", "database")
        if not must_review and not bool(diff_profile.get("docs_only")):
            add_required("correctness_business", "correctness")

        risk_level = "none"
        if must_review:
            risk_level = "medium"
        if set(risk_signals) & HIGH_RISK_SIGNALS or any(str(item.get("severity") or "").lower() in {"critical", "high"} for item in tool_observations):
            risk_level = "high"
        if bool(diff_profile.get("docs_only")):
            risk_level = "none"
        skip_candidates = {
            "security_compliance",
            "database_analysis",
            "frontend_accessibility",
            "architecture_design",
            "performance_reliability",
            "maintainability_code_health",
            "test_verification",
            "ddd_architecture",
            "mq_analysis",
            "redis_analysis",
        } - set(must_review) - set(optional)
        return {
            "risk_level": risk_level,
            "risk_domains": sorted(risk_domains),
            "must_review_agents": must_review,
            "optional_agents": optional,
            "skip_agents": sorted(skip_candidates),
            "reason": self._risk_reason(risk_signals, must_review, bool(diff_profile.get("docs_only"))),
        }

    def resolve_execution_strategy(
        self,
        diff_profile: dict[str, object],
        risk_profile: dict[str, object],
        runtime_settings: RuntimeSettings,
    ) -> str:
        configured = str(getattr(runtime_settings, "review_execution_strategy", "auto") or "auto").strip()
        if configured in {"no_llm", "light_review", "targeted_review", "deep_review"}:
            if configured != "deep_review" and self._force_deep_review(risk_profile, runtime_settings):
                return "deep_review"
            return configured
        if str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower() == "thorough_review":
            return "deep_review"
        if bool(diff_profile.get("docs_only")) and str(risk_profile.get("risk_level") or "") == "none":
            return "no_llm"
        if self._force_deep_review(risk_profile, runtime_settings) or bool(diff_profile.get("requires_deep_review")):
            return "deep_review"
        size_bucket = str(diff_profile.get("size_bucket") or "medium")
        if size_bucket == "small":
            return "light_review"
        return "targeted_review"

    def apply_to_selection_plan(
        self,
        *,
        subject: ReviewSubject,
        selection_plan: dict[str, object],
        enabled_experts: list[ExpertProfile],
        runtime_settings: RuntimeSettings,
    ) -> dict[str, object]:
        if not bool(getattr(runtime_settings, "enable_agent_routing", True)):
            return selection_plan
        llm_mode = str((selection_plan.get("llm") or {}).get("mode") or "").strip().lower()
        if llm_mode == "user_selected_direct":
            return selection_plan
        metadata = dict(subject.metadata or {})
        diff_profile = dict(metadata.get("diff_profile") or {})
        risk_profile = dict(metadata.get("risk_profile") or {})
        execution_strategy = str(risk_profile.get("llm_strategy") or metadata.get("review_execution_strategy") or "targeted_review")
        if execution_strategy == "deep_review":
            return {
                **selection_plan,
                "diff_profile": diff_profile,
                "risk_profile": risk_profile,
                "execution_strategy": execution_strategy,
            }
        enabled_by_id = {expert.expert_id: expert for expert in enabled_experts if expert.enabled}
        selected_ids = self._dedupe(
            str(item).strip()
            for item in list(selection_plan.get("selected_expert_ids") or [])
            if str(item).strip()
        )
        must_review = [expert_id for expert_id in list(risk_profile.get("must_review_agents") or []) if expert_id in enabled_by_id]
        optional = [expert_id for expert_id in list(risk_profile.get("optional_agents") or []) if expert_id in enabled_by_id]
        keep_ids: list[str] = []
        if subject.subject_type == "mr" and CHANGE_IMPACT_EXPERT_ID in enabled_by_id:
            keep_ids.append(CHANGE_IMPACT_EXPERT_ID)
        keep_ids.extend(must_review)
        max_review_agents = self._max_review_agents(execution_strategy, diff_profile, runtime_settings)
        review_keep = self._dedupe([expert_id for expert_id in keep_ids if expert_id != CHANGE_IMPACT_EXPERT_ID])
        for expert_id in selected_ids + optional:
            if expert_id == CHANGE_IMPACT_EXPERT_ID or expert_id in review_keep:
                continue
            if len(review_keep) >= max_review_agents:
                break
            if expert_id in enabled_by_id:
                review_keep.append(expert_id)
        if execution_strategy == "no_llm":
            review_keep = []
        final_ids = self._dedupe(([CHANGE_IMPACT_EXPERT_ID] if CHANGE_IMPACT_EXPERT_ID in keep_ids else []) + review_keep)
        selected_entries = self._build_selected_entries(selection_plan, final_ids, enabled_by_id, risk_profile, execution_strategy)
        skipped_entries = self._build_skipped_entries(selection_plan, selected_ids, final_ids, enabled_by_id, execution_strategy)
        return {
            **selection_plan,
            "selected_expert_ids": final_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
            "diff_profile": diff_profile,
            "risk_profile": risk_profile,
            "execution_strategy": execution_strategy,
            "routing_optimized": True,
        }

    @staticmethod
    def _count_changed_lines(unified_diff: str) -> int:
        count = 0
        for line in str(unified_diff or "").splitlines():
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+") or line.startswith("-"):
                count += 1
        return count

    @staticmethod
    def _language_for_path(path: str) -> str:
        lower = path.lower()
        suffix_map = {
            ".java": "java",
            ".kt": "kotlin",
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".sql": "sql",
            ".go": "go",
            ".rs": "rust",
        }
        for suffix, language in suffix_map.items():
            if lower.endswith(suffix):
                return language
        return ""

    @staticmethod
    def _layers_for_path(path: str) -> set[str]:
        lower = path.lower()
        layers: set[str] = set()
        if any(token in lower for token in ("controller", "/api/", "rest", "handler")):
            layers.add("controller")
        if any(token in lower for token in ("service", "manager", "workflow", "application")):
            layers.add("service")
        if any(token in lower for token in ("repository", "mapper", "dao", "entity", ".sql")):
            layers.add("repository")
        if any(token in lower for token in ("component", "page", "frontend", ".tsx", ".jsx", ".vue")):
            layers.add("frontend")
        if any(token in lower for token in ("config", "yaml", "yml", "properties", "dockerfile", "ci", "workflow")):
            layers.add("configuration")
        if any(token in lower for token in ("test", "spec")):
            layers.add("test")
        return layers

    @staticmethod
    def _is_docs_only_path(path: str) -> bool:
        lower = path.lower()
        return lower.endswith((".md", ".txt", ".adoc", ".rst")) or lower.startswith(("docs/", "doc/"))

    def _risk_signals(self, changed_files: list[str], unified_diff: str) -> set[str]:
        text = "\n".join([*changed_files, unified_diff]).lower()
        signals: set[str] = set()
        if any(path.lower().endswith((".tsx", ".jsx", ".vue", ".css", ".scss")) or "frontend/" in path.lower() for path in changed_files):
            signals.add("frontend_change")
        if any(token in text for token in ("auth", "authorization", "permission", "preauthorize", "tenant", "role", "jwt", "token")):
            signals.add("auth_related_path")
        if any(token in text for token in ("secret", "password", "credential", "encrypt", "decrypt", "敏感", "密钥")):
            signals.add("token_or_secret_change")
        if any(token in text for token in ("select ", " where ", "repository", "mapper", "dao", "entitymanager", ".sql")):
            signals.add("repository_changed")
        if any(token in text for token in ("limit", "pageable", "pagerequest", "setmaxresults", "offset", "分页", "查询边界")):
            signals.add("query_boundary_changed")
        if any(token in text for token in ("sql injection", "injection", "preparedstatement", "注入")):
            signals.add("sql_injection_candidate")
        if any(token in text for token in ("catch", "exception", "runtimeexception", "throw ", "异常")):
            signals.add("exception_handler_changed")
        if any(token in text for token in ("synchronized", "lock", "redisson", "setnx", "concurrent", "并发", "锁")):
            signals.add("lock_or_concurrency_changed")
        if any(token in text for token in ("@transactional", "transaction", "rollback", "commit", "事务")):
            signals.add("transaction_boundary_changed")
        if any(token in text for token in ("batch", "chunk", "bulk", "saveall", "批量")):
            signals.add("batch_boundary_changed")
        if any(path.lower().endswith((".yml", ".yaml", ".properties", ".env", "dockerfile")) for path in changed_files):
            signals.add("production_config_changed")
        return signals

    @staticmethod
    def _risk_reason(risk_signals: set[str], must_review: list[str], docs_only: bool) -> str:
        if docs_only:
            return "Only documentation or text files changed; deterministic strategy can skip review agents."
        if not risk_signals:
            return "No high-risk deterministic signals were detected; use a compact correctness-focused review."
        return f"Detected risk signals {', '.join(sorted(risk_signals))}; selected required agents {', '.join(must_review) or 'none'}."

    @staticmethod
    def _force_deep_review(risk_profile: dict[str, object], runtime_settings: RuntimeSettings) -> bool:
        domains = {str(item) for item in list(risk_profile.get("risk_domains") or [])}
        if "security" in domains and bool(getattr(runtime_settings, "force_deep_review_for_security", True)):
            return True
        if "security" in domains and bool(getattr(runtime_settings, "force_deep_review_for_auth", True)):
            return True
        if "database" in domains and bool(getattr(runtime_settings, "force_deep_review_for_data_consistency", True)):
            return True
        return False

    @staticmethod
    def _max_review_agents(execution_strategy: str, diff_profile: dict[str, object], runtime_settings: RuntimeSettings) -> int:
        if execution_strategy == "light_review" or str(diff_profile.get("size_bucket") or "") == "small":
            return max(1, int(getattr(runtime_settings, "max_agents_for_small_mr", 2) or 2))
        return max(1, int(getattr(runtime_settings, "max_agents_for_medium_mr", 3) or 3))

    @staticmethod
    def _dedupe(items: Iterable[str]) -> list[str]:
        result: list[str] = []
        for item in items:
            normalized = str(item or "").strip()
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def _build_selected_entries(
        self,
        selection_plan: dict[str, object],
        final_ids: list[str],
        enabled_by_id: dict[str, ExpertProfile],
        risk_profile: dict[str, object],
        execution_strategy: str,
    ) -> list[dict[str, object]]:
        existing = {
            str(item.get("expert_id") or "").strip(): dict(item)
            for item in list(selection_plan.get("selected_experts") or [])
            if isinstance(item, dict) and str(item.get("expert_id") or "").strip()
        }
        required = {str(item) for item in list(risk_profile.get("must_review_agents") or [])}
        entries: list[dict[str, object]] = []
        for expert_id in final_ids:
            expert = enabled_by_id.get(expert_id)
            current = existing.get(expert_id, {})
            source = "risk_required" if expert_id in required else current.get("source") or "strategy_selected"
            reason = str(current.get("reason") or "").strip()
            if not reason:
                reason = "Review execution strategy kept this expert for the current risk profile."
            if expert_id == CHANGE_IMPACT_EXPERT_ID:
                reason = "Every MR keeps change impact analysis for impact reporting."
                source = "system_required"
            entries.append(
                {
                    **current,
                    "expert_id": expert_id,
                    "expert_name": str(current.get("expert_name") or (expert.name_zh if expert else expert_id)),
                    "reason": reason,
                    "confidence": float(current.get("confidence") or 0.82),
                    "source": source,
                    "execution_strategy": execution_strategy,
                }
            )
        return entries

    def _build_skipped_entries(
        self,
        selection_plan: dict[str, object],
        original_selected_ids: list[str],
        final_ids: list[str],
        enabled_by_id: dict[str, ExpertProfile],
        execution_strategy: str,
    ) -> list[dict[str, object]]:
        final_set = set(final_ids)
        skipped = [
            dict(item)
            for item in list(selection_plan.get("skipped_experts") or [])
            if isinstance(item, dict) and str(item.get("expert_id") or "").strip() not in final_set
        ]
        existing_skipped = {str(item.get("expert_id") or "").strip() for item in skipped}
        for expert_id in original_selected_ids:
            if expert_id in final_set or expert_id in existing_skipped:
                continue
            expert = enabled_by_id.get(expert_id)
            skipped.append(
                {
                    "expert_id": expert_id,
                    "expert_name": expert.name_zh if expert else expert_id,
                    "reason": f"Execution strategy {execution_strategy} skipped this expert because the current risk profile did not require it.",
                    "source": "strategy_skipped",
                }
            )
        return skipped
