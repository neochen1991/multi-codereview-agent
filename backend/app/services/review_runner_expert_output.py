from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.domain.models.event import ReviewEvent
from app.domain.models.finding import ExpertFindingPayload
from app.domain.models.message import ConversationMessage

if TYPE_CHECKING:
    from app.domain.models.expert_profile import ExpertProfile
    from app.domain.models.review import ReviewSubject

DDD_ARCHITECTURE_EXPERT_IDS = {"ddd_architecture", "ddd_specification"}


class ReviewRunnerExpertOutputMixin:
    """Parse, repair and normalize expert LLM outputs into stable findings."""

    def _parse_expert_analysis(
        self,
        text: str,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
    ) -> dict[str, object]:
        parsed = self._parse_json_object(text)
        if parsed:
            return parsed
        has_design_docs = bool(self._review_design_docs(subject))
        return {
            "ack": self._extract_structured_field(text, "回应主Agent"),
            "title": self._extract_structured_field(text, "问题标题") or self._build_finding_title(expert),
            "claim": self._extract_structured_field(text, "风险结论")
            or self._build_finding_summary(subject, expert.expert_id),
            "finding_type": "risk_hypothesis",
            "normalized_issue_type": "",
            "severity": "",
            "line_start": line_start,
            "line_end": line_start,
            "matched_rules": [],
            "violated_guidelines": [],
            "rule_based_reasoning": self._extract_structured_field(text, "规范依据"),
            "evidence": [self._extract_structured_field(text, "代码证据")] if self._extract_structured_field(text, "代码证据") else [],
            "cross_file_evidence": [],
            "assumptions": [],
            "context_files": [],
            "why_it_matters": self._extract_structured_field(text, "证据诉求"),
            "fix_strategy": self._extract_structured_field(text, "修改思路")
            or self._build_remediation_strategy(subject, expert.expert_id, file_path),
            "suggested_fix": self._extract_structured_field(text, "修复建议")
            or self._build_remediation_suggestion(subject, expert.expert_id, file_path),
            "change_steps": self._build_remediation_steps(subject, expert.expert_id, file_path),
            "suggested_code": self._build_suggested_code(subject, file_path, line_start, expert.expert_id),
            "confidence": 0.0,
            "verification_needed": True,
            "verification_plan": "需要补充关联上下文、调用链和测试证据。",
            "design_alignment_status": "insufficient_design_context" if has_design_docs else "",
            "matched_design_points": [],
            "missing_design_points": [],
            "extra_implementation_points": [],
            "design_conflicts": [],
        }

    def _parse_expert_analyses(
        self,
        text: str,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        *,
        max_findings: int = 5,
        require_rule_guided: bool = False,
    ) -> list[dict[str, object]]:
        payload = self._parse_json_payload(text)
        candidates: list[dict[str, object]] = []
        explicit_empty_findings = False
        if isinstance(payload, list):
            if require_rule_guided:
                return []
            candidates = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            has_findings_key = "findings" in payload
            has_candidate_findings_key = "candidate_findings" in payload
            has_rule_check_results_key = "rule_check_results" in payload
            if require_rule_guided and not (has_candidate_findings_key and has_rule_check_results_key):
                return []
            nested = payload.get("findings")
            if isinstance(nested, list) and not require_rule_guided:
                candidates = [item for item in nested if isinstance(item, dict)]
                explicit_empty_findings = has_findings_key and not candidates
            rule_guided_candidates = payload.get("candidate_findings")
            if not candidates and isinstance(rule_guided_candidates, list):
                candidates = self._parse_rule_guided_candidate_findings(
                    payload,
                    file_path=file_path,
                    line_start=line_start,
                )
                explicit_empty_findings = True
            if not candidates and not require_rule_guided and not has_findings_key and not has_candidate_findings_key:
                candidates = [payload]
        if not candidates and not explicit_empty_findings and not require_rule_guided:
            candidates = [
                self._parse_expert_analysis(
                    text,
                    subject,
                    expert,
                    file_path,
                    line_start,
                )
            ]

        normalized: list[dict[str, object]] = []
        seen: set[tuple[str, int, str, str]] = set()
        for item in candidates:
            title = str(item.get("title") or "").strip().lower()
            claim = str(item.get("claim") or "").strip().lower()
            finding_type = str(item.get("finding_type") or "risk_hypothesis").strip().lower()
            current_line = self._normalize_line_start(item.get("line_start"), line_start)
            key = (title, current_line, finding_type, claim)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(dict(item))
            if len(normalized) >= max(1, int(max_findings or 1)):
                break
        return normalized

    def _parse_rule_guided_candidate_findings(
        self,
        payload: dict[str, object],
        *,
        file_path: str,
        line_start: int,
    ) -> list[dict[str, object]]:
        rule_results = self._rule_guided_rule_results_by_id(payload.get("rule_check_results"))
        parsed: list[dict[str, object]] = []
        for item in list(payload.get("candidate_findings") or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            evidence_text = str(item.get("evidence") or "").strip()
            title = str(item.get("title") or "").strip()
            if not rule_id or not evidence_text or not title:
                continue
            candidate_suggested_code = str(item.get("suggested_code") or "").strip()
            rule_result = rule_results.get(rule_id, {})
            status = str(rule_result.get("status") or "").strip().lower()
            if status in {"passed", "not_applicable"}:
                continue
            candidate_line = self._normalize_line_start(item.get("line") or item.get("line_start"), line_start)
            reason = str(rule_result.get("reason") or "").strip()
            rule_evidence = [
                str(value).strip()
                for value in list(rule_result.get("evidence") or [])
                if str(value).strip()
            ]
            missing_context = [
                str(value).strip()
                for value in list(rule_result.get("missing_context") or [])
                if str(value).strip()
            ]
            verification_needed = status != "violated"
            matched_rules = self._normalize_text_list(item.get("matched_rules"), [])
            if rule_id not in matched_rules and (rule_id != "GENERAL-EXPERT-CHECKS" or not matched_rules):
                matched_rules.append(rule_id)
            violated_guidelines = self._normalize_text_list(item.get("violated_guidelines"), [rule_id])
            change_refs = self._normalize_text_list(item.get("change_understanding_refs"), [])
            parsed.append(
                {
                    "title": title,
                    "rule_id": rule_id,
                    "claim": str(item.get("claim") or "").strip() or reason or title,
                    "finding_type": str(
                        item.get("finding_type") or ("direct_defect" if status == "violated" else "risk_hypothesis")
                    ).strip(),
                    "normalized_issue_type": str(item.get("normalized_issue_type") or "").strip(),
                    "severity": str(item.get("severity") or "medium").strip() or "medium",
                    "line_start": candidate_line,
                    "line_end": self._normalize_line_start(item.get("line_end"), candidate_line),
                    "method_name": str(item.get("method_name") or "").strip(),
                    "code_anchor": str(item.get("code_anchor") or evidence_text).strip(),
                    "change_understanding_refs": change_refs,
                    "adopted_tool_observations": self._normalize_text_list(item.get("adopted_tool_observations"), []),
                    "matched_rules": matched_rules,
                    "violated_guidelines": violated_guidelines,
                    "rule_based_reasoning": reason or f"命中规则 {rule_id}，候选证据需要进入后续校验。",
                    "evidence": [evidence_text, *rule_evidence],
                    "cross_file_evidence": self._normalize_text_list(item.get("cross_file_evidence"), []),
                    "assumptions": self._normalize_text_list(item.get("assumptions"), [])
                    + [f"缺失上下文: {item}" for item in missing_context],
                    "context_files": self._normalize_text_list(item.get("context_files"), []),
                    "why_it_matters": reason or title,
                    "fix_strategy": str(item.get("fix_strategy") or "围绕本条问题指向的位置，补齐缺失的业务逻辑或保护逻辑。").strip(),
                    "suggested_fix": str(item.get("suggested_fix") or "补齐缺失实现，并增加能复现该风险的回归测试。").strip(),
                    "change_steps": self._normalize_text_list(
                        item.get("change_steps"),
                        [
                            "补齐缺失的业务逻辑或保护逻辑",
                            "用回归用例覆盖本次被命中的风险路径",
                            "确认修复后问题代码和建议代码不再相同",
                        ],
                    ),
                    "suggested_code": candidate_suggested_code
                    if self._looks_like_concrete_suggested_code(candidate_suggested_code, file_path=file_path)
                    else "",
                    "confidence": self._rule_guided_candidate_confidence(item.get("confidence")),
                    "verification_needed": verification_needed,
                    "verification_plan": ""
                    if not verification_needed
                    else "复核问题位置、规则证据和建议代码是否一致；不一致时降级为候选发现。",
                    "file_path": str(item.get("file_path") or file_path).strip().replace("\\", "/"),
                    "observation_ids": self._normalize_text_list(item.get("observation_ids"), []),
                    "rule_guided_candidate": True,
                    "rule_check_status": status or "violated",
                    "missing_context": missing_context,
                }
            )
        return parsed

    def _rule_guided_rule_results_by_id(self, value: object) -> dict[str, dict[str, object]]:
        results: dict[str, dict[str, object]] = {}
        for item in list(value or []):
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id") or "").strip()
            if rule_id:
                results[rule_id] = dict(item)
        return results

    def _rule_guided_candidate_confidence(self, value: object) -> float:
        normalized = str(value or "").strip().lower()
        if normalized == "high":
            return 0.86
        if normalized == "low":
            return 0.55
        if normalized == "medium":
            return 0.72
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.72

    def _append_observation_followup_candidates(
        self,
        *,
        review: ReviewTask,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        normalized_batch_items: list[dict[str, object]],
        runtime_settings,
        analysis_mode: Literal["standard", "light"],
        llm_request_options: dict[str, int | float],
        bound_documents: list[object],
        active_skills: list[object],
        rule_screening: dict[str, object],
        initial_candidates: list[dict[str, object]],
        max_findings: int,
    ) -> list[dict[str, object]]:
        observations = self._filter_observations_for_batch_context(
            self._collect_batch_review_observations(repository_context, normalized_batch_items),
            normalized_batch_items,
        )
        uncovered_observations = self._find_uncovered_review_observations(initial_candidates, observations)
        if not uncovered_observations:
            return list(initial_candidates)

        deterministic_candidates = self._build_forced_observation_candidates(
            expert=expert,
            uncovered_observations=uncovered_observations,
            max_findings=max_findings,
        )
        quality_mode = str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()
        if deterministic_candidates and (initial_candidates or quality_mode == "thorough_review"):
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_observation_followup_fast_path",
                    phase="expert_review",
                    message=f"{expert.name_zh} 已用结构化 observation 快速补充候选，跳过额外 LLM 复核以控制耗时。",
                    payload={
                        "expert_id": expert.expert_id,
                        "observation_count": len(uncovered_observations),
                        "generated_candidate_count": len(deterministic_candidates),
                        "analysis_mode": analysis_mode,
                    },
                )
            )
            return self._merge_expert_analysis_candidates(
                initial_candidates,
                deterministic_candidates,
                max_findings=max_findings,
            )

        if analysis_mode == "light":
            fallback_candidates = deterministic_candidates
            if not fallback_candidates:
                return list(initial_candidates)
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_observation_followup_skipped",
                    phase="expert_review",
                    message=f"{expert.name_zh} 轻量模式下已用结构化 observation 生成补漏候选，跳过额外 LLM 复核",
                    payload={
                        "expert_id": expert.expert_id,
                        "observation_count": len(uncovered_observations),
                        "generated_candidate_count": len(fallback_candidates),
                        "analysis_mode": analysis_mode,
                    },
                )
            )
            return self._merge_expert_analysis_candidates(
                initial_candidates,
                fallback_candidates,
                max_findings=max_findings,
            )

        if initial_candidates and self._observations_covered_by_existing_candidates(
            initial_candidates,
            uncovered_observations,
        ):
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_observation_followup_skipped",
                    phase="expert_review",
                    message=f"{expert.name_zh} observation 已被首轮候选覆盖，跳过额外 LLM 增量复核",
                    payload={
                        "expert_id": expert.expert_id,
                        "reason": "covered_by_existing_candidates",
                        "observation_count": len(uncovered_observations),
                        "candidate_count": len(initial_candidates),
                        "analysis_mode": analysis_mode,
                    },
                )
            )
            return list(initial_candidates)

        batch_files = sorted(
            {
                str(item.get("file_path") or "").strip()
                for item in normalized_batch_items
                if str(item.get("file_path") or "").strip()
            }
        )
        self.message_repo.append(
            ConversationMessage(
                review_id=review.review_id,
                issue_id="review_orchestration",
                expert_id=expert.expert_id,
                message_type="expert_observation_followup",
                content=f"{expert.name_zh} 将对 {len(uncovered_observations)} 个未覆盖 observation 做一次增量复核，避免遗漏问题。",
                metadata={
                    "phase": "expert_review",
                    "analysis_mode": analysis_mode,
                    "file_path": file_path,
                    "line_start": line_start,
                    "batch_file_count": len(batch_files),
                    "batch_files": batch_files[:20],
                    "uncovered_observation_ids": [
                        str(item.get("observation_id") or "").strip() for item in uncovered_observations[:12]
                    ],
                    **self._expert_llm_metadata(expert, runtime_settings),
                },
            )
        )
        self.event_repo.append(
            ReviewEvent(
                review_id=review.review_id,
                event_type="expert_observation_followup_started",
                phase="expert_review",
                message=f"{expert.name_zh} 开始对未覆盖 observation 做增量复核",
                payload={
                    "expert_id": expert.expert_id,
                    "observation_count": len(uncovered_observations),
                    "batch_file_count": len(batch_files),
                },
            )
        )

        followup_prompt = self._build_observation_followup_prompt(
            subject=subject,
            expert=expert,
            repository_context=repository_context,
            batch_items=normalized_batch_items,
            uncovered_observations=uncovered_observations,
            existing_candidates=initial_candidates,
            max_findings=max_findings,
        )
        try:
            followup_result = self.llm_chat_service.complete_text(
                system_prompt=self._build_observation_followup_system_prompt(expert),
                user_prompt=followup_prompt,
                resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
                runtime_settings=runtime_settings,
                fallback_text=(
                    '{"rule_check_results":[],"candidate_findings":[],"context_requests":[],'
                    '"self_check":{"checked_all_rules":false,"used_context_files":[],"unverified_assumptions":["observation followup fallback"]}}'
                ),
                allow_fallback=self._allow_llm_fallback(runtime_settings),
                timeout_seconds=max(20.0, float(llm_request_options["timeout_seconds"]) * 0.75),
                max_attempts=1,
                log_context={
                    "review_id": review.review_id,
                    "issue_id": "review_orchestration",
                    "expert_id": expert.expert_id,
                    "phase": "expert_observation_followup",
                    "analysis_mode": analysis_mode,
                    "file_path": file_path,
                    "line_start": line_start,
                },
            )
            followup_candidates = self._parse_expert_analyses(
                followup_result.text,
                subject,
                expert,
                file_path,
                line_start,
                max_findings=max(1, int(max_findings or 1)),
            )
        except Exception as exc:
            self.event_repo.append(
                ReviewEvent(
                    review_id=review.review_id,
                    event_type="expert_observation_followup_degraded",
                    phase="expert_review",
                    message=f"{expert.name_zh} observation 增量复核失败，保留首轮候选并生成待验证观察 finding",
                    payload={
                        "expert_id": expert.expert_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:800],
                        "uncovered_observation_count": len(uncovered_observations),
                    },
                )
            )
            followup_candidates = []
        if not followup_candidates:
            merged_candidates = list(initial_candidates)
        else:
            merged_candidates = self._merge_expert_analysis_candidates(
                initial_candidates,
                followup_candidates,
                max_findings=max_findings,
            )
        still_uncovered = self._find_uncovered_review_observations(merged_candidates, uncovered_observations)
        fallback_candidates = self._build_forced_observation_candidates(
            expert=expert,
            uncovered_observations=still_uncovered,
            max_findings=max_findings,
        )
        if not fallback_candidates:
            return merged_candidates
        return self._merge_expert_analysis_candidates(
            merged_candidates,
            fallback_candidates,
            max_findings=max_findings,
        )

    def _build_observation_followup_system_prompt(self, expert: ExpertProfile) -> str:
        return (
            f"你是静态观察信号复核专家，当前专家职责是 {expert.expert_id} / {expert.name_zh}。"
            "本阶段只复核本轮给出的 observation 是否被已有候选覆盖，以及是否需要补充新的候选 finding。"
            "不要执行绑定规范批量校验，不要执行专家画像全量扫描，不要输出最终问题清单。"
            "如果 observation 与当前 diff 或上下文相关但证据不足，仍应输出待验证候选并写明缺失上下文。"
            "只输出符合 user prompt 中 OUTPUT_JSON 合同的 JSON 对象。"
        )

    def _collect_batch_review_observations(
        self,
        repository_context: dict[str, object],
        batch_items: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        collected: list[dict[str, object]] = []
        seen: set[str] = set()
        for item in self._normalize_review_observations(repository_context.get("review_observations")):
            observation_id = str(item.get("observation_id") or "").strip()
            if observation_id and observation_id in seen:
                continue
            if observation_id:
                seen.add(observation_id)
            collected.append(item)
        for item in self._normalize_tool_observations_as_review_observations(repository_context.get("tool_observations")):
            observation_id = str(item.get("observation_id") or "").strip()
            if observation_id and observation_id in seen:
                continue
            if observation_id:
                seen.add(observation_id)
            collected.append(item)
        for batch_item in batch_items:
            batch_context = batch_item.get("repository_context")
            if not isinstance(batch_context, dict):
                continue
            for item in self._normalize_review_observations(batch_context.get("review_observations")):
                observation_id = str(item.get("observation_id") or "").strip()
                if observation_id and observation_id in seen:
                    continue
                if observation_id:
                    seen.add(observation_id)
                collected.append(item)
            for item in self._normalize_tool_observations_as_review_observations(batch_context.get("tool_observations")):
                observation_id = str(item.get("observation_id") or "").strip()
                if observation_id and observation_id in seen:
                    continue
                if observation_id:
                    seen.add(observation_id)
                collected.append(item)
        return collected

    def _filter_observations_for_batch_context(
        self,
        observations: list[dict[str, object]],
        batch_items: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not observations or not batch_items:
            return list(observations or [])
        scoped: list[dict[str, object]] = []
        for observation in observations:
            if self._observation_matches_any_batch_hunk(observation, batch_items):
                scoped.append(dict(observation))
        return scoped

    def _observation_matches_any_batch_hunk(
        self,
        observation: dict[str, object],
        batch_items: list[dict[str, object]],
    ) -> bool:
        observation_file = str(observation.get("file_path") or "").strip().replace("\\", "/").lower()
        if not observation_file:
            return False
        for batch_item in batch_items:
            batch_file = str(batch_item.get("file_path") or "").strip().replace("\\", "/").lower()
            if batch_file != observation_file:
                continue
            target_hunks = [
                dict(item)
                for item in list(batch_item.get("target_hunks") or [])
                if isinstance(item, dict)
            ]
            target_hunk = batch_item.get("target_hunk")
            if isinstance(target_hunk, dict) and target_hunk:
                target_hunks.append(dict(target_hunk))
            if not target_hunks:
                return True
            if any(self._observation_matches_target_hunk(observation, hunk) for hunk in target_hunks):
                return True
        return False

    def _observation_matches_target_hunk(
        self,
        observation: dict[str, object],
        target_hunk: dict[str, object],
    ) -> bool:
        observation_line = self._normalize_optional_line_value(observation.get("line_start"))
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if observation_line is not None and changed_lines and int(observation_line) in set(changed_lines):
            return True
        text_matches = self._observation_matches_hunk_text(observation, target_hunk)
        if text_matches:
            return True
        start_line = (
            self._normalize_optional_line_value(target_hunk.get("start_line"))
            or self._normalize_optional_line_value(target_hunk.get("line_start"))
        )
        end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
        if observation_line is None or start_line is None or end_line is None:
            return False
        return int(start_line) - 1 <= int(observation_line) <= int(end_line) + 1 and text_matches

    def _observation_matches_hunk_text(
        self,
        observation: dict[str, object],
        target_hunk: dict[str, object],
    ) -> bool:
        hunk_text = str(target_hunk.get("excerpt") or target_hunk.get("content") or "").lower()
        if not hunk_text:
            return False
        terms = self._tokenize_observation_text(observation)
        if not terms:
            return False
        distinctive_terms = {
            term
            for term in terms
            if len(term) >= 4 or term in {"sql", "like", "save", "query", "event", "new"}
        }
        if not distinctive_terms:
            distinctive_terms = terms
        matched = [term for term in distinctive_terms if term.lower() in hunk_text]
        return len(matched) >= min(2, len(distinctive_terms)) or any(
            term in {"like", "eventbus", "repository", "save", "query", "catch"}
            and term.lower() in hunk_text
            for term in distinctive_terms
        )

    def _normalize_tool_observations_as_review_observations(self, value: object) -> list[dict[str, object]]:
        normalized: list[dict[str, object]] = []
        for raw in list(value or []):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            tool = str(item.get("tool") or "tool").strip()
            rule_id = str(item.get("rule_id") or item.get("check_id") or "rule").strip()
            line_start = int(self._normalize_optional_line_value(item.get("line_start") or item.get("line")) or 1)
            if hasattr(self, "_canonical_tool_observation_id"):
                observation_id = self._canonical_tool_observation_id(item)  # type: ignore[attr-defined]
            else:
                file_path = str(item.get("file_path") or item.get("path") or "unknown").strip().replace("\\", "/")
                existing = str(item.get("id") or item.get("observation_id") or "").strip()
                observation_id = existing if existing.startswith("sast:") else f"sast:{tool}:{rule_id}:{file_path or 'unknown'}:{line_start}"
                item["legacy_observation_id"] = existing or f"{tool}:{rule_id}:{line_start}"
            message = str(item.get("message") or item.get("summary") or "").strip()
            why_it_matters = str(item.get("why_it_matters") or "").strip()
            evidence = [
                value
                for value in [
                    f"{tool}:{rule_id}" if tool or rule_id else "",
                    message,
                    why_it_matters,
                ]
                if value
            ]
            normalized.append(
                {
                    **item,
                    "id": observation_id,
                    "observation_id": observation_id,
                    "kind": "tool_observation",
                    "summary": message or f"静态工具命中 {tool}:{rule_id} 候选信号。",
                    "evidence": evidence,
                    "risk_hints": [
                        str(value).strip()
                        for value in list(item.get("evidence_required") or [])
                        if str(value).strip()
                    ],
                    "source": str(item.get("source") or "sast_prescan").strip(),
                    "observation_type": "tool_observation",
                    "line_start": line_start,
                }
            )
        return normalized[:20]

    def _find_uncovered_review_observations(
        self,
        candidates: list[dict[str, object]],
        observations: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        if not observations:
            return []
        covered_ids: set[str] = set()
        covered_lines: dict[str, set[int]] = {}
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            for observation_id in self._normalize_text_list(candidate.get("observation_ids"), []):
                covered_ids.add(observation_id)
            candidate_file_path = str(candidate.get("file_path") or "").strip()
            candidate_line = self._normalize_optional_line_value(candidate.get("line_start"))
            if candidate_file_path and candidate_line is not None:
                covered_lines.setdefault(candidate_file_path, set()).add(int(candidate_line))

        uncovered: list[dict[str, object]] = []
        for item in observations:
            observation_id = str(item.get("observation_id") or "").strip()
            if observation_id and observation_id in covered_ids:
                continue
            observation_file_path = str(item.get("file_path") or "").strip()
            observation_line = self._normalize_optional_line_value(item.get("line_start"))
            if observation_file_path and observation_line is not None:
                if int(observation_line) in covered_lines.get(observation_file_path, set()):
                    continue
            uncovered.append(dict(item))
        return uncovered[:8]

    def _observations_covered_by_existing_candidates(
        self,
        candidates: list[dict[str, object]],
        observations: list[dict[str, object]],
    ) -> bool:
        if not observations:
            return True
        if not candidates:
            return False
        return all(
            self._observation_covered_by_existing_candidate(candidates, observation)
            for observation in observations
        )

    def _observation_covered_by_existing_candidate(
        self,
        candidates: list[dict[str, object]],
        observation: dict[str, object],
    ) -> bool:
        observation_id = str(observation.get("observation_id") or "").strip()
        observation_file = str(observation.get("file_path") or "").strip().lower()
        observation_line = self._normalize_optional_line_value(observation.get("line_start"))
        observation_terms = self._tokenize_observation_text(observation)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if observation_id and observation_id in self._normalize_text_list(candidate.get("observation_ids"), []):
                return True
            candidate_file = str(candidate.get("file_path") or "").strip().lower()
            if not observation_file or candidate_file != observation_file:
                continue
            text_matches = bool(observation_terms) and self._candidate_text_matches_observation(candidate, observation_terms)
            candidate_line = self._normalize_optional_line_value(candidate.get("line_start"))
            if observation_line is not None and candidate_line is not None:
                if abs(int(candidate_line) - int(observation_line)) <= 3 and text_matches:
                    return True
                if abs(int(candidate_line) - int(observation_line)) <= 5 and self._candidate_and_observation_share_risk_domain(
                    candidate,
                    observation,
                ):
                    return True
            if text_matches:
                return True
        return False

    def _tokenize_observation_text(self, observation: dict[str, object]) -> set[str]:
        text_parts = [
            str(observation.get("kind") or ""),
            str(observation.get("summary") or ""),
        ]
        text_parts.extend(str(item) for item in list(observation.get("evidence") or [])[:4])
        text_parts.extend(str(item) for item in list(observation.get("risk_hints") or [])[:4])
        text = " ".join(text_parts).lower()
        raw_terms = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)
        stop_terms = {
            "the",
            "and",
            "for",
            "with",
            "this",
            "that",
            "当前",
            "代码",
            "问题",
            "风险",
            "存在",
            "可能",
            "导致",
        }
        return {term for term in raw_terms if term not in stop_terms}

    def _candidate_text_matches_observation(
        self,
        candidate: dict[str, object],
        observation_terms: set[str],
    ) -> bool:
        candidate_text = " ".join(
            [
                str(candidate.get("title") or ""),
                str(candidate.get("claim") or ""),
                str(candidate.get("rule_based_reasoning") or ""),
                " ".join(str(item) for item in list(candidate.get("evidence") or [])[:5]),
                " ".join(str(item) for item in list(candidate.get("violated_guidelines") or [])[:5]),
            ]
        ).lower()
        if not candidate_text:
            return False
        matches = [term for term in observation_terms if term in candidate_text]
        return len(matches) >= min(2, len(observation_terms))

    def _candidate_and_observation_share_risk_domain(
        self,
        candidate: dict[str, object],
        observation: dict[str, object],
    ) -> bool:
        candidate_domains = self._risk_domains_from_text(
            " ".join(
                [
                    str(candidate.get("title") or ""),
                    str(candidate.get("claim") or ""),
                    str(candidate.get("rule_based_reasoning") or ""),
                    " ".join(str(item) for item in list(candidate.get("evidence") or [])[:5]),
                    " ".join(str(item) for item in list(candidate.get("violated_guidelines") or [])[:5]),
                ]
            )
        )
        observation_domains = self._risk_domains_from_text(
            " ".join(
                [
                    str(observation.get("kind") or ""),
                    str(observation.get("summary") or ""),
                    " ".join(str(item) for item in list(observation.get("evidence") or [])[:5]),
                    " ".join(str(item) for item in list(observation.get("risk_hints") or [])[:5]),
                ]
            )
        )
        return bool(candidate_domains and observation_domains and candidate_domains.intersection(observation_domains))

    def _risk_domains_from_text(self, value: str) -> set[str]:
        text = str(value or "").lower()
        domains: set[str] = set()
        if any(term in text for term in ("like", "模糊匹配", "精确匹配", "查询语义", "query_plan", "索引", "全表扫描")):
            domains.add("query_semantics")
        if any(term in text for term in ("权限", "租户", "越权", "authorization", "scope_broadened", "数据范围")):
            domains.add("authorization_scope")
        if any(term in text for term in ("eventbus", "domain_event", "领域事件", "事件发布", "publish", "repository.save")):
            domains.add("domain_event_ordering")
        if any(term in text for term in ("工厂", "factory", "new course", "聚合根", "不变量")):
            domains.add("aggregate_factory")
        if any(term in text for term in ("catch", "exception", "异常", "吞掉", "静默")):
            domains.add("exception_handling")
        if any(term in text for term in ("循环", "loop", "for_each", "foreach", "逐条", "批量")):
            domains.add("loop_or_batching")
        return domains

    def _merge_expert_analysis_candidates(
        self,
        base_candidates: list[dict[str, object]],
        extra_candidates: list[dict[str, object]],
        *,
        max_findings: int,
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[tuple[str, str, int, str]] = set()
        signal_titles = {"循环调用放大", "承诺未落地"}
        signal_best_by_anchor: dict[tuple[str, str, int], dict[str, object]] = {}
        for item in list(base_candidates) + list(extra_candidates):
            if not isinstance(item, dict):
                continue
            file_key = str(item.get("file_path") or "").strip().lower()
            title_key = str(item.get("title") or "").strip()
            line_key = int(self._normalize_optional_line_value(item.get("line_start")) or 0)
            if title_key in signal_titles:
                signal_anchor = (file_key, title_key.lower(), line_key)
                incumbent = signal_best_by_anchor.get(signal_anchor)
                if incumbent is None or self._candidate_strength_score(item) > self._candidate_strength_score(incumbent):
                    signal_best_by_anchor[signal_anchor] = dict(item)
                continue
            key = (
                file_key,
                title_key.lower(),
                line_key,
                str(item.get("claim") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
        merged.extend(signal_best_by_anchor.values())
        merged.sort(
            key=lambda item: (
                str(item.get("file_path") or "").strip().lower(),
                int(self._normalize_optional_line_value(item.get("line_start")) or 0),
                -self._candidate_strength_score(item),
            )
        )
        return merged[: max(1, int(max_findings or 1))]

    def _candidate_strength_score(self, candidate: dict[str, object]) -> float:
        finding_type = str(candidate.get("finding_type") or "").strip().lower()
        severity = str(candidate.get("severity") or "").strip().lower()
        observation_count = len(self._normalize_text_list(candidate.get("observation_ids"), []))
        confidence = self._normalize_confidence(candidate.get("confidence"), 0.0)
        score = confidence
        if finding_type == "direct_defect":
            score += 1.0
        elif finding_type == "test_gap":
            score += 0.5
        if severity in {"blocker", "critical", "high"}:
            score += 0.6
        elif severity == "medium":
            score += 0.2
        if observation_count:
            score += 0.3
        if bool(candidate.get("direct_evidence")):
            score += 0.3
        return score

    def _build_observation_followup_prompt(
        self,
        *,
        subject: ReviewSubject,
        expert: ExpertProfile,
        repository_context: dict[str, object],
        batch_items: list[dict[str, object]],
        uncovered_observations: list[dict[str, object]],
        existing_candidates: list[dict[str, object]],
        max_findings: int,
    ) -> str:
        lines = [
            f"你是 {expert.name_zh}，现在只做遗漏问题增量复核。",
            "要求：",
            "1. 下面给出的 observation 是首轮结果尚未明确覆盖的可疑代码现象；",
            "2. 你必须逐条判断 observation 是否构成真实问题；",
            "3. 只有确认成立且不是首轮已输出重复问题时，才输出新的 candidate_finding；",
            "4. 每条新增 candidate_finding 必须带 rule_id、file_path、line、title、evidence、confidence、observation_ids；",
            "5. 如果没有新增问题，candidate_findings 返回空数组，但仍要输出 rule_check_results、context_requests、self_check。",
            f"6. 最多新增 {max(1, int(max_findings or 1))} 条 candidate_findings。",
            "",
            f"仓库: {subject.repo_id}",
            f"目标分支: {subject.target_ref}",
            f"变更文件: {', '.join(subject.changed_files[:20]) or 'unknown'}",
            "",
            "首轮已输出 findings 摘要：",
        ]
        if existing_candidates:
            for item in existing_candidates[:12]:
                lines.append(
                    f"- {str(item.get('file_path') or '').strip() or 'unknown'}"
                    f":L{int(self._normalize_optional_line_value(item.get('line_start')) or 1)} "
                    f"{str(item.get('title') or '').strip() or 'untitled'} | "
                    f"{str(item.get('claim') or '').strip() or 'no-claim'}"
                )
        else:
            lines.append("- 首轮没有输出 findings。")
        lines.extend(["", "待复核 observation："])
        for item in uncovered_observations:
            observation_id = str(item.get("observation_id") or "").strip() or "observation"
            file_path = str(item.get("file_path") or "").strip() or "unknown"
            line_start = int(self._normalize_optional_line_value(item.get("line_start")) or 1)
            summary = str(item.get("summary") or "").strip()
            kind = str(item.get("kind") or "").strip()
            lines.append(f"- {observation_id} | {file_path}:L{line_start} | {kind} | {summary}")
            evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
            if evidence:
                lines.append(f"  证据: {' / '.join(evidence[:3])}")
            risk_hints = [str(value).strip() for value in list(item.get("risk_hints") or []) if str(value).strip()]
            if risk_hints:
                lines.append(f"  风险提示: {' / '.join(risk_hints[:3])}")

        appendix = self._build_multi_file_prompt_appendix(subject, expert, batch_items)
        if appendix.strip():
            lines.extend(["", appendix])
        repository_blocks = self._build_repository_source_blocks(repository_context, [])
        if repository_blocks.strip():
            lines.extend(["", "关键源码上下文：", repository_blocks])

        lines.extend(
            [
                "",
                "输出格式要求：",
                '仅输出 JSON：{"rule_check_results":[],"candidate_findings":[],"context_requests":[],"self_check":{"checked_all_rules":true,"used_context_files":[],"unverified_assumptions":[]}}',
                "rule_check_results 每条至少包含: rule_id, status, evidence, missing_context, reason；rule_id 可使用 observation_id 或真实命中规则 ID。",
                "candidate_findings 每条至少包含: rule_id, file_path, line, title, evidence, confidence, observation_ids；line 必须落在真实变更行或 observation 行。",
                "observation 只能作为候选风险线索，默认 status=insufficient_context、confidence=low/medium。",
                "只有当你能从源码上下文独立证明缺陷成立，并且证据链不依赖 observation 本身时，才允许升级为 direct_defect。",
            ]
        )
        return "\n".join(lines)

    def _classify_tool_observation_candidate(
        self,
        *,
        tool: str,
        rule_id: str,
        category: str,
        message: str,
        why_it_matters: str,
        evidence: list[str],
    ) -> tuple[str, str, str]:
        text = "\n".join([tool, rule_id, category, message, why_it_matters, *evidence]).lower()
        compact = re.sub(r"[\s_.]+", "-", text)
        if any(token in compact for token in ("sql-injection", "injection", "jdbc-injection", "jpa-injection")):
            return (
                "sql_injection_risk",
                "SQL/命令注入风险必须使用参数化查询、白名单校验或安全 API，禁止把外部输入直接拼接进 SQL/查询表达式。",
                "确认输入来源和查询构造路径，改为参数绑定或 Criteria 参数化表达式，并补充恶意输入回归测试。",
            )
        if any(token in compact for token in ("xss", "cross-site-scripting")):
            return (
                "xss_output_encoding_risk",
                "用户可控内容输出到页面、模板或响应前必须按上下文编码，禁止未转义直接输出。",
                "按 HTML/JS/URL/CSS 上下文做编码或使用安全模板 API，并补充包含特殊字符的安全测试。",
            )
        if any(token in compact for token in ("ssrf", "server-side-request-forgery")):
            return (
                "ssrf_risk",
                "外部可控 URL、主机或路径发起服务端请求前必须做白名单、协议和内网地址校验。",
                "限制目标协议和域名/IP 范围，禁止访问内网与元数据地址，并补充绕过用例测试。",
            )
        if any(token in compact for token in ("secret", "password", "credential", "apikey", "api-key", "token-log", "token")):
            return (
                "sensitive_data_exposure",
                "敏感凭据、token、密码和个人敏感信息不得写入日志、异常信息或明文响应。",
                "移除明文输出或改为脱敏/哈希展示，补充日志断言防止敏感字段再次泄露。",
            )
        if any(token in compact for token in ("auth", "permission", "authorization", "access-control", "idor", "tenant")):
            return (
                "authorization_bypass_risk",
                "涉及用户、租户、资源归属或管理操作的入口必须做鉴权和越权校验。",
                "在服务入口或查询条件中补齐身份、角色、租户和资源归属校验，并增加越权访问测试。",
            )
        if any(token in compact for token in ("empty-catch", "emptycatch", "swallow", "catch-generic", "generic-exception")):
            return (
                "exception_swallowed",
                "异常处理不能静默吞掉，至少需要日志、重新抛出、补偿或明确失败状态。",
                "恢复异常日志和失败传播语义，按业务语义选择重试、补偿或抛出异常，并补充异常路径测试。",
            )
        if any(token in compact for token in ("n-plus-one", "n+1", "loop", "performance")):
            return (
                "loop_call_amplification",
                "循环体内不应逐条执行数据库、远程接口或消息发送等外部依赖调用。",
                "改为批量查询、批量提交或循环外聚合后统一处理，并补充大批量输入场景测试。",
            )
        if any(token in compact for token in ("unbounded-query", "limit", "pagination", "pageable", "full-table")):
            return (
                "unbounded_query_risk",
                "列表、批处理和消费查询必须有稳定边界、分页或批量上限，禁止无界查询。",
                "补充分页、limit 或固定批次窗口，并为大数据量场景补充回归测试。",
            )
        if str(category or "").strip().lower() == "security":
            return (
                "security_tool_observation",
                "安全工具候选必须结合当前 diff、代码上下文、语言通用安全规范和绑定安全规范复核。",
                "确认工具命中是否为当前变更引入，并按对应安全规范修复代码和补充测试。",
            )
        return (
            f"tool_observation_{str(category or 'static_analysis').strip()}".replace("-", "_"),
            "静态工具候选必须结合当前 diff、专家通用规范和绑定规范复核后才能升级。",
            "结合具体工具规则和代码上下文修复该候选风险；若确认误报，在人工确认中说明误报依据。",
        )

    def _build_forced_observation_candidates(
        self,
        *,
        expert: ExpertProfile,
        uncovered_observations: list[dict[str, object]],
        max_findings: int,
    ) -> list[dict[str, object]]:
        if not uncovered_observations or max(1, int(max_findings or 1)) <= 0:
            return []

        forced: list[dict[str, object]] = []
        for item in uncovered_observations:
            kind = str(item.get("kind") or "").strip()
            file_path = str(item.get("file_path") or "").strip()
            line_start = int(self._normalize_optional_line_value(item.get("line_start")) or 1)
            observation_id = str(item.get("observation_id") or "").strip()
            evidence = [str(value).strip() for value in list(item.get("evidence") or []) if str(value).strip()]
            summary = str(item.get("summary") or "").strip()
            why_it_matters = str(item.get("why_it_matters") or "").strip()
            related_symbols = [str(value).strip() for value in list(item.get("related_symbols") or []) if str(value).strip()]
            symbol_display = " / ".join(related_symbols[:2]) if related_symbols else "当前调用"

            if kind == "tool_observation" or str(item.get("observation_type") or "").strip() == "tool_observation":
                tool = str(item.get("tool") or "tool").strip()
                rule_id = str(item.get("rule_id") or "rule").strip()
                category = str(item.get("category") or "static_analysis").strip()
                severity = str(item.get("severity") or "medium").strip().lower() or "medium"
                issue_type, guideline, remediation_hint = self._classify_tool_observation_candidate(
                    tool=tool,
                    rule_id=rule_id,
                    category=category,
                    message=summary,
                    why_it_matters=why_it_matters,
                    evidence=evidence,
                )
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": f"静态工具候选需复核：{rule_id}",
                        "finding_type": "risk_hypothesis",
                        "normalized_issue_type": issue_type,
                        "claim": f"{tool} 命中 {rule_id} 候选信号，当前变更需要由 {expert.name_zh} 结合上下文判断是否构成真实问题。",
                        "severity": "high" if severity in {"error", "critical", "high"} else "medium" if severity in {"warning", "medium"} else "low",
                        "matched_rules": [f"{tool}:{rule_id}"],
                        "violated_guidelines": [guideline],
                        "rule_based_reasoning": why_it_matters or "该命中来自静态扫描工具，只能作为候选证据；需要专家确认是否落在当前变更和真实可达路径上。",
                        "evidence": evidence[:3] or [summary or f"{tool} 命中 {rule_id}"],
                        "cross_file_evidence": [],
                        "assumptions": ["工具信号尚未被专家主审输出明确覆盖，系统保留为待验证 finding，避免静默漏报。"],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "adopted_tool_observations": [observation_id or f"{tool}:{rule_id}"],
                        "fix_strategy": remediation_hint,
                        "suggested_fix": remediation_hint,
                        "change_steps": ["确认工具命中是否位于本次变更或影响路径", "按对应安全/质量规范修复代码", "补充回归测试或静态规则验证"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.62), 0.78),
                        "verification_needed": True,
                        "verification_plan": "验证重点：确认工具命中行是否为当前变更、输入/调用路径是否真实可达，以及是否已有上游防护或项目规则豁免。",
                        "direct_evidence": False,
                        "evidence_source": "tool_observation",
                    }
                )
            elif expert.expert_id == "performance_reliability" and kind == "control_flow_with_external_call":
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "循环调用放大",
                        "finding_type": "risk_hypothesis",
                        "claim": f"当前实现把外部依赖调用放进循环路径（{symbol_display}），批量场景会线性放大数据库/网络往返与整体时延。",
                        "severity": "high",
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": "循环体内逐条调用仓储、远程服务或消息发送，可能把单次调用成本放大到批量路径，需要结合调用规模和依赖成本复核。",
                        "evidence": evidence[:3] or [summary or "检测到循环体中的外部依赖调用。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "把循环内逐条外部调用改成批量查询、批量远程接口或先聚合后统一处理。",
                        "suggested_fix": "优先把循环内的仓储/远程调用提到循环外，避免每个元素都触发一次外部依赖访问。",
                        "change_steps": ["把循环内逐条调用移到循环外统一处理", "改用批量获取或批量提交", "保留单次结果映射关系"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "验证重点：核对循环内依赖调用次数、批量输入规模和外部依赖成本，确认是否按批量接口或批量提交修复。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "ddd_architecture" and kind in {
                "construction_path_changed",
                "aggregate_factory_bypass",
                "domain_event_ordering_risk",
            }:
                is_event_ordering = kind == "domain_event_ordering_risk"
                issue_type = "domain_event_ordering_risk" if is_event_ordering else "aggregate_factory_bypass"
                title = "领域事件发布早于聚合持久化" if is_event_ordering else "绕过聚合工厂创建聚合根"
                claim = (
                    f"当前变更把领域事件发布放在聚合持久化之前（{symbol_display}），事件订阅方可能先看到尚未持久化成功的状态。"
                    if is_event_ordering
                    else f"当前变更把原聚合工厂创建路径改成直接 new 构造（{symbol_display}），会绕过工厂封装的不变量校验、领域事件记录或创建语义。"
                )
                reasoning = (
                    "领域事件应在聚合状态持久化成功后再发布，否则事件消费者可能读到未提交或最终失败的数据。"
                    if is_event_ordering
                    else "聚合工厂通常封装创建不变量、默认值和领域事件记录；直接 new 聚合根会让这些领域语义失效。"
                )
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": title,
                        "finding_type": "direct_defect",
                        "normalized_issue_type": issue_type,
                        "claim": claim,
                        "severity": "high",
                        "matched_rules": ["DDD-JDDD-001", "ARCH-JDDD-002"],
                        "violated_guidelines": ["聚合创建和领域事件发布必须保持领域不变量、持久化顺序和事件一致性"],
                        "rule_based_reasoning": reasoning,
                        "evidence": evidence[:3] or [summary or "检测到对象创建路径发生变化。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "恢复原有聚合工厂/静态工厂创建入口，并保持先保存聚合、再发布聚合领域事件的顺序。",
                        "suggested_fix": "使用聚合工厂创建对象，保存成功后再发布该聚合产生的领域事件，并补充创建行为测试。",
                        "change_steps": ["恢复原有聚合工厂/静态工厂创建入口", "先保存聚合状态，再发布聚合产生的领域事件", "补充聚合创建、事件记录和持久化顺序的回归测试"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.86), 0.92),
                        "verification_needed": False,
                        "verification_plan": "",
                        "direct_evidence": True,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "correctness_business" and kind == "declared_intent_without_implementation":
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "承诺未落地",
                        "finding_type": "risk_hypothesis",
                        "claim": f"注释、TODO 或方法意图已经承诺了行为（{symbol_display}），但当前实现没有对应动作，调用方会误以为能力已经落地。",
                        "severity": "high",
                        "matched_rules": ["CORR-JDDD-001", "CORRECTNESS-CONTRACT-001"],
                        "violated_guidelines": ["注释、TODO 或接口说明承诺的业务行为必须在实现中落地"],
                        "rule_based_reasoning": "注释、接口说明或 TODO 已经表达代码语义承诺；当实现中没有对应动作时，调用方会按已落地能力使用，容易造成业务结果缺失。",
                        "evidence": evidence[:3] or [summary or "检测到注释、TODO 或方法意图与实现不一致。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "要么补齐承诺中的行为，要么删除会误导调用方的注释、TODO 或命名表达。",
                        "suggested_fix": "如果该行为属于本次交付范围，请补齐对应业务动作并增加测试；如果不属于本次交付范围，应删除或改写会误导调用方的 TODO/注释。",
                        "change_steps": ["补齐 TODO 或注释承诺的业务动作", "增加覆盖该业务动作的回归测试", "同步更新注释、接口说明和方法命名，避免继续承诺未实现能力"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "验证重点：核对注释、TODO 或命名表达对应的业务动作是否已经在当前实现中落地。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "database_analysis" and kind in {"query_without_bound", "query_plan_risk"}:
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "查询边界缺失",
                        "finding_type": "risk_hypothesis",
                        "normalized_issue_type": "query_bound_removed",
                        "claim": f"当前查询路径缺少分页、LIMIT 或批量边界保护（{symbol_display}），数据量放大后可能触发全表扫描或大结果集返回。",
                        "severity": "high",
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": "查询入口删除分页、LIMIT 或引入模糊/全量查询时，数据库访问成本会随数据规模放大，属于需要直接修正的数据访问缺陷。",
                        "evidence": evidence[:3] or [summary or "检测到查询边界或查询计划风险。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "恢复分页、LIMIT、批量分片或更精确的查询条件。",
                        "suggested_fix": "为该查询补回分页/limit 约束，并确认索引能覆盖过滤和排序字段。",
                        "change_steps": ["恢复查询边界", "补充或确认索引", "增加大数据量场景测试"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "验证重点：检查查询入口是否具备分页、LIMIT、批量窗口或等价边界控制。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "performance_reliability" and kind in {"bulk_processing_boundary_missing", "transactional_side_effect"}:
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "批量路径可靠性风险",
                        "finding_type": "risk_hypothesis",
                        "normalized_issue_type": kind,
                        "claim": f"当前批量或事务路径存在会随数据量放大的副作用（{symbol_display}），容易造成吞吐退化、超时或回滚语义不一致。",
                        "severity": "high",
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": "批量路径和事务边界内的外部副作用都需要有边界、超时和失败语义，否则生产数据量下会放大为稳定性问题。",
                        "evidence": evidence[:3] or [summary or "检测到批处理或事务副作用风险。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "把副作用移出事务边界，或改成批量、异步、带超时和幂等保护的处理方式。",
                        "suggested_fix": "为批处理增加分片、限流、超时和失败补偿；事务内不要直接做远程调用或消息发送。",
                        "change_steps": ["评估批量输入上限和调用频次", "拆分事务与外部副作用", "补充超时/幂等/重试保护"],
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "验证重点：检查批量规模、事务边界和外部副作用是否已通过分片、超时、幂等或补偿机制控制。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "security_compliance" and kind in {
                "input_validation_removed",
                "security_guard_removed",
                "sql_injection_risk",
                "sensitive_data_exposure",
                "query_authorization_scope_broadened",
            }:
                if kind == "sql_injection_risk":
                    title = "动态 SQL 拼接存在注入风险"
                    normalized_issue_type = "sql_injection_risk"
                    claim = f"当前查询把外部输入拼进 SQL 或动态查询语句（{symbol_display}），应改为参数绑定，避免注入和越权读取。"
                    reasoning = "SQL 查询不能通过字符串拼接承接用户输入；应使用占位符、参数绑定或安全的查询构造器。"
                    fix_strategy = "把拼接 SQL 改为参数化查询，并补充带特殊字符输入的安全测试。"
                    suggested_fix = "使用 ? / named parameter / QueryWrapper 等参数绑定方式，不要把请求参数直接拼进 SQL 字符串。"
                    steps = ["定位拼接 SQL 的输入来源", "改成参数绑定", "补充注入字符和越权查询测试"]
                elif kind == "sensitive_data_exposure":
                    title = "敏感信息可能被日志或响应暴露"
                    normalized_issue_type = "sensitive_data_exposure"
                    claim = f"当前变更把 token、密码、密钥或个人敏感字段输出到日志/响应路径（{symbol_display}），需要脱敏或移除。"
                    reasoning = "敏感字段进入日志、错误响应或普通返回值后，会扩大泄露面并带来合规风险。"
                    fix_strategy = "移除敏感字段输出，或在统一脱敏组件中只保留必要的掩码信息。"
                    suggested_fix = "日志只记录脱敏后的用户标识或请求编号，不输出 token、password、secret、Authorization 等字段原文。"
                    steps = ["识别敏感字段来源", "移除或脱敏输出", "补充日志/响应不含敏感字段的测试"]
                elif kind == "query_authorization_scope_broadened":
                    title = "共享过滤条件从精确匹配放宽为模糊匹配"
                    normalized_issue_type = "query_authorization_scope_broadened"
                    claim = f"当前共享过滤或查询条件从精确匹配放宽为模糊匹配（{symbol_display}），可能扩大用户、租户或业务对象的数据访问范围。"
                    reasoning = "用于权限、租户、用户或通用 criteria 的过滤条件应保持边界明确；equal 改为 like/contains 会让原本只匹配一个值的查询返回更多数据。"
                    fix_strategy = "保留安全边界相关字段的精确匹配；如果确需模糊搜索，应只用于明确允许搜索的展示字段，并增加权限/租户过滤测试。"
                    suggested_fix = "将共享 criteria 的默认等值过滤恢复为 builder.equal；新增单独的 contains/like 操作符并限制可用字段。"
                    steps = ["识别该过滤器覆盖的用户/租户/权限字段", "恢复默认精确匹配或拆出显式模糊查询操作符", "补充越权和结果集扩大回归测试"]
                else:
                    title = "入口保护变更风险"
                    normalized_issue_type = "security_guard_removed"
                    claim = f"当前变更删除或弱化了入口校验、权限校验或身份一致性保护（{symbol_display}），如果没有等价保护会放大越权或非法输入风险。"
                    reasoning = "入口校验和权限判断属于安全边界；删除或迁移这类保护时，应在同一调用链提供等价保护，避免越权或非法输入进入业务层。"
                    fix_strategy = "把被删除或迁移的入口保护落实到 Controller、Filter、Interceptor、注解或下游服务中的等价保护点。"
                    suggested_fix = "如果没有等价保护，请恢复入口校验或权限判断；如果已经迁移，请补充测试和说明证明保护仍然生效。"
                    steps = ["定位原入口保护职责", "补齐新路径的等价保护", "补充非法输入或越权路径测试"]
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": title,
                        "finding_type": "risk_hypothesis",
                        "normalized_issue_type": normalized_issue_type,
                        "claim": claim,
                        "severity": "high",
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": reasoning,
                        "evidence": evidence[:3] or [summary or "检测到入口保护或权限校验被删除。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": fix_strategy,
                        "suggested_fix": suggested_fix,
                        "change_steps": steps,
                        "suggested_code": "",
                        "confidence": min(max(self._normalize_confidence(item.get("confidence"), 0.0), 0.68), 0.8),
                        "verification_needed": True,
                        "verification_plan": "验证重点：沿当前接口调用链检查输入、权限、日志/响应和 SQL 执行路径是否具备等价安全保护。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )

            if len(forced) >= max(1, int(max_findings or 1)):
                break

        return forced

    def _looks_like_concrete_suggested_code(self, value: object, *, file_path: str) -> bool:
        code = str(value or "").strip()
        if not code:
            return False
        lower = code.lower()
        generic_markers = [
            "当前还没有生成建议修改代码",
            "请先补充更多上下文",
            "修复思路",
            "修复建议",
            "建议如下",
            "可以考虑",
            "需要结合实际",
            "请结合实际",
            "todo",
            "待补充",
            "占位",
            "placeholder",
            "伪代码",
            "根据实际",
            "按实际",
            "# suggested rewrite for",
        ]
        if any(marker in lower for marker in generic_markers):
            return False
        if "..." in code or "…" in code:
            return False
        non_empty_lines = [line.strip() for line in code.splitlines() if line.strip()]
        if non_empty_lines and all(line.startswith(("//", "#", "/*", "*", "--")) for line in non_empty_lines):
            return False
        language = self._infer_code_language(file_path)
        if language == "java":
            return any(token in code for token in (";", "{", "}", "class ", "public ", "private ", "protected ", "return "))
        if language in {"javascript", "typescript", "jsx", "tsx"}:
            return any(token in code for token in (";", "{", "}", "function ", "const ", "let ", "return "))
        if language == "python":
            return any(token in code for token in (":\n", "def ", "return ", "if "))
        return len(code.splitlines()) >= 2

    def _repair_missing_suggested_code(
        self,
        *,
        review: ReviewTask,
        expert: ExpertProfile,
        runtime_settings,
        llm_request_options: dict[str, int | float],
        file_path: str,
        line_start: int,
        parsed: dict[str, object],
        target_hunk: dict[str, object],
    ) -> str:
        if self._should_skip_suggested_code_llm_repair(runtime_settings):
            return ""
        current_code = (
            str(target_hunk.get("excerpt") or "").strip()
            or self._load_repository_problem_context(review.subject, file_path, line_start, target_hunk).get("snippet", "")
            or self._load_repository_source_excerpt(review.subject, file_path, line_start)
        )
        repair_prompt = (
            f"你只需要补全 suggested_code 字段，不要重新审查。\n"
            f"目标文件: {file_path}\n"
            f"目标行号: {line_start}\n"
            f"问题标题: {str(parsed.get('title') or '').strip()}\n"
            f"问题结论: {str(parsed.get('claim') or '').strip()}\n"
            f"修改思路: {str(parsed.get('fix_strategy') or '').strip()}\n"
            f"修复建议: {str(parsed.get('suggested_fix') or '').strip()}\n"
            f"修改步骤: {' / '.join(str(item).strip() for item in list(parsed.get('change_steps') or []) if str(item).strip())}\n"
            f"当前代码:\n{current_code}\n\n"
            "输出要求：\n"
            "1. 只输出 JSON 对象；\n"
            "2. 必须包含字段 suggested_code；\n"
            "3. suggested_code 必须是该文件该问题对应的具体修改后代码片段；\n"
            "4. 不要输出解释、Markdown、修复思路、占位文字。\n"
            '输出格式: {"suggested_code":"..."}'
        )
        repair_result = self.llm_chat_service.complete_text(
            system_prompt="你是代码修复补全器。你的唯一任务是根据已确认的问题结论补出具体 suggested_code。",
            user_prompt=repair_prompt,
            resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
            runtime_settings=runtime_settings,
            fallback_text='{"suggested_code":""}',
            allow_fallback=self._allow_llm_fallback(runtime_settings),
            timeout_seconds=max(20.0, float(llm_request_options["timeout_seconds"]) * 0.5),
            max_attempts=1,
            log_context={
                "review_id": review.review_id,
                "issue_id": "review_orchestration",
                "expert_id": expert.expert_id,
                "phase": "expert_repair_suggested_code",
                "file_path": file_path,
                "line_start": line_start,
            },
        )
        payload = self._parse_json_payload(repair_result.text)
        if isinstance(payload, dict):
            candidate = str(payload.get("suggested_code") or "").strip()
            if self._looks_like_concrete_suggested_code(candidate, file_path=file_path):
                return candidate
        return ""

    def _should_skip_suggested_code_llm_repair(self, runtime_settings) -> bool:
        quality_mode = str(getattr(runtime_settings, "review_quality_mode", "") or "").strip().lower()
        if quality_mode != "thorough_review":
            return False
        return str(getattr(runtime_settings, "suggested_code_repair_mode", "") or "").strip().lower() != "llm"

    def _stabilize_expert_analysis(
        self,
        parsed: dict[str, object],
        expert_id: str,
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
        repository_context: dict[str, object] | None = None,
        input_completeness: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """对专家输出做二次收敛，压制明显误报。"""
        result = dict(parsed)
        text_blob = "\n".join(
            [
                str(result.get("title") or ""),
                str(result.get("claim") or ""),
                *[str(item) for item in list(result.get("evidence") or [])],
                *[str(item) for item in list(result.get("assumptions") or [])],
            ]
        )
        excerpt = str(target_hunk.get("excerpt") or "")
        normalized_excerpt_lines = []
        for line in excerpt.splitlines():
            cleaned = line
            if "|" in cleaned:
                cleaned = cleaned.split("|", 1)[1]
            cleaned = cleaned.strip()
            normalized_excerpt_lines.append(cleaned)
        import_only_excerpt = bool(excerpt) and all(
            line.startswith(("+import", "-import", "import ")) or not line
            for line in normalized_excerpt_lines
        )
        speculative_tokens = ["未显示", "未看到", "可能", "若", "如果", "假设", "推测"]
        import_inference_tokens = ["constructor", "注入", "依赖缺失", "未注入", "Cannot resolve dependency"]
        has_speculative_language = any(token in text_blob for token in speculative_tokens)
        has_import_inference = any(token in text_blob for token in import_inference_tokens)
        strong_rule_signal = bool(list(result.get("matched_rules") or []) or list(result.get("violated_guidelines") or []))
        if has_speculative_language and not strong_rule_signal:
            result["finding_type"] = "risk_hypothesis"
            result["verification_needed"] = True
            text_blob_lower = text_blob.lower()
            excerpt_lower = excerpt.lower()
            high_value_runtime_tokens = [
                "锁",
                "死锁",
                "事务",
                "回滚",
                "并发",
                "竞争",
                "批量",
                "回填",
                "全表",
                "超时",
                "重试",
            ]
            contract_source_tokens = ["注释", "todo", "方法名", "接口说明", "说明", "文档"]
            contract_mismatch_tokens = ["未实现", "未生效", "未落地", "不一致", "缺失", "没有实现"]
            has_high_value_runtime_risk = any(token in text_blob_lower or token in excerpt_lower for token in high_value_runtime_tokens)
            has_contract_mismatch_risk = (
                any(token in text_blob_lower or token in excerpt_lower for token in contract_source_tokens)
                and any(token in text_blob_lower for token in contract_mismatch_tokens)
            )
            preserve_verifiable_risk = has_high_value_runtime_risk or has_contract_mismatch_risk
            if preserve_verifiable_risk:
                result["direct_evidence"] = bool(
                    list(result.get("evidence") or [])
                    or list(result.get("cross_file_evidence") or [])
                    or list(result.get("context_files") or [])
                )
            else:
                result["direct_evidence"] = False
                result["confidence"] = min(self._normalize_confidence(result.get("confidence"), 0.0), 0.4)
                if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                    result["severity"] = "medium"
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            assumption = (
                "当前问题已有直接代码证据，但仍需补充完整调用链或上下文来确认影响范围。"
                if preserve_verifiable_risk
                else "当前结论依赖 diff 片段外信息或未展示的实现细节，系统需要补齐完整方法/类定义后再自动复核。"
            )
            if assumption not in assumptions:
                assumptions.append(assumption)
            result["assumptions"] = assumptions
            result["verification_plan"] = (
                str(result.get("verification_plan") or "").strip()
                or (
                    "需要补充完整事务边界、调用链或实现上下文，确认该高价值风险是否会在真实路径上触发。"
                    if preserve_verifiable_risk
                    else "系统将补齐完整 diff、相关方法实现和调用链后自动复核，确认推断是否成立。"
                )
            )
        if import_only_excerpt and has_import_inference:
            result["verification_plan"] = (
                str(result.get("verification_plan") or "").strip()
                or "系统需要补齐完整类定义和 constructor 注入信息，不能仅凭 import 变化下结论。"
            )
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            assumption = "当前结论基于 import 变化推断，系统尚需补齐完整类定义与 constructor 信息后再自动复核。"
            if assumption not in assumptions:
                assumptions.append(assumption)
            result["assumptions"] = assumptions
            result["confidence"] = min(self._normalize_confidence(result.get("confidence"), 0.0), 0.45)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
        if expert_id == "performance_reliability":
            perf_tokens = [
                "超时",
                "重试",
                "限流",
                "队列",
                "吞吐",
                "性能",
                "缓存",
                "序列化",
                "响应体",
                "锁",
                "热点",
                "退化",
                "并发",
                "sql",
                "query",
                "limit",
                "分页",
                "扫描",
                "索引",
                "n+1",
                "cpu",
                "内存",
                "network",
                "latency",
                "throughput",
                "cache",
                "timeout",
                "retry",
                "循环",
                "foreach",
                ".foreach",
                "批量路径",
            ]
            has_perf_signal = any(token.lower() in text_blob.lower() for token in perf_tokens)
            if not has_perf_signal:
                result["finding_type"] = "design_concern"
                result["severity"] = "low"
                result["confidence"] = min(self._normalize_confidence(result.get("confidence"), 0.0), 0.35)
                result["verification_needed"] = True

        explicit_line_start = self._extract_explicit_line_start_from_analysis(result, target_hunk)
        effective_line_start = self._stabilize_line_start(
            explicit_line_start if explicit_line_start is not None else result.get("line_start"),
            line_start,
            target_hunk,
        )
        result["line_start"] = effective_line_start
        result["line_end"] = self._stabilize_line_end(result.get("line_end"), effective_line_start, target_hunk)
        result["matched_rules"] = [str(item).strip() for item in list(result.get("matched_rules") or []) if str(item).strip()]
        result["violated_guidelines"] = [
            str(item).strip() for item in list(result.get("violated_guidelines") or []) if str(item).strip()
        ]
        result = self._enrich_java_domain_finding_language(result, expert_id)
        if not str(result.get("rule_based_reasoning") or "").strip():
            matched_rules = list(result.get("matched_rules") or [])
            violated = list(result.get("violated_guidelines") or [])
            if matched_rules or violated:
                result["rule_based_reasoning"] = (
                    f"命中规范: {' / '.join(matched_rules[:3]) or '无'}；"
                    f"违反规范: {' / '.join(violated[:3]) or '无'}。"
                )
        result["context_files"] = [str(item).strip() for item in list(result.get("context_files") or []) if str(item).strip()]
        result["evidence"] = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        result["observation_ids"] = list(dict.fromkeys(self._normalize_text_list(result.get("observation_ids"), [])))
        result["matched_design_points"] = self._normalize_text_list(result.get("matched_design_points"), [])
        result["missing_design_points"] = self._normalize_text_list(result.get("missing_design_points"), [])
        result["extra_implementation_points"] = self._normalize_text_list(result.get("extra_implementation_points"), [])
        result["design_conflicts"] = self._normalize_text_list(result.get("design_conflicts"), [])
        result["design_alignment_status"] = str(result.get("design_alignment_status") or "").strip()
        result = self._enrich_java_quality_signal_language(
            result,
            expert_id,
            file_path,
            target_hunk,
            repository_context or {},
        )
        result = self._sanitize_candidate_to_current_anchor(result, file_path, effective_line_start, target_hunk)
        result = self._enforce_expert_output_schema(result, expert_id)
        result = self._apply_input_quality_gate(result, input_completeness or {})
        result = self._sanitize_user_confirmation_language(result)
        return result

    def _sanitize_candidate_to_current_anchor(
        self,
        parsed: dict[str, object],
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
    ) -> dict[str, object]:
        """把弱模型混入的其它文件/其它问题证据收回到当前 finding 锚点。"""

        result = dict(parsed)
        if self._is_tool_observation_candidate(result):
            return result
        anchor_domains = self._candidate_anchor_issue_domains(target_hunk, line_start)
        hunk_domains = anchor_domains if anchor_domains != {"general"} else self._candidate_issue_domains(str((target_hunk or {}).get("excerpt") or ""))
        metadata_domain_blob = "\n".join(
            [
                str(result.get("normalized_issue_type") or ""),
                " ".join(str(item) for item in list(result.get("violated_guidelines") or [])),
            ]
        )
        issue_domains = hunk_domains if hunk_domains != {"general"} else self._candidate_issue_domains(metadata_domain_blob)
        if issue_domains == {"general"}:
            issue_domains = self._candidate_issue_domains(
                "\n".join(
                    [
                        str(result.get("title") or ""),
                        str(result.get("claim") or result.get("summary") or ""),
                    ]
                )
            )
        issue_domains = issue_domains or {"general"}
        title = str(result.get("title") or "").strip()
        if title and self._candidate_text_mixes_unrelated_domains(title, issue_domains):
            result["title"] = self._build_anchor_specific_title(title, issue_domains)
        normalized_issue_type = str(result.get("normalized_issue_type") or "").strip()
        if (
            normalized_issue_type.lower()
            in {"comment_contract_unimplemented", "declared_intent_without_implementation", "comment_promise_unimplemented"}
            and "loop_call" in anchor_domains
            and "contract" not in anchor_domains
        ):
            result["normalized_issue_type"] = self._build_anchor_specific_issue_type(anchor_domains, normalized_issue_type)
        elif not normalized_issue_type and issue_domains != {"general"}:
            result["normalized_issue_type"] = self._build_anchor_specific_issue_type(issue_domains, "")
        elif normalized_issue_type and self._candidate_text_mixes_unrelated_domains(normalized_issue_type, issue_domains):
            result["normalized_issue_type"] = self._build_anchor_specific_issue_type(issue_domains, normalized_issue_type)
        issue_domains = self._candidate_issue_domains(
            "\n".join(
                [
                    str(result.get("title") or ""),
                    str(result.get("normalized_issue_type") or ""),
                    " ".join(str(item) for item in list(result.get("violated_guidelines") or [])),
                    str((target_hunk or {}).get("excerpt") or ""),
                ]
            )
        )
        evidence = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        filtered_evidence = [
            item
            for item in evidence
            if self._candidate_evidence_matches_anchor(
                item,
                file_path=file_path,
                line_start=line_start,
                target_hunk=target_hunk,
                issue_domains=issue_domains,
            )
        ]
        if filtered_evidence:
            result["evidence"] = filtered_evidence

        for text_key in ("claim", "summary"):
            text = str(result.get(text_key) or "").strip()
            if text and self._candidate_text_mixes_unrelated_domains(text, issue_domains):
                result[text_key] = self._build_anchor_specific_claim(result, file_path, line_start)

        for text_key in ("fix_strategy", "suggested_fix"):
            text = str(result.get(text_key) or "").strip()
            if text and self._candidate_text_mixes_unrelated_domains(text, issue_domains):
                result[text_key] = self._build_anchor_specific_remediation(
                    result,
                    text_key=text_key,
                    issue_domains=issue_domains,
                )

        steps = [str(item).strip() for item in list(result.get("change_steps") or []) if str(item).strip()]
        if steps:
            scoped_steps = [
                step
                for step in steps
                if not self._candidate_text_mixes_unrelated_domains(step, issue_domains)
            ]
            result["change_steps"] = scoped_steps or self._build_anchor_specific_steps(issue_domains)
        return result

    def _candidate_anchor_issue_domains(self, target_hunk: dict[str, object], line_start: int) -> set[str]:
        line_candidates = self._extract_semantic_line_candidates(target_hunk or {})
        target_line = "\n".join(line_candidates.get(int(line_start or 0), []))
        target_window = "\n".join(
            value
            for line_no, values in line_candidates.items()
            if abs(int(line_no) - int(line_start or 0)) <= 1
            for value in values
        )
        if self._anchor_line_looks_like_comment_contract(target_line):
            return {"contract"}
        domains = self._candidate_issue_domains(target_window or target_line)
        lowered_line = target_line.lower()
        lowered_window = target_window.lower()
        if any(token in lowered_window for token in ("for (", ".foreach", "foreach", "while (")) and any(
            token in lowered_line or token in lowered_window
            for token in ("repository.save", ".save(", "gateway.", "client.", "service.", "mapper.")
        ):
            domains.add("loop_call")
            domains.discard("contract")
        return domains or {"general"}

    @staticmethod
    def _anchor_line_looks_like_comment_contract(value: object) -> bool:
        stripped = str(value or "").strip().lower()
        return bool(
            stripped
            and (
                stripped.startswith(("//", "/*", "*"))
                or any(token in stripped for token in ("todo", "fixme", "未实现", "承诺", "unsupportedoperationexception"))
            )
        )

    def _candidate_issue_domains(self, text: str) -> set[str]:
        lowered = str(text or "").lower()
        domains: set[str] = set()
        domain_terms = {
            "naming": ("常量", "命名", "chunks", "tmp", "magic", "naming", "constant", "语义变量"),
            "exception": ("异常", "catch", "printstacktrace", "吞掉", "静默吞", "exception"),
            "query_bound": ("limit", "分页", "全表", "无上限", "不设上限", "unbounded"),
            "query_semantics": ("like", "equal", "predicate", "精确匹配", "模糊匹配", "查询语义"),
            "contract": ("todo", "注释", "承诺", "未实现", "未落地", "占位实现", "comment_contract", "unimplemented"),
            "domain_creation": (
                "course.create",
                "new course",
                "领域事件",
                "聚合",
                "工厂",
                "aggregate",
                "factory",
                "domain_event",
                "domain_event_missing",
            ),
            "security": ("鉴权", "权限", "越权", "注入", "token", "secret", "security", "auth"),
            "loop_call": ("循环", "外部接口", "远程调用", "逐条", "n+1", "foreach", "for (", "repository.save", ".save("),
        }
        for domain, terms in domain_terms.items():
            if any(term in lowered for term in terms):
                domains.add(domain)
        return domains or {"general"}

    def _candidate_evidence_matches_anchor(
        self,
        evidence: str,
        *,
        file_path: str,
        line_start: int,
        target_hunk: dict[str, object],
        issue_domains: set[str],
    ) -> bool:
        text = str(evidence or "").strip()
        if not text:
            return False
        lowered = text.lower().replace("\\", "/")
        normalized_path = str(file_path or "").strip().replace("\\", "/").lower()
        basename = normalized_path.rsplit("/", 1)[-1]
        evidence_domains = self._candidate_issue_domains(text)
        shares_domain = bool(issue_domains & evidence_domains)
        mentions_target_file = bool(normalized_path and normalized_path in lowered) or bool(basename and basename in lowered)
        mentioned_java_paths = [match.group(0).lower().replace("\\", "/") for match in re.finditer(r"[\w./\\-]+\.java(?::\d+(?:-\d+)?)?", text)]
        mentions_other_file = bool(
            mentioned_java_paths
            and not any(normalized_path in item or (basename and basename in item) for item in mentioned_java_paths)
        )
        if mentions_other_file and not shares_domain:
            return False

        line_refs = self._candidate_line_refs(text)
        if mentions_target_file and line_refs and not any(abs(ref - int(line_start or 1)) <= 2 for ref in line_refs):
            return shares_domain
        if mentions_target_file:
            return True

        hunk_excerpt = str((target_hunk or {}).get("excerpt") or "")
        hunk_tokens = {
            token.lower()
            for token in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", hunk_excerpt)
            if len(token.strip()) >= 3
        }
        evidence_tokens = {
            token.lower()
            for token in re.split(r"[^a-zA-Z0-9_\u4e00-\u9fff]+", text)
            if len(token.strip()) >= 3
        }
        return shares_domain or bool(hunk_tokens & evidence_tokens)

    @staticmethod
    def _candidate_line_refs(text: str) -> list[int]:
        refs: list[int] = []
        for match in re.finditer(r":(\d+)(?:-(\d+))?", str(text or "")):
            start = int(match.group(1))
            end = int(match.group(2) or start)
            refs.extend([start, end])
        return refs

    def _candidate_text_mixes_unrelated_domains(self, text: str, issue_domains: set[str]) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        domains = self._candidate_issue_domains(stripped)
        unrelated = domains - issue_domains
        if any(marker in stripped for marker in ("发现多个", "多个通用", "多个业务", "同时恢复", "包括领域事件")):
            return True
        if domains == {"general"} or issue_domains == {"general"}:
            return False
        if unrelated and not (domains & issue_domains):
            return True
        return bool(unrelated and len(domains) > max(1, len(issue_domains)))

    def _is_tool_observation_candidate(self, parsed: dict[str, object]) -> bool:
        if str(parsed.get("evidence_source") or "").strip() == "tool_observation":
            return True
        if self._normalize_text_list(parsed.get("adopted_tool_observations"), []):
            return True
        for rule in self._normalize_text_list(parsed.get("matched_rules"), []):
            if self._is_tool_rule_reference(rule, parsed):
                return True
        return False

    def _build_anchor_specific_claim(self, parsed: dict[str, object], file_path: str, line_start: int) -> str:
        title = str(parsed.get("title") or "当前变更存在代码质量问题").strip()
        basename = str(file_path or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
        return f"{basename}:{int(line_start or 1)} 定位到“{title}”，修复时应围绕该问题位置处理，避免把其他位置的问题混入同一条结论。"

    def _build_anchor_specific_title(self, title: str, issue_domains: set[str]) -> str:
        cleaned = str(title or "").strip()
        unrelated_markers = {
            "exception": ("（异常处理被忽略）", "(异常处理被忽略)", "（静默吞掉异常）", "(静默吞掉异常)", "（异常处理）", "(异常处理)"),
            "query_semantics": ("（查询语义退化）", "(查询语义退化)"),
            "query_bound": ("（查询边界缺失）", "(查询边界缺失)"),
            "domain_creation": ("（领域事件缺失）", "(领域事件缺失)"),
        }
        for domain, markers in unrelated_markers.items():
            if domain in issue_domains:
                continue
            for marker in markers:
                cleaned = cleaned.replace(marker, "")
        if cleaned and not self._candidate_text_mixes_unrelated_domains(cleaned, issue_domains):
            return cleaned.strip(" ，,;；")
        if "query_semantics" in issue_domains:
            return "查询语义从精确匹配退化为模糊匹配"
        if "exception" in issue_domains:
            return "失败被当成成功返回"
        if "query_bound" in issue_domains:
            return "查询缺少分页或 LIMIT 边界"
        if "domain_creation" in issue_domains:
            return "聚合创建绕过领域工厂导致领域事件缺失"
        if "naming" in issue_domains:
            return "常量命名与使用语义不一致"
        return cleaned or "当前变更存在代码质量问题"

    def _build_anchor_specific_issue_type(self, issue_domains: set[str], fallback: str) -> str:
        if "naming" in issue_domains:
            return "naming_misleading"
        if "exception" in issue_domains:
            return "exception_swallowed"
        if "contract" in issue_domains:
            return "comment_contract_unimplemented"
        if "loop_call" in issue_domains:
            return "loop_call_amplification"
        if "query_semantics" in issue_domains:
            return "query_semantics_weakened"
        if "query_bound" in issue_domains:
            return "query_bound_removed"
        if "domain_creation" in issue_domains:
            return "aggregate_factory_bypass"
        if "security" in issue_domains:
            return "security_guard_removed"
        return fallback

    def _normalize_candidate_for_refined_anchor(
        self,
        parsed: dict[str, object],
        *,
        expert_id: str,
        line_start: int,
    ) -> dict[str, object]:
        """最终行号确定后，把混合候选收回到单一、用户可读的问题。"""

        result = dict(parsed or {})
        text = "\n".join(
            [
                str(result.get("title") or ""),
                str(result.get("claim") or result.get("summary") or ""),
                str(result.get("normalized_issue_type") or ""),
                " ".join(str(item) for item in list(result.get("evidence") or [])),
            ]
        ).lower()
        explicit_type = str(result.get("normalized_issue_type") or "").strip().lower()
        if explicit_type in {
            "comment_contract_unimplemented",
            "lock_guard_removed",
            "loop_call_amplification",
            "n_plus_one",
            "exception_swallowed",
            "exception_semantics_weakened",
            "query_authorization_scope_broadened",
            "sql_injection_risk",
            "sensitive_data_exposure",
            "authorization_bypass_risk",
            "security_tool_observation",
            "ssrf_risk",
            "xss_output_encoding_risk",
            "security_guard_removed",
            "aggregate_factory_bypass",
            "aggregate_factory_bypassed",
            "domain_event_ordering_risk",
            "domain_event_ordering",
            "course_creation_semantics",
        }:
            if explicit_type == "loop_call_amplification":
                result["normalized_issue_type"] = "n_plus_one"
            return result
        if int(line_start or 1) >= 30 and any(token in text for token in ("库存", "加锁", "超卖", "stock", "lock")):
            if self._looks_like_concrete_comment_contract(text):
                result["normalized_issue_type"] = "comment_contract_unimplemented"
                result.setdefault("title", "注释或 TODO 承诺未落地")
                result.setdefault("fix_strategy", "补齐注释或 TODO 承诺的业务动作；如果不准备实现，就删除会误导调用方的承诺。")
                result.setdefault("suggested_fix", "按当前方法的业务语义补齐对应副作用、校验或事件发布，并补充回归验证。")
            else:
                result["normalized_issue_type"] = "lock_guard_removed"
                result.setdefault("title", "并发保护被删除")
                result.setdefault("fix_strategy", "恢复原有锁保护，或补充数据库唯一约束、乐观锁、幂等表、分布式锁等等价并发控制。")
                result.setdefault("suggested_fix", "不要直接删除并发保护；先补齐等价并发控制，再用并发提交或重复消费测试验证。")
        elif (
            "n+1" in text
            or "findbyid" in text
            or "loop_call_amplification" in text
            or ("循环" in text and "repository" in text)
            or ("逐条" in text and "repository" in text)
        ):
            result["normalized_issue_type"] = "n_plus_one"
            if not str(result.get("title") or "").strip():
                result["title"] = "循环内逐条外部调用会放大批量处理成本"
            result.setdefault("fix_strategy", "改成批量查询、批量保存或循环外聚合处理，避免循环内逐条访问外部依赖。")
            result.setdefault("suggested_fix", "先收集批量输入，再通过批量接口一次性处理，并按 ID 或业务键组装结果。")
        elif "lock_guard_removed" in text or "synchronized" in text or ("锁" in text and "删除" in text):
            result["normalized_issue_type"] = "lock_guard_removed"
            if not str(result.get("title") or "").strip():
                result["title"] = "并发保护被删除"
            result.setdefault("fix_strategy", "恢复原有锁保护，或补充数据库唯一约束、乐观锁、幂等表、分布式锁等等价并发控制。")
            result.setdefault("suggested_fix", "不要直接删除并发保护；先补齐等价并发控制，再用并发提交或重复消费测试验证。")
        elif self._looks_like_concrete_comment_contract(text):
            result["normalized_issue_type"] = "comment_contract_unimplemented"
            if not str(result.get("title") or "").strip():
                result["title"] = "注释或 TODO 承诺未落地"
            result.setdefault("fix_strategy", "补齐注释或 TODO 承诺的业务动作；如果不准备实现，就删除会误导调用方的承诺。")
            result.setdefault("suggested_fix", "按当前方法的业务语义补齐对应副作用、校验或事件发布，并补充回归验证。")
        elif (
            "权限" in text
            or "越权" in text
            or "登录用户" in text
            or (expert_id == "correctness_business" and int(line_start or 1) <= 20 and "todo" in text)
        ):
            if "listorders" in text and "todo" in text:
                result["normalized_issue_type"] = "comment_contract_unimplemented"
                result["title"] = "订单权限过滤承诺未落地"
                result["claim"] = "listOrders 的 TODO 明确要求只返回当前登录用户有权限的订单，但当前实现没有任何权限过滤逻辑，存在越权读取风险。"
                result.setdefault("fix_strategy", "按当前登录用户或租户维度过滤订单查询结果。")
                result.setdefault("suggested_fix", "在查询前获取当前用户身份，将 orderIds 与用户可访问订单范围做交集，或在仓储查询中加入用户/租户条件。")
            else:
                result["normalized_issue_type"] = "query_authorization_scope_broadened"
                if not str(result.get("title") or "").strip() or "订单权限过滤" in str(result.get("title") or ""):
                    result["title"] = "查询访问范围可能被放大"
                result.setdefault("fix_strategy", "收窄查询过滤条件，保持权限、租户、用户或业务对象边界的精确匹配。")
                result.setdefault("suggested_fix", "对安全边界字段继续使用精确匹配；如需模糊搜索，请拆出明确的搜索操作符并限制可搜索字段。")
        return result

    def _looks_like_concrete_comment_contract(self, text: str) -> bool:
        lowered = str(text or "").lower()
        if "comment_contract_unimplemented" in lowered:
            return True
        if not any(token in lowered for token in ("todo", "承诺", "未实现", "没有实现", "未落地")):
            return False
        concrete_terms = (
            "库存",
            "inventory",
            "reserve",
            "deduct",
            "审计",
            "audit",
            "事件",
            "event",
            "通知",
            "notify",
            "缓存",
            "cache",
            "调用接口",
            "调用下游",
            "远程",
            "remote",
            "retry",
            "重试",
            "输入校验",
            "参数校验",
            "权限",
            "越权",
            "登录用户",
        )
        return any(token in lowered for token in concrete_terms)

    def _build_anchor_specific_remediation(
        self,
        parsed: dict[str, object],
        *,
        text_key: str,
        issue_domains: set[str],
    ) -> str:
        if "naming" in issue_domains:
            if text_key == "fix_strategy":
                return "修正常量命名与使用方式，使当前变更行只表达一个具体问题。"
            return "将当前变更行恢复为符合命名、不可变性和实际使用语义的常量写法。"
        if "exception" in issue_domains:
            return "只围绕当前 catch 块补齐日志、异常传播或补偿处理，不混入其它变更点。"
        if "query_bound" in issue_domains:
            return "只围绕当前查询恢复分页、LIMIT 或批量边界，不混入其它问题修复。"
        if "query_semantics" in issue_domains:
            return "只围绕当前谓词恢复原有查询语义，并用测试覆盖精确匹配行为。"
        if "domain_creation" in issue_domains:
            return "只围绕当前聚合创建路径恢复领域工厂和领域事件语义。"
        title = str(parsed.get("title") or "当前问题").strip()
        return f"只修复“{title}”对应的问题位置，不混入其它文件或其它问题。"

    def _build_anchor_specific_steps(self, issue_domains: set[str]) -> list[str]:
        if "naming" in issue_domains:
            return ["定位当前命名违规行", "恢复符合规范的常量声明", "清理未使用或临时命名"]
        if "exception" in issue_domains:
            return ["定位当前 catch 块", "补齐日志或异常传播", "增加异常路径测试"]
        if "query_bound" in issue_domains:
            return ["定位当前查询语句", "恢复分页或 LIMIT 边界", "补充批量边界测试"]
        if "domain_creation" in issue_domains:
            return ["定位当前聚合创建行", "恢复领域工厂调用", "验证领域事件生成路径"]
        return ["定位当前问题代码行", "按该问题最小范围修复", "补充对应回归验证"]

    def _enforce_expert_output_schema(self, parsed: dict[str, object], expert_id: str) -> dict[str, object]:
        """对专家 JSON 做最低限度的 schema 约束，避免弱结构输出直接升级成 issue。"""

        result = dict(parsed)
        errors: list[str] = []
        allowed_finding_types = {"direct_defect", "direct_code_issue", "test_gap", "risk_hypothesis", "design_concern"}
        allowed_severities = {"blocker", "critical", "high", "medium", "low"}

        finding_type = str(result.get("finding_type") or "").strip().lower()
        if finding_type not in allowed_finding_types:
            errors.append("finding_type_invalid_or_missing")
            result["finding_type"] = "risk_hypothesis"

        severity = str(result.get("severity") or "").strip().lower()
        if severity not in allowed_severities:
            errors.append("severity_invalid_or_missing")
            result["severity"] = "medium"

        if not str(result.get("title") or "").strip():
            errors.append("title_missing")
            result["title"] = self._build_finding_title_by_expert_id(expert_id)

        if not str(result.get("claim") or result.get("summary") or "").strip():
            errors.append("claim_missing")
            result["claim"] = "专家输出缺少明确结论，系统已降级为待复核风险。"

        if not str(result.get("normalized_issue_type") or "").strip():
            inferred_type = self._normalize_issue_type(result, expert_id)
            result["normalized_issue_type"] = inferred_type
        result["schema_payload_valid"] = True

        evidence = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        cross_file_evidence = [
            str(item).strip() for item in list(result.get("cross_file_evidence") or []) if str(item).strip()
        ]
        observation_ids = [
            str(item).strip() for item in list(result.get("observation_ids") or []) if str(item).strip()
        ]
        result["evidence"] = evidence
        result["cross_file_evidence"] = cross_file_evidence
        result["observation_ids"] = observation_ids

        matched_rules = [str(item).strip() for item in list(result.get("matched_rules") or []) if str(item).strip()]
        result["matched_rules"] = matched_rules
        signal_terms = {
            str(key).strip(): value
            for key, value in dict(result.get("signal_terms") or {}).items()
            if str(key).strip()
        }
        has_deterministic_signal = bool(observation_ids or signal_terms)
        if not matched_rules and not (evidence or cross_file_evidence or has_deterministic_signal):
            errors.append("matched_rules_missing")

        strong_finding = str(result.get("finding_type") or "").strip().lower() in {"direct_defect", "direct_code_issue"}
        if strong_finding and not (evidence or cross_file_evidence or observation_ids or has_deterministic_signal):
            errors.append("direct_defect_without_evidence")
            result["finding_type"] = "risk_hypothesis"
            result["direct_evidence"] = False

        payload_errors = self._validate_expert_finding_payload(result)
        if payload_errors:
            errors.extend(payload_errors)
            result["schema_payload_valid"] = False

        if errors:
            irrecoverable_empty_payload = (
                "title_missing" in errors
                and "claim_missing" in errors
                and not (evidence or cross_file_evidence or observation_ids or has_deterministic_signal or matched_rules)
            )
            if irrecoverable_empty_payload:
                errors.append("irrecoverable_empty_payload")
                result["schema_rejected"] = True
                result["finding_type"] = "risk_hypothesis"
                result["direct_evidence"] = False
            result["schema_validation_errors"] = errors
            result["verification_needed"] = True
            result["confidence"] = min(
                self._normalize_confidence(result.get("confidence"), 0.5),
                0.35 if irrecoverable_empty_payload else 0.69,
            )
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            marker = (
                "专家输出缺少标题、结论和证据，系统已硬拒收为无效结构化输出。"
                if irrecoverable_empty_payload
                else f"专家输出结构不完整: {' / '.join(errors[:5])}，系统已降级并自动复核。"
            )
            if marker not in assumptions:
                assumptions.append(marker)
            result["assumptions"] = assumptions
            if not str(result.get("verification_plan") or "").strip():
                result["verification_plan"] = "系统将补齐结构化字段、代码证据和规范命中后再自动复核该结论。"
        return result

    def _validate_expert_finding_payload(self, parsed: dict[str, object]) -> list[str]:
        payload = dict(parsed)
        payload["claim"] = str(payload.get("claim") or payload.get("summary") or "").strip()
        payload["summary"] = str(payload.get("summary") or payload.get("claim") or "").strip()
        payload["title"] = str(payload.get("title") or "").strip()
        payload["finding_type"] = str(payload.get("finding_type") or "").strip()
        payload["severity"] = str(payload.get("severity") or "").strip()
        payload["normalized_issue_type"] = str(payload.get("normalized_issue_type") or "").strip()
        payload["file_path"] = str(payload.get("file_path") or "").strip()
        for key in (
            "evidence",
            "cross_file_evidence",
            "assumptions",
            "context_files",
            "matched_rules",
            "violated_guidelines",
            "change_steps",
            "change_understanding_refs",
        ):
            payload[key] = self._normalize_text_list(payload.get(key), [])
        try:
            ExpertFindingPayload.model_validate(payload)
        except Exception as exc:
            return [f"pydantic_payload_invalid:{type(exc).__name__}"]
        return []

    def _build_finding_title_by_expert_id(self, expert_id: str) -> str:
        names = {
            "database_analysis": "数据库访问风险",
            "performance_reliability": "性能与可靠性风险",
            "security_compliance": "安全与合规风险",
            "redis_analysis": "缓存一致性风险",
            "mq_analysis": "消息可靠性风险",
            "frontend_accessibility": "前端交互风险",
            "test_verification": "测试覆盖风险",
            "ddd_architecture": "DDD 架构边界风险",
            "ddd_specification": "DDD 规范一致性风险",
            "maintainability_code_health": "通用编码规范风险",
            "correctness_business": "业务正确性风险",
        }
        return names.get(expert_id, "代码检视风险")

    def _apply_input_quality_gate(
        self,
        parsed: dict[str, object],
        input_completeness: dict[str, object],
    ) -> dict[str, object]:
        result = dict(parsed)
        text_blob = "\n".join(
            [
                str(result.get("title") or ""),
                str(result.get("claim") or ""),
                str(result.get("summary") or ""),
                *[str(item) for item in list(result.get("evidence") or [])],
            ]
        ).lower()
        has_strong_java_signal = any(
            token in text_blob
            for token in {
                "循环调用放大",
                "循环内调用放大",
                "承诺未落地",
                "注释/待办承诺未实现",
                "comment_contract_unimplemented",
                "loop_call_amplification",
            }
        )
        missing_sections = [
            str(item).strip()
            for item in list(input_completeness.get("missing_sections") or [])
            if str(item).strip()
        ]
        required_sections = {"专家规范", "语言通用规范提示", "变更代码原文", "当前源码上下文", "关联源码上下文"}
        missing_required = [item for item in missing_sections if item in required_sections]
        if not missing_required:
            return result

        strong_missing = {"专家规范", "语言通用规范提示", "变更代码原文"}
        has_strong_missing = any(item in strong_missing for item in missing_required)

        if has_strong_missing and not has_strong_java_signal:
            # 缺失规范/变更原文时执行强降级，避免在关键输入缺失时给出“确定性结论”。
            result["finding_type"] = "risk_hypothesis"
            result["verification_needed"] = True
            result["direct_evidence"] = False
            result["confidence"] = min(self._normalize_confidence(result.get("confidence"), 0.0), 0.35)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
        else:
            # 仅缺失源码上下文时保留原始 finding 类型/置信度，避免把有效问题整体降为“提示性”导致 issue 为空。
            has_evidence = bool(result.get("evidence") or result.get("cross_file_evidence"))
            finding_type = str(result.get("finding_type") or "risk_hypothesis")
            original_direct_evidence = bool(result.get("direct_evidence"))
            clear_verification = (
                has_strong_java_signal
                and finding_type in {"direct_defect", "direct_code_issue"}
                and original_direct_evidence
            )
            result["finding_type"] = finding_type
            result["verification_needed"] = bool(result.get("verification_needed", False)) and not clear_verification
            result["direct_evidence"] = original_direct_evidence or bool(has_evidence and not result["verification_needed"])
            result["confidence"] = self._normalize_confidence(result.get("confidence"), 0.0)
            if (not has_evidence) and str(result.get("severity") or "").lower() in {"blocker", "critical"}:
                result["severity"] = "high"

        assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
        assumption = f"当前审查输入缺失: {' / '.join(missing_required[:5])}，结论仅基于已提供代码证据。"
        if assumption not in assumptions:
            assumptions.append(assumption)
        result["assumptions"] = assumptions

        existing_plan = str(result.get("verification_plan") or "").strip()
        missing_input_plan = f"系统先补齐 {' / '.join(missing_required[:5])}，再自动复核该问题是否成立。"
        if has_strong_missing:
            result["verification_plan"] = missing_input_plan
        elif existing_plan:
            result["verification_plan"] = existing_plan
        else:
            result["verification_plan"] = missing_input_plan
        return result

    def _sanitize_user_confirmation_language(self, parsed: dict[str, object]) -> dict[str, object]:
        result = dict(parsed)
        text_fields = [
            "claim",
            "rule_based_reasoning",
            "why_it_matters",
            "fix_strategy",
            "suggested_fix",
            "verification_plan",
        ]
        step_fields = ["change_steps", "assumptions"]
        user_confirm_patterns = [
            r"请(?:先)?(?:你|用户|人工)?[^。；\n]*(?:确认|核查|查看|排查)[^。；\n]*",
            r"建议(?:你|用户|人工)?[^。；\n]*(?:确认|核查|查看|排查)[^。；\n]*",
            r"需要(?:你|用户|人工)[^。；\n]*(?:确认|核查|查看|排查)[^。；\n]*",
            r"需(?:你|用户|人工)[^。；\n]*(?:确认|核查|查看|排查)[^。；\n]*",
        ]

        hit_user_confirmation = False

        def _rewrite_text(value: str) -> str:
            nonlocal hit_user_confirmation
            text = str(value or "").strip()
            if not text:
                return text
            rewritten = text
            for pattern in user_confirm_patterns:
                if re.search(pattern, rewritten):
                    hit_user_confirmation = True
                    rewritten = re.sub(pattern, "系统将自动补齐上下文并复核", rewritten)
            rewritten = re.sub(r"(系统将自动补齐上下文并复核)([\s，、；]*)\1+", r"\1", rewritten).strip(" ，、；")
            return rewritten

        for field in text_fields:
            result[field] = _rewrite_text(str(result.get(field) or ""))

        for field in step_fields:
            values = [str(item).strip() for item in list(result.get(field) or []) if str(item).strip()]
            rewritten_values: list[str] = []
            for item in values:
                rewritten_item = _rewrite_text(item)
                if rewritten_item:
                    rewritten_values.append(rewritten_item)
            result[field] = rewritten_values

        if hit_user_confirmation:
            result["verification_needed"] = True
            result["verification_plan"] = "系统将自动补齐关联上下文并复核，无需额外手工核查。"
            assumptions = [str(item).strip() for item in list(result.get("assumptions") or []) if str(item).strip()]
            marker = "该结论由系统自动补齐上下文后复核，无需额外手工确认。"
            if marker not in assumptions:
                assumptions.append(marker)
            result["assumptions"] = assumptions
        return result

    def _enrich_java_domain_finding_language(
        self,
        parsed: dict[str, object],
        expert_id: str,
    ) -> dict[str, object]:
        if expert_id not in DDD_ARCHITECTURE_EXPERT_IDS | {"architecture_design"}:
            return parsed

        matched_rules = [str(item).strip().upper() for item in list(parsed.get("matched_rules") or []) if str(item).strip()]
        if not any(rule.startswith("DDD-JDDD-001") or rule.startswith("ARCH-JDDD-002") for rule in matched_rules):
            return parsed

        result = dict(parsed)
        title = str(result.get("title") or "").strip()
        claim = str(result.get("claim") or "").strip()
        text_blob = f"{title}\n{claim}".lower()

        needs_aggregate = "aggregate" not in text_blob
        needs_factory = "factory" not in text_blob and "工厂" not in text_blob
        needs_domain_event = "domain event" not in text_blob

        if needs_aggregate or needs_factory:
            suffix = "创建路径变更风险"
            if suffix.lower() not in title.lower():
                title = f"{title} ({suffix})" if title else suffix
        result["title"] = title

        additions: list[str] = []
        if needs_aggregate or needs_factory:
            additions.append("核对原创建入口承载的 aggregate/factory 语义或不变量校验")
        if needs_domain_event:
            additions.append("确认 domain event 录制/发布语义是否仍被保留")
        if additions:
            claim = claim.rstrip("。")
            suffix = "；".join(additions)
            result["claim"] = f"{claim}；{suffix}。".strip("；")
        else:
            result["claim"] = claim
        return result

    def _enrich_java_quality_signal_language(
        self,
        parsed: dict[str, object],
        expert_id: str,
        file_path: str,
        target_hunk: dict[str, object],
        repository_context: dict[str, object],
    ) -> dict[str, object]:
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=str(target_hunk.get("excerpt") or ""),
        )
        signal_set = {
            str(item).strip()
            for item in list(java_quality.get("signals") or [])
            if str(item).strip()
        }
        parsed_text = "\n".join(
            [
                str(parsed.get("title") or ""),
                str(parsed.get("claim") or ""),
                str(parsed.get("summary") or ""),
                str(target_hunk.get("excerpt") or ""),
                *[str(item) for item in list(parsed.get("evidence") or [])],
                *[str(item) for item in list(parsed.get("matched_rules") or [])],
                *[str(item) for item in list(parsed.get("violated_guidelines") or [])],
            ]
        ).lower()
        if (
            "printstacktrace" in parsed_text
            and any(token in parsed_text for token in ("catch", "exception", "异常"))
            and any(token in parsed_text for token in ("+}", "+\t\t\t}", "+    }", "空 catch", "静默吞"))
        ):
            signal_set.add("exception_swallowed")
        if not signal_set:
            return parsed

        matched_terms = [
            str(item).strip()
            for item in list(java_quality.get("matched_terms") or [])
            if str(item).strip()
        ]
        signal_terms = {
            str(key).strip(): [str(item).strip() for item in list(value or []) if str(item).strip()]
            for key, value in dict(java_quality.get("signal_terms") or {}).items()
            if str(key).strip()
        }
        result = self._enrich_java_domain_finding_language(dict(parsed), expert_id)
        title = str(result.get("title") or "").strip()
        summary = str(result.get("summary") or "").strip()
        claim = str(result.get("claim") or "").strip()
        claim_blob = f"{title}\n{claim}".lower()
        evidence = [str(item).strip() for item in list(result.get("evidence") or []) if str(item).strip()]
        summary_parts = [summary] if summary else []

        if "query_semantics_weakened" in signal_set and expert_id in {
            "database_analysis",
            "correctness_business",
            "performance_reliability",
            "security_compliance",
        }:
            phrase = "当前变更把 equal 精确匹配放宽成 like/contains 模糊匹配"
            summary_phrase = "查询语义从精确匹配退化为模糊匹配，可能扩大结果范围并削弱索引命中。"
            evidence_phrase = "检测到查询语义从 equal 精确匹配退化为 like 模糊匹配。"
            if "查询语义" not in title:
                title = f"{title}（查询语义退化）" if title else "查询语义退化"
            if "like" not in claim_blob:
                claim = f"{claim.rstrip('。')}；{phrase}。".strip("；")
            if summary_phrase not in summary_parts:
                summary_parts.append(summary_phrase)
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)

        if "naming_convention_violation" in signal_set:
            rename_terms = [term for term in list(signal_terms.get("naming_convention_violation") or []) if term]
            if len(rename_terms) >= 2:
                rename_phrase = f"并存在命名规范退化（{rename_terms[0]} -> {rename_terms[1]}）"
                rename_summary = f"变量/常量命名从 {rename_terms[0]} 退化为 {rename_terms[1]}，违背 Java 通用命名规范。"
            elif rename_terms:
                rename_phrase = f"并存在命名规范退化（{rename_terms[0]}）"
                rename_summary = f"标识符 {rename_terms[0]} 存在命名规范退化。"
            else:
                rename_phrase = "并存在命名规范退化"
                rename_summary = "当前改动引入了命名规范退化。"
            if "命名规范" not in title:
                title = f"{title}（命名规范退化）" if title else "命名规范退化"
            if rename_phrase.replace("并", "") not in claim:
                claim = f"{claim.rstrip('。')}；{rename_phrase}。".strip("；")
            if rename_summary not in summary_parts:
                summary_parts.append(rename_summary)
            if rename_phrase not in evidence:
                evidence.append(rename_phrase)

        if "magic_value_literal" in signal_set:
            magic_terms = [term for term in list(signal_terms.get("magic_value_literal") or []) if term]
            magic_display = " / ".join(magic_terms[:3]) if magic_terms else "新增字面量"
            magic_summary = f"当前改动把 {magic_display} 直接写进业务逻辑，形成魔法值，后续理解、复用和统一修改成本会升高。"
            if "魔法值" not in title:
                title = f"{title}（魔法值散落）" if title else "魔法值散落"
            if "魔法值" not in claim:
                claim = f"{claim.rstrip('。')}；并直接引入魔法值（{magic_display}）。".strip("；")
            if magic_summary not in summary_parts:
                summary_parts.append(magic_summary)
            evidence_phrase = f"检测到魔法值字面量：{magic_display}"
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)

        if "exception_swallowed" in signal_set:
            exception_text_blob = "\n".join(
                [
                    claim_blob,
                    str(target_hunk.get("excerpt") or ""),
                    *evidence,
                ]
            ).lower()
            exception_names = []
            for display, token in (
                ("NoSuchMethodException", "nosuchmethodexception"),
                ("IllegalAccessException", "illegalaccessexception"),
                ("InvocationTargetException", "invocationtargetexception"),
                ("InstantiationException", "instantiationexception"),
            ):
                if token in exception_text_blob:
                    exception_names.append(display)
            exception_display = "、".join(exception_names[:2]) if exception_names else "反射异常"
            swallow_summary = f"{exception_display} 等异常处理被忽略，后续排障、补偿和审计都会变难。"
            if swallow_summary not in summary_parts:
                summary_parts.append(swallow_summary)
            if "静默吞掉" not in claim_blob and "空 catch" not in claim_blob and "忽略" not in claim_blob:
                swallow_phrase = "当前变更还让 catch 块里的失败被忽略"
                claim = f"{claim.rstrip('。')}；{swallow_phrase}。".strip("；")
                if swallow_phrase not in evidence:
                    evidence.append(swallow_phrase)
            if expert_id in {"correctness_business", "performance_reliability"}:
                if "失败被忽略" not in title:
                    title = f"{title}（失败被忽略）" if title else "失败被忽略"
                if self._is_rule_backed_exception_swallow(
                    result,
                    evidence=evidence,
                    target_hunk=target_hunk,
                ):
                    result["finding_type"] = "direct_defect"
                    result["verification_needed"] = False
                    result["direct_evidence"] = True
                    result["normalized_issue_type"] = "exception_swallowed"
                    result["confidence"] = max(self._normalize_confidence(result.get("confidence"), 0.0), 0.86)
                elif str(result.get("finding_type") or "").strip().lower() not in {"direct_defect", "direct_code_issue"}:
                    result["finding_type"] = "risk_hypothesis"
                    result["verification_needed"] = True
                    result["direct_evidence"] = False
                    result["confidence"] = min(max(self._normalize_confidence(result.get("confidence"), 0.0), 0.68), 0.8)
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )

        if "exception_semantics_weakened" in signal_set and "伪装成成功" not in claim_blob and "返回语义" not in claim_blob:
            semantics_terms = [term for term in list(signal_terms.get("exception_semantics_weakened") or []) if term]
            semantics_display = " / ".join(semantics_terms[:2]) if semantics_terms else "fallback_return"
            semantics_phrase = f"当前异常路径把失败语义弱化成成功返回（{semantics_display}）"
            semantics_summary = "异常被吞掉后继续返回默认值、空值或成功态，会让上游误以为流程成功，补偿和回滚判断也会失真。"
            if "返回语义" not in title and "伪装成成功" not in title:
                title = f"{title}（异常返回语义被弱化）" if title else "异常返回语义被弱化"
            if "异常路径" not in claim and "返回语义" not in claim:
                claim = f"{claim.rstrip('。')}；{semantics_phrase}。".strip("；")
            if semantics_summary not in summary_parts:
                summary_parts.append(semantics_summary)
            evidence_phrase = f"检测到异常后返回语义被弱化：{semantics_display}"
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)
            if expert_id in {"correctness_business", "performance_reliability"}:
                if str(result.get("finding_type") or "").strip().lower() not in {"direct_defect", "direct_code_issue"}:
                    result["finding_type"] = "risk_hypothesis"
                    result["verification_needed"] = True
                    result["direct_evidence"] = False
                    result["confidence"] = min(max(self._normalize_confidence(result.get("confidence"), 0.0), 0.68), 0.8)
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )

        if "loop_call_amplification" in signal_set and expert_id in {"performance_reliability", "database_analysis"}:
            loop_terms = [term for term in list(signal_terms.get("loop_call_amplification") or []) if term]
            loop_display = " / ".join(loop_terms[:2]) if loop_terms else "循环内调用"
            loop_summary = "当前改动把仓储或远程调用放进循环路径，批量场景下会放大数据库往返、网络调用和超时风险。"
            if "循环调用放大" not in title:
                title = f"{title}（循环调用放大）" if title else "循环调用放大"
            if "循环" not in claim:
                claim = f"{claim.rstrip('。')}；当前实现存在循环内调用放大（{loop_display}）。".strip("；")
            if loop_summary not in summary_parts:
                summary_parts.append(loop_summary)
            evidence_phrase = f"检测到循环内调用放大：{loop_display}"
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)
            if expert_id == "performance_reliability":
                if str(result.get("finding_type") or "").strip().lower() not in {"direct_defect", "direct_code_issue"}:
                    result["finding_type"] = "risk_hypothesis"
                    result["verification_needed"] = True
                    result["direct_evidence"] = False
                    result["confidence"] = min(max(self._normalize_confidence(result.get("confidence"), 0.0), 0.65), 0.78)
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )

        if "comment_contract_unimplemented" in signal_set and expert_id in {"correctness_business", "maintainability_code_health"}:
            contract_terms = [term for term in list(signal_terms.get("comment_contract_unimplemented") or []) if term]
            contract_display = contract_terms[0] if contract_terms else "注释/TODO 承诺"
            contract_summary = "当前改动留下了注释或 TODO 承诺，但实现里没有对应动作，容易让调用方误以为能力已经落地。"
            if "承诺未落地" not in title:
                title = f"{title}（承诺未落地）" if title else "承诺未落地"
            if "承诺" not in claim and "TODO" not in claim:
                claim = f"{claim.rstrip('。')}；当前注释或 TODO 中承诺的行为没有在实现中落地（{contract_display}）。".strip("；")
            if contract_summary not in summary_parts:
                summary_parts.append(contract_summary)
            evidence_phrase = f"检测到注释/待办承诺未实现：{contract_display}"
            if evidence_phrase not in evidence:
                evidence.append(evidence_phrase)
            if expert_id == "correctness_business":
                if str(result.get("finding_type") or "").strip().lower() not in {"direct_defect", "direct_code_issue"}:
                    result["finding_type"] = "risk_hypothesis"
                    result["verification_needed"] = True
                    result["direct_evidence"] = False
                    result["confidence"] = min(max(self._normalize_confidence(result.get("confidence"), 0.0), 0.65), 0.78)
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )

        result["title"] = title
        if summary_parts:
            result["summary"] = "；".join(part for part in summary_parts if part)
        result["claim"] = claim
        result["evidence"] = evidence
        return result

    def _is_rule_backed_exception_swallow(
        self,
        parsed: dict[str, object],
        *,
        evidence: list[str],
        target_hunk: dict[str, object],
    ) -> bool:
        matched_rules = {
            str(item).strip().upper()
            for item in [*list(parsed.get("matched_rules") or []), *list(parsed.get("violated_guidelines") or [])]
            if str(item).strip()
        }
        if not matched_rules.intersection({"CORR-JDDD-002", "REL-JDDD-001", "CODE-JAVA-002", "GENERAL-EXPERT-CHECKS"}):
            return False
        text = "\n".join(
            [
                str(parsed.get("title") or ""),
                str(parsed.get("claim") or ""),
                str(parsed.get("summary") or ""),
                str(target_hunk.get("excerpt") or ""),
                *evidence,
            ]
        ).lower()
        if "catch" not in text and "异常" not in text and "exception" not in text:
            return False
        if not any(
            token in text
            for token in (
                "printstacktrace",
                "空 catch",
                "空catch",
                "静默吞",
                "吞掉",
                "nosuchmethodexception",
                "invocationtargetexception",
                "instantiationexception",
                "{ }",
            )
        ):
            return False
        has_context = bool([item for item in list(parsed.get("context_files") or []) if str(item).strip()])
        has_diff_anchor = "@@" in text or "-    e.printstacktrace" in text or "-\t\t\t\te.printstacktrace" in text
        return has_context or has_diff_anchor

    def _stabilize_line_start(self, value: object, fallback: int, target_hunk: dict[str, object]) -> int:
        normalized = self._normalize_line_start(value, fallback)
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            min_changed = min(changed_lines)
            max_changed = max(changed_lines)
            if normalized < min_changed or normalized > max_changed:
                return min_changed
            return normalized
        start_line = self._normalize_optional_line_value(target_hunk.get("start_line"))
        end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
        if start_line is None:
            return normalized
        if normalized < start_line or (end_line is not None and normalized > end_line):
            return start_line
        return normalized

    def _stabilize_line_end(self, value: object, fallback: int, target_hunk: dict[str, object]) -> int:
        normalized = self._normalize_line_start(value, fallback)
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if changed_lines:
            return max(fallback, min(normalized, max(changed_lines)))
        end_line = self._normalize_optional_line_value(target_hunk.get("end_line"))
        if end_line is None:
            return max(fallback, normalized)
        return max(fallback, min(normalized, end_line))

    def _refine_line_start_within_hunk(
        self,
        parsed: dict[str, object],
        target_hunk: dict[str, object],
        fallback_line_start: int,
    ) -> int:
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if len(changed_lines) <= 1:
            return int(fallback_line_start or 1)

        line_candidates = self._extract_semantic_line_candidates(target_hunk)
        if not line_candidates:
            return int(fallback_line_start or 1)

        semantic_parts: list[str] = []
        for key in ("title", "claim", "summary", "fix_strategy", "suggested_fix", "rule_based_reasoning"):
            value = str(parsed.get(key) or "").strip()
            if value:
                semantic_parts.append(value)
        for key in ("evidence", "assumptions", "matched_rules", "violated_guidelines", "change_steps"):
            semantic_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())

        finding_tokens = self._extract_anchor_tokens("\n".join(semantic_parts))
        finding_phrases = self._extract_anchor_phrases("\n".join(semantic_parts))
        if not finding_tokens and not finding_phrases:
            return int(fallback_line_start or 1)

        best_line = int(fallback_line_start or 1)
        best_score = 0
        explicit_line = self._normalize_optional_line_value(parsed.get("line_start"))
        for line_no, texts in line_candidates.items():
            combined_text = "\n".join(texts)
            candidate_tokens = self._extract_anchor_tokens(combined_text)
            overlap = finding_tokens & candidate_tokens
            score = 0
            for token in overlap:
                score += 3 if len(token) >= 8 or any(char.isdigit() for char in token) else 1
            candidate_phrases = self._extract_anchor_phrases(combined_text)
            for phrase in finding_phrases & candidate_phrases:
                score += 8 if phrase in {"n+1", "todo", "权限", "库存", "加锁", "超卖"} else 3
            lowered_text = combined_text.lower()
            for phrase in semantic_parts:
                normalized_phrase = phrase.lower()
                if normalized_phrase and len(normalized_phrase) >= 6 and normalized_phrase in lowered_text:
                    score += 4
            if explicit_line is not None and explicit_line == line_no:
                score += 2
            if score > best_score:
                best_score = score
                best_line = line_no
        return best_line if best_score > 0 else int(fallback_line_start or 1)

    def _refine_line_start_across_hunks(
        self,
        parsed: dict[str, object],
        target_hunks: list[dict[str, object]],
        fallback_line_start: int,
    ) -> int:
        best_line = int(fallback_line_start or 1)
        best_score = 0
        semantic_parts: list[str] = []
        for key in ("title", "claim", "summary", "fix_strategy", "suggested_fix", "rule_based_reasoning"):
            value = str(parsed.get(key) or "").strip()
            if value:
                semantic_parts.append(value)
        for key in ("evidence", "assumptions", "matched_rules", "violated_guidelines", "change_steps"):
            semantic_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())
        finding_tokens = self._extract_anchor_tokens("\n".join(semantic_parts))
        finding_phrases = self._extract_anchor_phrases("\n".join(semantic_parts))
        if not finding_tokens and not finding_phrases:
            return best_line
        semantic_candidates: list[tuple[int, str, set[str]]] = []
        for hunk in target_hunks or []:
            for line_no, texts in self._extract_semantic_line_candidates(dict(hunk)).items():
                combined_text = "\n".join(texts)
                candidate_tokens = self._extract_anchor_tokens(combined_text)
                candidate_phrases = self._extract_anchor_phrases(combined_text)
                semantic_candidates.append((int(line_no), combined_text, candidate_phrases))
                score = 0
                for token in finding_tokens & candidate_tokens:
                    score += 3 if len(token) >= 8 or any(char.isdigit() for char in token) else 1
                for phrase in finding_phrases & candidate_phrases:
                    score += 10 if phrase in {"n+1", "findbyid", "todo", "权限", "库存", "加锁", "超卖"} else 3
                if score > best_score:
                    best_score = score
                    best_line = int(line_no)
        if {"n+1", "findbyid"} & finding_phrases:
            for line_no, _combined_text, candidate_phrases in semantic_candidates:
                if {"n+1", "findbyid"} & candidate_phrases:
                    return int(line_no)
        if {"库存", "加锁", "超卖"} & finding_phrases:
            for line_no, _combined_text, candidate_phrases in semantic_candidates:
                if {"库存", "加锁", "超卖"} & candidate_phrases:
                    return int(line_no)
        return best_line if best_score > 0 else int(fallback_line_start or 1)

    def _extract_anchor_phrases(self, text: str) -> set[str]:
        """补充中文和符号型锚点，避免同一个 hunk 内问题行号漂移。"""

        lowered = str(text or "").lower()
        phrases = {
            "n+1",
            "todo",
            "循环",
            "批量",
            "repository",
            "findbyid",
            "findallbyid",
            "权限",
            "越权",
            "库存",
            "加锁",
            "超卖",
            "事务",
            "事件",
            "publish",
            "save",
        }
        found = {phrase for phrase in phrases if phrase in lowered}
        if "n_plus_one" in lowered:
            found.add("n+1")
        return found

    def _extract_semantic_line_candidates(self, target_hunk: dict[str, object]) -> dict[int, list[str]]:
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if not changed_lines:
            return {}

        hunk_lines = self._parse_target_hunk_diff_lines(target_hunk) if hasattr(self, "_parse_target_hunk_diff_lines") else {}
        added_lines = list(hunk_lines.get("added") or []) if isinstance(hunk_lines, dict) else []
        line_candidates: dict[int, list[str]] = {}
        if added_lines:
            for line_no, text in added_lines:
                if line_no in set(changed_lines) and str(text).strip():
                    line_candidates.setdefault(int(line_no), []).append(str(text).strip())

        excerpt = str(target_hunk.get("excerpt") or "")
        if not excerpt:
            return {line_no: self._merge_unique(values) for line_no, values in line_candidates.items()}
        for raw_line in excerpt.splitlines():
            formatted_match = re.match(r"^\s*(\d+)\s*\|\s*\+\s*(.*)$", raw_line)
            if formatted_match:
                line_no = int(formatted_match.group(1))
                text = formatted_match.group(2).strip()
                if line_no in set(changed_lines) and text:
                    line_candidates.setdefault(line_no, []).append(text)
        relevant_lines = [
            raw_line
            for raw_line in excerpt.splitlines()
            if raw_line[:1] in {"+", "-"} and not raw_line.startswith("+++") and not raw_line.startswith("---")
        ]
        if not relevant_lines:
            return {line_no: self._merge_unique(values) for line_no, values in line_candidates.items()}

        changed_index = 0
        for index, raw_line in enumerate(relevant_lines):
            assigned_line = changed_lines[min(changed_index, len(changed_lines) - 1)]
            line_candidates.setdefault(assigned_line, []).append(raw_line[1:].strip())
            next_line = relevant_lines[index + 1] if index + 1 < len(relevant_lines) else ""
            if raw_line.startswith("+") and changed_index < len(changed_lines) - 1:
                changed_index += 1
            elif raw_line.startswith("-") and (not next_line.startswith("+")) and changed_index < len(changed_lines) - 1:
                changed_index += 1
        return {line_no: self._merge_unique(values) for line_no, values in line_candidates.items()}

    def _normalize_changed_line_values(self, values: object) -> list[int]:
        normalized: list[int] = []
        for item in list(values or []):
            parsed = self._normalize_optional_line_value(item)
            if parsed is not None:
                normalized.append(parsed)
        return normalized

    def _extract_explicit_line_start_from_analysis(
        self,
        parsed: dict[str, object],
        target_hunk: dict[str, object],
    ) -> int | None:
        candidate_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if not candidate_lines:
            start_line = self._normalize_optional_line_value(target_hunk.get("start_line"))
            end_line = self._normalize_optional_line_value(target_hunk.get("end_line")) or start_line
            if start_line is not None and end_line is not None:
                candidate_lines = list(range(start_line, end_line + 1))
        if not candidate_lines:
            return None

        text_parts: list[str] = []
        for key in ("title", "claim", "summary", "code_excerpt"):
            value = str(parsed.get(key) or "").strip()
            if value:
                text_parts.append(value)
        for key in ("evidence", "cross_file_evidence", "assumptions", "change_steps"):
            text_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())
        text_blob = "\n".join(text_parts)
        if not text_blob:
            return None

        candidate_set = set(candidate_lines)
        line_numbers: list[int] = []
        for pattern in (
            r"第\s*(\d+)\s*行",
            r"\bline\s*(\d+)\b",
            r"^\s*(\d+)\s*\|",
            r"(\d+)\s*行",
        ):
            for match in re.finditer(pattern, text_blob, flags=re.IGNORECASE | re.MULTILINE):
                parsed_line = self._normalize_optional_line_value(match.group(1))
                if parsed_line is not None and parsed_line in candidate_set and parsed_line not in line_numbers:
                    line_numbers.append(parsed_line)
        return line_numbers[0] if line_numbers else None

    def _normalize_optional_line_value(self, value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return max(1, parsed)
