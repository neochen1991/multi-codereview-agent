from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.domain.models.expert_profile import ExpertProfile
    from app.domain.models.finding import ReviewFinding
    from app.domain.models.issue import DebateIssue
    from app.domain.models.review import ReviewSubject, ReviewTask

logger = logging.getLogger(__name__)


class ReviewRunnerCommonMixin:
    """Small shared helpers for ReviewRunner.

    Keep these methods side-effect-light so the core runner can focus on orchestration.
    """

    def _is_meaningful_context_file(self, path_text: str) -> bool:
        normalized = str(path_text or "").strip().replace("\\", "/")
        if not normalized:
            return False
        banned_parts = [".git/", "node_modules/", "dist/", "build/", ".next/", ".turbo/", "__pycache__/"]
        if any(part in normalized for part in banned_parts):
            return False
        banned_suffixes = (".lock", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2")
        return not normalized.endswith(banned_suffixes)

    def _should_skip_finding(self, expert_id: str, finding: ReviewFinding) -> bool:
        if expert_id == "change_impact_analysis":
            logger.info(
                "suppressing impact-analysis finding review_id=%s file=%s line=%s title=%s",
                finding.review_id,
                finding.file_path,
                finding.line_start,
                finding.title,
            )
            return True
        if self._looks_like_uncertain_finding(finding):
            logger.info(
                "suppressing uncertain finding review_id=%s expert_id=%s file=%s line=%s title=%s",
                finding.review_id,
                expert_id,
                finding.file_path,
                finding.line_start,
                finding.title,
            )
            return True
        if self._looks_like_non_issue_finding(finding):
            logger.info(
                "suppressing non-issue finding review_id=%s expert_id=%s file=%s line=%s title=%s",
                finding.review_id,
                expert_id,
                finding.file_path,
                finding.line_start,
                finding.title,
            )
            return True
        if expert_id != "performance_reliability":
            return False
        text_blob = "\n".join(
            [
                finding.title,
                finding.summary,
                finding.rule_based_reasoning,
                *finding.evidence,
                *finding.cross_file_evidence,
            ]
        ).lower()
        perf_tokens = {
            "超时",
            "重试",
            "限流",
            "吞吐",
            "循环",
            "for",
            "foreach",
            "n+1",
            "逐条",
            "查库",
            "数据库往返",
            "远程调用",
            "批量",
            "query",
            "repository",
            "jdbc",
            "sql",
            "锁",
            "热点",
            "退化",
            "并发",
            "序列化",
            "响应体",
            "缓存",
            "内存",
            "cpu",
            "latency",
            "throughput",
            "timeout",
            "retry",
            "cache",
            "performance",
        }
        has_perf_signal = any(token in text_blob for token in perf_tokens)
        has_loop_signal = any(
            token in text_blob
            for token in {"循环调用放大", "循环内调用", "loop_call_amplification", "for (", "forEach", ".forEach"}
        )
        has_repo_context = len(finding.context_files) >= 2
        if (
            finding.finding_type == "risk_hypothesis"
            and (not has_perf_signal and not has_repo_context and not has_loop_signal)
            and not finding.matched_rules
            and not finding.violated_guidelines
        ):
            logger.info(
                "suppressing weak performance finding review_id=%s file=%s line=%s has_perf_signal=%s has_repo_context=%s title=%s",
                finding.review_id,
                finding.file_path,
                finding.line_start,
                has_perf_signal,
                has_repo_context,
                finding.title,
            )
            return True
        return False

    def _looks_like_non_issue_finding(self, finding: ReviewFinding) -> bool:
        text_blob = "\n".join(
            [
                finding.title,
                finding.summary,
                finding.rule_based_reasoning,
                *finding.evidence,
                *finding.cross_file_evidence,
            ]
        ).lower()
        no_issue_phrases = {
            "无风险",
            "没有风险",
            "无架构风险",
            "无可维护性风险",
            "无需处理",
            "保持现状",
            "仅涉及格式化",
            "仅为格式化",
            "仅是格式化",
            "代码格式化",
            "缩进调整",
            "空格调整",
            "换行调整",
        }
        formatting_tokens = {"formatting", "format only", "whitespace", "indent", "reformat"}
        has_no_issue_phrase = any(token in text_blob for token in no_issue_phrases | formatting_tokens)
        if not has_no_issue_phrase:
            return False
        if finding.severity not in {"low", "medium"}:
            return False
        return True

    def _looks_like_uncertain_finding(self, finding: ReviewFinding) -> bool:
        # risk_hypothesis/verification_needed 仅表示“待核验风险”，不等价于“无效结论”。
        # 只有在“缺证据 + 明确不确定措辞”时才抑制，避免误杀有效问题。
        user_confirmation_tokens = {
            "请用户",
            "用户确认",
            "用户核查",
            "用户查看",
            "人工确认",
            "人工核查",
            "人工查看",
            "自行确认",
            "自行核实",
            "need user",
            "ask user",
        }
        uncertain_tokens = {
            "需要核对",
            "请核对",
            "建议核对",
            "需核对",
            "建议查看",
            "需要查看",
            "请查看",
            "自行确认",
            "自行核实",
            "待确认",
            "无法确认",
            "证据不足",
            "verify",
            "double-check",
            "need to check",
            "need check",
            "uncertain",
        }
        text_blob = "\n".join(
            [
                finding.title,
                finding.summary,
                finding.rule_based_reasoning,
                finding.verification_plan,
                finding.remediation_suggestion,
                *finding.assumptions,
                *finding.remediation_steps,
            ]
        ).lower()
        has_user_confirmation_phrase = any(token in text_blob for token in user_confirmation_tokens)
        if has_user_confirmation_phrase:
            return True
        has_uncertain_phrase = any(token in text_blob for token in uncertain_tokens)
        has_evidence = bool(
            finding.evidence
            or finding.cross_file_evidence
            or finding.matched_rules
            or finding.violated_guidelines
            or finding.context_files
        )
        if bool(finding.verification_needed) and not has_evidence:
            return True
        return has_uncertain_phrase and not has_evidence

    def _build_debate_prompt(
        self,
        subject: ReviewSubject,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
        bound_documents: list[object],
    ) -> str:
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        bound_documents_summary = self._build_bound_documents_summary(bound_documents)
        return (
            f"议题标题: {issue.title}\n"
            f"议题摘要: {issue.summary}\n"
            f"当前专家: {expert.expert_id} / {expert.name_zh}\n"
            f"你要回应的对象: {reply_to_expert_id}\n"
            f"目标代码: {file_path}:{line_start}\n"
            f"职责边界: {' / '.join(expert.focus_areas) or expert.role}\n"
            f"禁止越界: {' / '.join(expert.out_of_scope) or '不要替其他专家下最终结论'}\n"
            f"已绑定参考文档:\n{bound_documents_summary}\n"
            f"代码片段:\n{code_excerpt}\n"
            f"请输出一段中文聊天式辩论消息，必须围绕 {file_path}:{line_start} 这段真实变更展开，"
            f"先点名回应对象，再说明你同意或反驳什么，指出具体代码证据，并说明还缺什么验证。"
        )

    def _build_debate_fallback(
        self,
        issue: DebateIssue,
        expert: ExpertProfile,
        reply_to_expert_id: str,
        file_path: str,
        line_start: int,
    ) -> str:
        return (
            f"回应 @{reply_to_expert_id}：我继续看了 {file_path}:{line_start}。"
            f" 对于“{issue.title}”这个议题，我认为争议点不只是 {issue.summary}，"
            f" 还要确认这里的边界条件和回退路径是否被覆盖，否则这个风险还不能直接关闭。"
        )

    def _extract_structured_field(self, text: str, label: str) -> str:
        marker = f"{label}："
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith(marker):
                return line.split(marker, 1)[1].strip()
        return ""

    def _parse_json_payload(self, text: str) -> object:
        content = text.strip()
        if not content:
            return None
        candidates = [content]
        if "```json" in content:
            fragment = content.split("```json", 1)[1].split("```", 1)[0].strip()
            if fragment:
                candidates.insert(0, fragment)
        if "```" in content and len(candidates) == 1:
            fragment = content.split("```", 1)[1].split("```", 1)[0].strip()
            if fragment:
                candidates.insert(0, fragment)
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except Exception:
                continue
        for open_char, close_char in (("{", "}"), ("[", "]")):
            start = content.find(open_char)
            end = content.rfind(close_char)
            if start >= 0 and end > start:
                try:
                    return json.loads(content[start : end + 1])
                except Exception:
                    continue
        return None

    def _parse_json_object(self, text: str) -> dict[str, object]:
        payload = self._parse_json_payload(text)
        if isinstance(payload, dict):
            return payload
        return {}

    def _normalize_severity(self, value: object, fallback: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"blocker", "critical"}:
            return "blocker"
        if normalized in {"high", "medium", "low"}:
            return normalized
        return fallback

    def _normalize_confidence(self, value: object, fallback: float) -> float:
        try:
            parsed = float(value)
        except Exception:
            return fallback
        return min(0.99, max(0.01, parsed))

    def _normalize_issue_type(self, parsed: dict[str, object], expert_id: str) -> str:
        explicit = str(parsed.get("normalized_issue_type") or "").strip().lower()
        if explicit:
            return explicit.replace(" ", "_").replace("-", "_")
        text_blob = "\n".join(
            [
                str(parsed.get("title") or ""),
                str(parsed.get("claim") or ""),
                str(parsed.get("summary") or ""),
                *[str(item) for item in list(parsed.get("matched_rules") or [])],
                *[str(item) for item in list(parsed.get("evidence") or [])],
            ]
        ).lower()
        keyword_types: list[tuple[str, tuple[str, ...]]] = [
            ("comment_contract_unimplemented", ("注释", "todo", "fixme", "comment", "未实现", "没有实现", "contract")),
            ("loop_call_amplification", ("循环", "for ", "foreach", "while ", "stream", "批量", "逐条", "n+1", "n + 1")),
            ("lock_contention_risk", ("锁", "synchronized", "lock", "deadlock", "竞态", "并发")),
            ("aggregate_factory_bypass", ("聚合工厂", "factory bypass", "直接构造聚合", "new course", "course.create")),
            ("domain_event_missing", ("领域事件", "domain event", "event", "事件丢失", "未发布事件")),
            ("query_boundary_missing", ("limit", "分页", "全量扫描", "无上限", "大结果集", "select *")),
            ("exception_swallowed", ("吞异常", "catch", "except", "printstacktrace", "只记录日志")),
            ("missing_auth_check", ("鉴权", "权限", "auth", "permission", "role", "token")),
            ("cache_consistency_risk", ("redis", "cache", "缓存", "ttl", "expire")),
            ("message_idempotency_risk", ("mq", "kafka", "consumer", "producer", "消息", "幂等", "重复消费")),
            ("missing_test", ("测试", "test", "spec", "覆盖")),
            ("naming_violation", ("命名", "naming", "变量名", "方法名")),
            ("magic_value", ("魔法值", "magic", "硬编码", "常量")),
        ]
        for issue_type, keywords in keyword_types:
            if any(keyword in text_blob for keyword in keywords):
                return issue_type
        expert_defaults = {
            "database_analysis": "database_risk",
            "performance_reliability": "performance_reliability_risk",
            "security_compliance": "security_risk",
            "redis_analysis": "cache_consistency_risk",
            "mq_analysis": "message_reliability_risk",
            "frontend_accessibility": "frontend_accessibility_risk",
            "test_verification": "missing_test",
            "ddd_architecture": "ddd_boundary_risk",
            "ddd_specification": "ddd_specification_risk",
            "maintainability_code_health": "maintainability_risk",
            "correctness_business": "business_correctness_risk",
        }
        return expert_defaults.get(expert_id, "general_code_review_risk")

    def _normalize_text_list(self, value: object, fallback: list[str]) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            chunks = [item.strip() for item in value.split("\n") if item.strip()]
            return chunks or [str(item).strip() for item in fallback if str(item).strip()]
        return [str(item).strip() for item in fallback if str(item).strip()]

    def _normalize_line_start(self, value: object, fallback: int) -> int:
        try:
            parsed = int(value)
        except Exception:
            return fallback
        return max(1, parsed)

    def _extract_summary(self, text: str, fallback: str) -> str:
        normalized = " ".join(text.replace("\n", " ").split())
        if not normalized:
            return fallback
        if len(normalized) <= 96:
            return normalized
        return normalized[:96].rstrip("，,。.;；:：") + "。"

    def _allow_llm_fallback(self, runtime_settings) -> bool:
        return bool(getattr(runtime_settings, "allow_llm_fallback", False) or os.getenv("PYTEST_CURRENT_TEST"))

    def _resolve_analysis_mode(self, review: ReviewTask, runtime_settings) -> Literal["standard", "light"]:
        mode = str(
            getattr(review, "analysis_mode", "") or getattr(runtime_settings, "default_analysis_mode", "") or "standard"
        ).strip().lower()
        if mode not in {"standard", "light"}:
            return "standard"
        return mode  # type: ignore[return-value]

    def _effective_runtime_settings(self, runtime_settings, analysis_mode: Literal["standard", "light"]):
        if analysis_mode != "light":
            return runtime_settings
        return runtime_settings.model_copy(
            update={
                "default_analysis_mode": "light",
                "default_max_debate_rounds": min(
                    int(getattr(runtime_settings, "default_max_debate_rounds", 1) or 1),
                    int(getattr(runtime_settings, "light_max_debate_rounds", 1) or 1),
                ),
            }
        )

    def _build_llm_request_options(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> dict[str, int | float]:
        if analysis_mode == "light":
            configured_timeout = int(getattr(runtime_settings, "light_llm_timeout_seconds", 90) or 90)
            timeout_cap = max(30, int(os.getenv("REVIEW_LIGHT_LLM_TIMEOUT_CAP_SECONDS", "90") or 90))
            configured_attempts = int(getattr(runtime_settings, "light_llm_retry_count", 1) or 1)
            attempt_cap = max(1, int(os.getenv("REVIEW_LIGHT_LLM_RETRY_CAP", "1") or 1))
            return {
                "timeout_seconds": min(max(30, configured_timeout), timeout_cap),
                "max_attempts": min(max(1, configured_attempts), attempt_cap),
            }
        return {
            "timeout_seconds": max(20, int(getattr(runtime_settings, "standard_llm_timeout_seconds", 60) or 60)),
            "max_attempts": max(1, int(getattr(runtime_settings, "standard_llm_retry_count", 3) or 3)),
        }

    def _max_parallel_experts(
        self,
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
    ) -> int:
        if analysis_mode == "light":
            return max(1, int(getattr(runtime_settings, "light_max_parallel_experts", 1) or 1))
        return max(1, int(getattr(runtime_settings, "standard_max_parallel_experts", 4) or 4))
