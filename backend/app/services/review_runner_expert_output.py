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
    ) -> list[dict[str, object]]:
        payload = self._parse_json_payload(text)
        candidates: list[dict[str, object]] = []
        explicit_empty_findings = False
        if isinstance(payload, list):
            candidates = [item for item in payload if isinstance(item, dict)]
        elif isinstance(payload, dict):
            has_findings_key = "findings" in payload
            nested = payload.get("findings")
            if isinstance(nested, list):
                candidates = [item for item in nested if isinstance(item, dict)]
                explicit_empty_findings = has_findings_key and not candidates
            if not candidates and not has_findings_key:
                candidates = [payload]
        if not candidates and not explicit_empty_findings:
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
        observations = self._collect_batch_review_observations(repository_context, normalized_batch_items)
        uncovered_observations = self._find_uncovered_review_observations(initial_candidates, observations)
        if not uncovered_observations:
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
        followup_result = self.llm_chat_service.complete_text(
            system_prompt=self._build_expert_system_prompt(
                expert,
                bound_documents,
                active_skills,
                rule_screening,
                analysis_mode=analysis_mode,
            ),
            user_prompt=followup_prompt,
            resolution=self.llm_chat_service.resolve_expert(expert, runtime_settings),
            runtime_settings=runtime_settings,
            fallback_text='{"findings":[]}',
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
        return collected

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
        confidence = float(candidate.get("confidence") or 0.0)
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
            "3. 只有确认成立且不是首轮已输出重复问题时，才输出新的 finding；",
            "4. 每条新增 finding 必须带 file_path、line_start、line_end、claim、suggested_code、observation_ids；",
            "5. 如果没有新增问题，返回 {\"findings\":[]}。",
            f"6. 最多新增 {max(1, int(max_findings or 1))} 条 findings。",
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
                '仅输出 JSON：{"findings":[{...}]}',
                'finding 字段至少包含: file_path, line_start, line_end, title, finding_type, claim, evidence, '
                'fix_strategy, suggested_fix, change_steps, suggested_code, confidence, verification_needed, observation_ids',
                "verification_needed 默认应为 false；只有结论仍依赖缺失上下文、外部条件或人工确认时才设为 true。",
                "如果 evidence 已直接指向变更代码，或 SAST/linter/规则/观察信号已交叉佐证，请保持 verification_needed=false。",
            ]
        )
        return "\n".join(lines)

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
            related_symbols = [str(value).strip() for value in list(item.get("related_symbols") or []) if str(value).strip()]
            symbol_display = " / ".join(related_symbols[:2]) if related_symbols else "当前调用"

            if expert.expert_id == "performance_reliability" and kind == "control_flow_with_external_call":
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
                        "rule_based_reasoning": "循环体内逐条调用仓储、远程服务或消息发送，会把单次调用成本放大到批量路径，属于需要直接修正的性能缺陷。",
                        "evidence": evidence[:3] or [summary or "检测到循环体中的外部依赖调用。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "把循环内逐条外部调用改成批量查询、批量远程接口或先聚合后统一处理。",
                        "suggested_fix": "优先把循环内的仓储/远程调用提到循环外，避免每个元素都触发一次外部依赖访问。",
                        "change_steps": ["确认循环内调用的依赖类型", "改成批量获取或批量提交", "保留单次结果映射关系"],
                        "suggested_code": "// TODO: 将循环内逐条外部调用改为批量处理，避免调用放大",
                        "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "该问题来自结构化观察信号，需要结合调用频率、批量规模和外部依赖成本复核后再升级为确定缺陷。",
                        "direct_evidence": False,
                        "evidence_source": "observation_signal",
                    }
                )
            elif expert.expert_id == "ddd_architecture" and kind == "construction_path_changed":
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "聚合工厂绕过",
                        "finding_type": "risk_hypothesis",
                        "claim": f"当前变更把原本的工厂创建路径替换成直接构造（{symbol_display}），可能绕过聚合根内的不变量和领域事件记录。",
                        "severity": "blocker",
                        "matched_rules": ["DDD-JDDD-001", "ARCH-JDDD-002"],
                        "violated_guidelines": ["聚合根必须在领域层内守护不变量", "ApplicationService 只能编排流程，不应绕过聚合工厂"],
                        "rule_based_reasoning": "从 diff 可直接看到工厂方法调用被删除并改为 new 构造；在 DDD 代码中，聚合工厂通常承载不变量检查和领域事件记录，应用层绕过它属于确定性架构缺陷。",
                        "evidence": evidence[:3] or [summary or "检测到工厂方法调用被直接构造替代。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "恢复通过聚合根工厂方法创建对象，保证不变量和领域事件仍在领域层内完成。",
                        "suggested_fix": "把直接 new 聚合根的代码改回调用原有 create 工厂方法；如工厂方法被删除，应在聚合根内恢复该工厂方法并保留领域事件记录。",
                        "change_steps": ["定位被替换的工厂方法调用", "恢复调用聚合根工厂方法", "确认工厂方法内仍记录必要领域事件"],
                        "suggested_code": "// TODO: 恢复为 Course.create(...) 这类聚合工厂调用，避免绕过领域事件记录",
                        "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "该问题来自结构化观察信号，需要确认被替换的工厂方法是否确实承载不变量校验或领域事件记录。",
                        "direct_evidence": False,
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
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": "注释、接口说明或 TODO 对外表达的是代码语义承诺；如果实现中没有对应动作，属于直接的业务正确性缺口，不应仅作为提示保留。",
                        "evidence": evidence[:3] or [summary or "检测到注释、TODO 或方法意图与实现不一致。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "要么补齐承诺中的行为，要么删除会误导调用方的注释、TODO 或命名表达。",
                        "suggested_fix": "先确认该承诺是否仍然成立；如果成立，补齐实现；如果不再成立，删除失效承诺并同步修正文档或方法命名。",
                        "change_steps": ["确认承诺的目标行为", "补齐对应业务动作或副作用", "同步修正注释/TODO/接口说明"],
                        "suggested_code": "// TODO: 补齐承诺中的业务动作，或删除失效承诺避免误导调用方",
                        "confidence": min(max(float(item.get("confidence") or 0.0), 0.65), 0.78),
                        "verification_needed": True,
                        "verification_plan": "该问题来自结构化观察信号，需要确认注释、TODO 或命名表达是否仍是当前有效业务契约。",
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
                        "finding_type": "direct_defect",
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
                        "suggested_code": "// TODO: 恢复分页/LIMIT 或批量边界，避免无界查询",
                        "confidence": max(float(item.get("confidence") or 0.0), 0.86),
                        "verification_needed": False,
                        "verification_plan": "",
                        "direct_evidence": True,
                    }
                )
            elif expert.expert_id == "performance_reliability" and kind in {"bulk_processing_boundary_missing", "transactional_side_effect"}:
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "批量路径可靠性风险",
                        "finding_type": "direct_defect",
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
                        "change_steps": ["识别批量输入规模", "拆分事务与外部副作用", "补充超时/幂等/重试保护"],
                        "suggested_code": "// TODO: 为批量/事务副作用路径补充边界、超时和幂等保护",
                        "confidence": max(float(item.get("confidence") or 0.0), 0.84),
                        "verification_needed": False,
                        "verification_plan": "",
                        "direct_evidence": True,
                    }
                )
            elif expert.expert_id == "security_compliance" and kind in {"input_validation_removed", "security_guard_removed"}:
                forced.append(
                    {
                        "file_path": file_path,
                        "line_start": line_start,
                        "line_end": line_start,
                        "title": "入口保护被删除",
                        "finding_type": "direct_defect",
                        "normalized_issue_type": "security_guard_removed",
                        "claim": f"当前变更删除或弱化了入口校验、权限校验或身份一致性保护（{symbol_display}），可能扩大未授权或非法输入的进入面。",
                        "severity": "high",
                        "matched_rules": [],
                        "violated_guidelines": [],
                        "rule_based_reasoning": "入口校验和权限判断属于安全边界。diff 中删除这类保护时，应直接作为高风险安全问题处理，而不是依赖后续人工猜测。",
                        "evidence": evidence[:3] or [summary or "检测到入口保护或权限校验被删除。"],
                        "cross_file_evidence": [],
                        "assumptions": [],
                        "context_files": [file_path] if file_path else [],
                        "observation_ids": [observation_id] if observation_id else [],
                        "fix_strategy": "恢复入口校验、权限判断或身份一致性检查。",
                        "suggested_fix": "保留原有校验，并为异常输入/越权输入补充回归测试。",
                        "change_steps": ["恢复被删除的校验", "确认错误响应语义", "补充越权或非法输入测试"],
                        "suggested_code": "// TODO: 恢复入口校验/权限保护，避免非法输入绕过",
                        "confidence": max(float(item.get("confidence") or 0.0), 0.86),
                        "verification_needed": False,
                        "verification_plan": "",
                        "direct_evidence": True,
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
            "# suggested rewrite for",
        ]
        if any(marker in lower for marker in generic_markers):
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
                result["confidence"] = min(float(result.get("confidence") or 0.0), 0.4)
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
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.45)
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
                result["confidence"] = min(float(result.get("confidence") or 0.0), 0.35)
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
        result = self._enforce_expert_output_schema(result, expert_id)
        result = self._apply_input_quality_gate(result, input_completeness or {})
        result = self._sanitize_user_confirmation_language(result)
        return result

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
        ):
            payload[key] = [str(item).strip() for item in list(payload.get(key) or []) if str(item).strip()]
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
            result["confidence"] = min(float(result.get("confidence") or 0.0), 0.35)
            if str(result.get("severity") or "").lower() in {"blocker", "critical", "high"}:
                result["severity"] = "medium"
        else:
            # 仅缺失源码上下文时保留原始 finding 类型/置信度，避免把有效问题整体降为“提示性”导致 issue 为空。
            has_evidence = bool(result.get("evidence") or result.get("cross_file_evidence"))
            result["finding_type"] = str(result.get("finding_type") or "risk_hypothesis")
            result["verification_needed"] = bool(result.get("verification_needed", False)) and not has_strong_java_signal
            result["direct_evidence"] = bool(has_evidence)
            result["confidence"] = float(result.get("confidence") or 0.0)
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

        needs_course_create = "course.create" not in text_blob
        needs_aggregate = "aggregate" not in text_blob
        needs_factory = "factory" not in text_blob
        needs_domain_event = "domain event" not in text_blob

        if needs_aggregate or needs_factory:
            suffix = "Aggregate factory bypass"
            if suffix.lower() not in title.lower():
                title = f"{title} ({suffix})" if title else suffix
        result["title"] = title

        additions: list[str] = []
        if needs_course_create:
            additions.append("当前变更绕过了 Course.create")
        if needs_aggregate or needs_factory:
            additions.append("这属于 aggregate factory bypass")
        if needs_domain_event:
            additions.append("并可能让 domain event 录制/发布语义退化")
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

        if "exception_swallowed" in signal_set and "静默吞掉" not in claim_blob and "空 catch" not in claim_blob:
            swallow_phrase = "当前变更还让 catch 块静默吞掉异常"
            swallow_summary = "异常处理被弱化为静默吞掉异常，后续排障、补偿和审计都会变难。"
            claim = f"{claim.rstrip('。')}；{swallow_phrase}。".strip("；")
            if swallow_summary not in summary_parts:
                summary_parts.append(swallow_summary)
            if swallow_phrase not in evidence:
                evidence.append(swallow_phrase)
            if expert_id in {"correctness_business", "performance_reliability"}:
                if "静默吞掉异常" not in title:
                    title = f"{title}（静默吞掉异常）" if title else "静默吞掉异常"
                result["finding_type"] = "direct_defect"
                result["verification_needed"] = False
                result["direct_evidence"] = True
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )
                result["confidence"] = max(float(result.get("confidence") or 0.0), 0.87)

        if "exception_semantics_weakened" in signal_set and "伪装成成功" not in claim_blob and "返回语义" not in claim_blob:
            semantics_terms = [term for term in list(signal_terms.get("exception_semantics_weakened") or []) if term]
            semantics_display = " / ".join(semantics_terms[:2]) if semantics_terms else "fallback_return"
            semantics_phrase = f"当前异常路径把失败语义弱化成成功或兜底返回（{semantics_display}）"
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
                result["finding_type"] = "direct_defect"
                result["verification_needed"] = False
                result["direct_evidence"] = True
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )
                result["confidence"] = max(float(result.get("confidence") or 0.0), 0.88)

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
                result["finding_type"] = "direct_defect"
                result["verification_needed"] = False
                result["direct_evidence"] = True
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )
                result["confidence"] = max(float(result.get("confidence") or 0.0), 0.86)

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
                result["finding_type"] = "direct_defect"
                result["verification_needed"] = False
                result["direct_evidence"] = True
                result["severity"] = (
                    "high"
                    if str(result.get("severity") or "").lower() not in {"blocker", "critical", "high"}
                    else result.get("severity")
                )
                result["confidence"] = max(float(result.get("confidence") or 0.0), 0.88)

        result["title"] = title
        if summary_parts:
            result["summary"] = "；".join(part for part in summary_parts if part)
        result["claim"] = claim
        result["evidence"] = evidence
        return result

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
        if not finding_tokens:
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

    def _extract_semantic_line_candidates(self, target_hunk: dict[str, object]) -> dict[int, list[str]]:
        excerpt = str(target_hunk.get("excerpt") or "")
        changed_lines = self._normalize_changed_line_values(target_hunk.get("changed_lines"))
        if not excerpt or not changed_lines:
            return {}

        relevant_lines = [
            raw_line
            for raw_line in excerpt.splitlines()
            if raw_line[:1] in {"+", "-"} and not raw_line.startswith("+++") and not raw_line.startswith("---")
        ]
        if not relevant_lines:
            return {}

        line_candidates: dict[int, list[str]] = {}
        changed_index = 0
        for index, raw_line in enumerate(relevant_lines):
            assigned_line = changed_lines[min(changed_index, len(changed_lines) - 1)]
            line_candidates.setdefault(assigned_line, []).append(raw_line[1:].strip())
            next_line = relevant_lines[index + 1] if index + 1 < len(relevant_lines) else ""
            if raw_line.startswith("+") and changed_index < len(changed_lines) - 1:
                changed_index += 1
            elif raw_line.startswith("-") and (not next_line.startswith("+")) and changed_index < len(changed_lines) - 1:
                changed_index += 1
        return line_candidates

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
