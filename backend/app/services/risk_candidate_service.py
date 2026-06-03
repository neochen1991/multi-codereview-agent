from __future__ import annotations

import hashlib
from typing import Any, Iterable

from app.services.change_understanding_service import RISK_DOMAIN_EXPERTS


def jsonish(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key}={jsonish(item)}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(jsonish(item) for item in value)
    return str(value or "")


RISK_SIGNAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "security_guard_removed": ("security", "business"),
    "sql_injection_risk": ("security",),
    "sensitive_data_exposure": ("security",),
    "query_authorization_scope_broadened": ("security", "business", "database"),
    "query_bound_removed": ("database", "performance"),
    "unbounded_query_risk": ("database", "performance"),
    "loop_call_amplification": ("performance", "database"),
    "bulk_processing_risk": ("performance", "database"),
    "comment_contract_unimplemented": ("business", "test"),
    "exception_swallowed": ("business", "maintainability"),
    "exception_semantics_weakened": ("business", "test"),
    "lock_scope_risk": ("concurrency", "performance"),
    "lock_guard_removed": ("concurrency", "performance"),
    "transactional_side_effect": ("transaction", "performance"),
    "event_ordering_risk": ("ddd", "business", "mq"),
    "factory_bypass": ("ddd", "business"),
    "cross_layer_dependency": ("ddd", "maintainability"),
    "mq_delivery_risk": ("mq", "test"),
    "cache_consistency_risk": ("cache", "test"),
    "idempotency_guard_removed": ("mq", "business", "concurrency"),
}


TOOL_RULE_DOMAIN_HINTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("sql-injection", "injection", "xss", "csrf", "auth", "permission", "secret", "password", "token"), ("security",)),
    (("archunit", "layer", "architecture", "dependency", "ddd", "domain"), ("ddd",)),
    (("npe", "null", "resource", "leak", "concurrency", "thread"), ("maintainability", "performance")),
    (("complexity", "empty-catch", "emptycatch", "catch", "cyclomatic"), ("maintainability", "business")),
    (("coverage", "jacoco", "mutation", "pit"), ("test",)),
)


class RiskCandidateService:
    """Build expert-facing risk candidates from deterministic observations.

    Risk candidates are not issues. They are recall material that tells expert
    agents where to look and which evidence signals are available.
    """

    def build(
        self,
        *,
        change_understanding: dict[str, Any] | None = None,
        change_slices: Iterable[dict[str, Any]] | None = None,
        tool_observations: Iterable[dict[str, Any]] | None = None,
        code_graph_context: dict[str, Any] | None = None,
        feedback_quality_profiles: dict[str, Any] | None = None,
        existing_candidates: Iterable[dict[str, Any]] | None = None,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for item in list(existing_candidates or []):
            if isinstance(item, dict):
                candidates.append(dict(item))
        for item in self._from_change_understanding(dict(change_understanding or {})):
            candidates.append(item)
        for item in self._from_change_slices(list(change_slices or [])):
            candidates.append(item)
        for item in self._from_tool_observations(list(tool_observations or [])):
            candidates.append(item)
        for item in self._from_code_graph_context(dict(code_graph_context or {})):
            candidates.append(item)
        for item in self._from_feedback_quality_profiles(dict(feedback_quality_profiles or {})):
            candidates.append(item)
        return self._dedupe_candidates(candidates)[:80]

    def _from_change_understanding(self, facts: dict[str, Any]) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for file_facts in list(facts.get("files") or []):
            if not isinstance(file_facts, dict):
                continue
            file_path = str(file_facts.get("path") or "").strip()
            if not file_path:
                continue
            method_names = [
                str(value).strip()
                for value in list(file_facts.get("changed_methods") or [])
                if str(value).strip()
            ]
            role = str(file_facts.get("file_role") or "").strip()
            for domain in self._text_list(file_facts.get("risk_domains")):
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="change_understanding",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path=file_path,
                            method_name=method_names[0] if method_names else "",
                            line_start=0,
                            code_anchor="",
                            message=f"{role or 'code'} 文件命中 {domain} 风险域，需要专家结合 diff 与上下文复核。",
                            evidence=[
                                f"file_role={role or 'unknown'}",
                                f"changed_methods={', '.join(method_names[:4]) or '未识别'}",
                            ],
                            raw={"file_facts": file_facts},
                        )
                    )
        return candidates

    def _from_code_graph_context(self, code_graph_context: dict[str, Any]) -> list[dict[str, object]]:
        if not code_graph_context:
            return []
        minimal_context = dict(code_graph_context.get("code_graph_minimal_context") or {})
        impact = dict(code_graph_context.get("code_graph_impact_analysis") or {})
        related_contexts = [
            dict(item)
            for item in list(code_graph_context.get("code_graph_related_contexts") or [])
            if isinstance(item, dict)
        ]
        candidates: list[dict[str, object]] = []
        for priority in list(minimal_context.get("review_priorities") or impact.get("review_priorities") or []):
            if not isinstance(priority, dict):
                continue
            message = str(priority.get("reason") or priority.get("summary") or priority.get("title") or "").strip()
            file_path = str(priority.get("file_path") or priority.get("path") or "").strip()
            domains = self._domains_for_graph_text(message or jsonish(priority))
            for domain in domains:
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="code_graph_priority",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path=file_path,
                            method_name=str(priority.get("qualified_name") or priority.get("method_name") or "").strip(),
                            line_start=int(priority.get("line_start") or priority.get("line_number") or 0),
                            code_anchor=str(priority.get("symbol") or priority.get("qualified_name") or "").strip(),
                            message=message or "代码图谱识别到该变更点处于高优先级影响链路，需要专家结合调用上下文复核。",
                            evidence=["code_graph:review_priority"],
                            raw={"code_graph_priority": priority},
                        )
                    )
        for flow in list(minimal_context.get("affected_flows") or impact.get("affected_flows") or []):
            flow_text = jsonish(flow)
            domains = self._domains_for_graph_text(flow_text)
            for domain in domains:
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="code_graph_flow",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path=str(flow.get("file_path") or flow.get("path") or "").strip() if isinstance(flow, dict) else "",
                            method_name=str(flow.get("qualified_name") or flow.get("name") or "").strip() if isinstance(flow, dict) else "",
                            line_start=int(flow.get("line_start") or flow.get("line_number") or 0) if isinstance(flow, dict) else 0,
                            code_anchor=str(flow.get("path_summary") or flow.get("path") or "").strip() if isinstance(flow, dict) else "",
                            message="代码图谱识别到受影响调用链，需评估业务、事务、性能或测试影响。",
                            evidence=["code_graph:affected_flow"],
                            raw={"code_graph_flow": flow if isinstance(flow, dict) else {"value": flow_text}},
                        )
                    )
        for test_gap in list(impact.get("test_gaps") or minimal_context.get("test_gaps") or []):
            if not isinstance(test_gap, dict):
                continue
            file_path = str(test_gap.get("file_path") or test_gap.get("path") or "").strip()
            for expert_id in RISK_DOMAIN_EXPERTS.get("test", ()):
                candidates.append(
                    self._candidate(
                        source="code_graph_test_gap",
                        risk_domain="test",
                        expert_id=expert_id,
                        file_path=file_path,
                        method_name=str(test_gap.get("qualified_name") or test_gap.get("name") or "").strip(),
                        line_start=int(test_gap.get("line_start") or test_gap.get("line_number") or 0),
                        code_anchor=str(test_gap.get("qualified_name") or test_gap.get("name") or "").strip(),
                        message="代码图谱识别到高风险变更缺少对应测试覆盖，需要测试专家复核。",
                        evidence=["code_graph:test_gap"],
                        raw={"code_graph_test_gap": test_gap},
                    )
                )
        for related in related_contexts[:8]:
            text = jsonish(related)
            domains = self._domains_for_graph_text(text)
            file_path = str(related.get("path") or related.get("file_path") or "").strip()
            for domain in domains:
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="code_graph_related_context",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path=file_path,
                            method_name=str(related.get("target_qualified_name") or related.get("source_qualified_name") or "").strip(),
                            line_start=int(related.get("line_number") or related.get("line_start") or 0),
                            code_anchor=str(related.get("relationship") or "").strip(),
                            message="代码图谱补充了关联源码上下文，需要专家结合上下游关系复核。",
                            evidence=["code_graph:related_context"],
                            raw={"code_graph_related_context": related},
                        )
                    )
        return candidates

    def _from_change_slices(self, change_slices: list[dict[str, Any]]) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for change_slice in change_slices:
            if not isinstance(change_slice, dict):
                continue
            file_path = str(change_slice.get("file_path") or "").strip()
            if not file_path:
                continue
            line_start = int(change_slice.get("line_start") or 0)
            code_anchor = self._code_anchor(str(change_slice.get("excerpt") or ""))
            for signal in self._text_list(change_slice.get("risk_signals")):
                for domain in RISK_SIGNAL_DOMAINS.get(signal, ()):
                    for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                        candidates.append(
                            self._candidate(
                                source="hunk_signal",
                                risk_domain=domain,
                                expert_id=expert_id,
                                file_path=file_path,
                                method_name="",
                                line_start=line_start,
                                code_anchor=code_anchor,
                                message=f"hunk 命中 {signal} 信号，需要 {expert_id} 判断是否构成真实问题。",
                                evidence=[str(change_slice.get("summary") or signal)],
                                raw={"change_slice": change_slice, "risk_signal": signal},
                            )
                        )
        return candidates

    def _from_tool_observations(self, tool_observations: list[dict[str, Any]]) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for observation in tool_observations:
            if not isinstance(observation, dict):
                continue
            file_path = str(observation.get("file_path") or observation.get("path") or "").strip()
            if not file_path:
                continue
            domains = self._domains_for_tool_observation(observation)
            line_start = int(observation.get("line_start") or observation.get("line") or 0)
            message = str(observation.get("message") or observation.get("summary") or "").strip()
            rule_id = str(observation.get("rule_id") or observation.get("check_id") or "").strip()
            for domain in domains:
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="tool_observation",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path=file_path,
                            method_name=str(observation.get("method_name") or "").strip(),
                            line_start=line_start,
                            code_anchor=str(observation.get("code_anchor") or "").strip(),
                            message=message or f"静态工具命中 {rule_id or domain} 线索，需要专家复核。",
                            evidence=[f"{observation.get('tool') or 'tool'}:{rule_id or 'rule'}"],
                            raw={"tool_observation": observation},
                        )
                    )
        return candidates

    def _from_feedback_quality_profiles(self, profiles: dict[str, Any]) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        expert_profiles = {
            str(expert_id or "").strip(): dict(profile)
            for expert_id, profile in dict(profiles.get("experts") or {}).items()
            if str(expert_id or "").strip() and isinstance(profile, dict)
        }
        issue_type_profiles = {
            str(issue_type or "").strip().lower(): dict(profile)
            for issue_type, profile in dict(profiles.get("issue_types") or {}).items()
            if str(issue_type or "").strip() and isinstance(profile, dict)
        }
        for expert_id, profile in expert_profiles.items():
            if not self._is_high_value_feedback_profile(profile):
                continue
            candidates.append(
                self._candidate(
                    source="feedback_profile",
                    risk_domain=self._domain_for_expert(expert_id),
                    expert_id=expert_id,
                    file_path="",
                    method_name="",
                    line_start=0,
                    code_anchor="",
                    message=(
                        f"历史人工反馈显示 {expert_id} 的同类发现接受率较高；"
                        "本轮如命中对应风险域，应优先交由该专家复核。"
                    ),
                    evidence=[self._feedback_evidence(profile)],
                    raw={"feedback_profile": {"expert_id": expert_id, **profile}},
                )
            )
        for issue_type, profile in issue_type_profiles.items():
            if not self._is_high_value_feedback_profile(profile):
                continue
            domains = self._domains_for_issue_type(issue_type)
            for domain in domains:
                for expert_id in RISK_DOMAIN_EXPERTS.get(domain, ()):
                    candidates.append(
                        self._candidate(
                            source="feedback_profile",
                            risk_domain=domain,
                            expert_id=expert_id,
                            file_path="",
                            method_name="",
                            line_start=0,
                            code_anchor=issue_type,
                            message=(
                                f"历史人工反馈显示 {issue_type} 类型接受率较高；"
                                "本轮若 diff 出现相同根因，应由专家确认是否复现。"
                            ),
                            evidence=[self._feedback_evidence(profile)],
                            raw={"feedback_profile": {"issue_type": issue_type, **profile}},
                        )
                    )
        return candidates

    def _domains_for_tool_observation(self, observation: dict[str, Any]) -> list[str]:
        explicit = self._text_list(observation.get("risk_domains") or observation.get("risk_domain"))
        if explicit:
            return explicit
        blob = " ".join(
            str(observation.get(key) or "")
            for key in ("tool", "rule_id", "check_id", "message", "severity", "cwe", "why_it_matters")
        ).lower()
        domains: list[str] = []
        for tokens, token_domains in TOOL_RULE_DOMAIN_HINTS:
            if any(token in blob for token in tokens):
                domains.extend(token_domains)
        return self._dedupe(domains or ["maintainability"])

    def _domains_for_graph_text(self, text: str) -> list[str]:
        lowered = str(text or "").lower()
        domains: list[str] = []
        if any(token in lowered for token in ("controller", "request", "auth", "tenant", "user", "permission", "token", "鉴权", "权限")):
            domains.append("security")
        if any(token in lowered for token in ("order", "payment", "refund", "inventory", "amount", "status", "state", "业务", "状态")):
            domains.append("business")
        if any(token in lowered for token in ("domain", "aggregate", "factory", "domainevent", "applicationservice", "ddd", "领域", "聚合")):
            domains.append("ddd")
        if any(token in lowered for token in ("repository", "mapper", "dao", "sql", "database", "transaction", "事务", "数据库")):
            domains.append("database")
        if any(token in lowered for token in ("transaction", "rollback", "commit", "event", "publish", "事务")):
            domains.append("transaction")
        if any(token in lowered for token in ("loop", "batch", "bulk", "latency", "timeout", "performance", "调用链", "性能")):
            domains.append("performance")
        if any(token in lowered for token in ("mq", "kafka", "rocketmq", "rabbit", "consumer", "producer", "ack", "message", "消息")):
            domains.append("mq")
        if any(token in lowered for token in ("redis", "cache", "ttl", "缓存")):
            domains.append("cache")
        if any(token in lowered for token in ("test", "coverage", "jacoco", "测试", "回归")):
            domains.append("test")
        return self._dedupe(domains or ["business", "test"])

    def _domains_for_issue_type(self, issue_type: str) -> list[str]:
        normalized = str(issue_type or "").strip().lower()
        if normalized in RISK_SIGNAL_DOMAINS:
            return list(RISK_SIGNAL_DOMAINS[normalized])
        return self._domains_for_tool_observation({"rule_id": normalized, "message": normalized})

    def _domain_for_expert(self, expert_id: str) -> str:
        for domain, expert_ids in RISK_DOMAIN_EXPERTS.items():
            if expert_id in expert_ids:
                return domain
        return "business"

    def _is_high_value_feedback_profile(self, profile: dict[str, Any]) -> bool:
        sample_count = int(profile.get("sample_count") or 0)
        accept_rate = float(profile.get("accept_rate") or 0.0)
        false_positive_rate = float(profile.get("false_positive_rate") or 0.0)
        confidence_bonus = float(profile.get("confidence_bonus") or 0.0)
        return sample_count >= 3 and false_positive_rate < 0.25 and (accept_rate >= 0.65 or confidence_bonus > 0)

    def _feedback_evidence(self, profile: dict[str, Any]) -> str:
        return (
            f"sample_count={int(profile.get('sample_count') or 0)}, "
            f"accept_rate={float(profile.get('accept_rate') or 0.0):.2f}, "
            f"false_positive_rate={float(profile.get('false_positive_rate') or 0.0):.2f}"
        )

    def _candidate(
        self,
        *,
        source: str,
        risk_domain: str,
        expert_id: str,
        file_path: str,
        method_name: str,
        line_start: int,
        code_anchor: str,
        message: str,
        evidence: list[str],
        raw: dict[str, object],
    ) -> dict[str, object]:
        identity = "|".join([source, risk_domain, expert_id, file_path, method_name, str(line_start), code_anchor, message])
        return {
            "candidate_id": "risk_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12],
            "source": source,
            "risk_domain": risk_domain,
            "suggested_expert_id": expert_id,
            "file_path": file_path,
            "method_name": method_name,
            "line_start": line_start,
            "code_anchor": code_anchor,
            "message": message,
            "evidence": [item for item in evidence if str(item).strip()][:5],
            "raw": raw,
        }

    def _code_anchor(self, excerpt: str) -> str:
        for raw_line in str(excerpt or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("@@", "---", "+++")):
                continue
            if "| +" in line:
                value = line.split("| +", 1)[1].strip()
            elif line.startswith("+ |"):
                parts = line.split("|", 2)
                value = parts[2].strip() if len(parts) >= 3 else ""
            elif line.startswith("+") and not line.startswith("+++"):
                value = line[1:].strip()
            else:
                continue
            if value and not value.startswith(("//", "/*", "*")):
                return value[:180]
        return ""

    def _dedupe_candidates(self, candidates: list[dict[str, object]]) -> list[dict[str, object]]:
        seen: set[tuple[str, str, str, str, str, int, str]] = set()
        deduped: list[dict[str, object]] = []
        for candidate in candidates:
            key = (
                str(candidate.get("source") or ""),
                str(candidate.get("risk_domain") or ""),
                str(candidate.get("suggested_expert_id") or ""),
                str(candidate.get("file_path") or ""),
                str(candidate.get("method_name") or ""),
                int(candidate.get("line_start") or 0),
                str(candidate.get("code_anchor") or "")[:120],
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _text_list(self, value: object) -> list[str]:
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, Iterable):
            values = [str(item) for item in value]
        else:
            values = []
        return self._dedupe(values)

    def _dedupe(self, values: Iterable[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped
