from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Literal

from app.services.cross_file_impact import build_cross_file_impact_hints
from app.services.context_block import ContextBlock
from app.services.context_priority_policy import priority_for_block_type
from app.services.model_prompt_profiles import resolve_model_prompt_profile
from app.services.prompt_budget_planner import PromptBudgetPlanner
from app.services.risk_candidate_service import RiskCandidateService

if TYPE_CHECKING:
    from app.domain.models.expert_profile import ExpertProfile
    from app.domain.models.review import ReviewSubject

DDD_ARCHITECTURE_EXPERT_IDS = {"ddd_architecture", "ddd_specification"}


class ReviewRunnerPromptingMixin:
    """Build expert prompts and trim prompt context within model budgets."""

    def _build_expert_prompt(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        tool_evidence: list[dict[str, object]],
        runtime_tool_results: list[dict[str, object]],
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]] | None,
        bound_documents: list[object],
        disallowed_inference: list[str],
        expected_checks: list[str],
        active_skills: list[object],
        rule_screening: dict[str, object] | None = None,
        *,
        analysis_mode: Literal["standard", "light"] = "standard",
        include_target_file_full_diff: bool = True,
        include_related_diff_summary: bool = True,
        model_name: str | None = None,
        prompt_profile_name: str | None = "auto",
    ) -> str:
        """构造专家最终输入给 LLM 的用户提示词。

        这里强制把 diff、代码仓上下文、运行时工具结果、规范文档和禁止推断规则合并，
        目的是把专家的审查边界和证据来源约束得足够明确。
        """
        prompt_profile = resolve_model_prompt_profile(model_name or expert.model, profile_name=prompt_profile_name)
        if prompt_profile.avoid_long_system_prompt:
            return self._build_rule_guided_expert_prompt(
                subject=subject,
                expert=expert,
                file_path=file_path,
                line_start=line_start,
                runtime_tool_results=runtime_tool_results,
                repository_context=repository_context,
                target_hunk=target_hunk,
                target_hunks=target_hunks or [],
                bound_documents=bound_documents,
                disallowed_inference=disallowed_inference,
                expected_checks=expected_checks,
                active_skills=active_skills,
                rule_screening=rule_screening or {},
                include_target_file_full_diff=include_target_file_full_diff,
                include_related_diff_summary=include_related_diff_summary,
                max_context_chars=prompt_profile.max_context_chars,
                max_rules_per_prompt=prompt_profile.max_rules_per_prompt,
            )
        capability_summary = self.capability_service.build_capability_summary(expert, tool_evidence)
        code_excerpt = self._build_code_excerpt(subject, file_path, line_start, expert.expert_id)
        target_file_full_diff = (
            self._build_target_file_full_diff(subject, file_path)
            if include_target_file_full_diff
            else "本轮为多文件批量模式，目标文件完整 diff 已在批次附录中按文件展开，请结合附录逐文件审查。"
        )
        related_diff_summary = (
            self._build_related_diff_summary(subject, file_path)
            if include_related_diff_summary
            else "本轮为多文件批量模式，其他变更文件摘要已由“本轮批量文件清单”提供，不再重复展开。"
        )
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=target_file_full_diff,
        )
        prompt_repository_context = dict(repository_context)
        if list(java_quality.get("signals") or []):
            prompt_repository_context["java_quality_signals"] = list(java_quality.get("signals") or [])
        if str(java_quality.get("summary") or "").strip():
            prompt_repository_context["java_quality_signal_summary"] = str(java_quality.get("summary") or "").strip()
        if list(java_quality.get("observations") or []):
            prompt_repository_context["review_observations"] = self._normalize_review_observations(
                java_quality.get("observations")
            )
        if dict(java_quality.get("analysis_stages") or {}):
            prompt_repository_context["analysis_stages"] = dict(java_quality.get("analysis_stages") or {})
        prompt_repository_context = self._prepare_prompt_repository_context(
            expert=expert,
            repository_context=prompt_repository_context,
            rule_screening=rule_screening or {},
            analysis_mode=analysis_mode,
        )
        runtime_tool_summary = self._build_runtime_tool_summary(runtime_tool_results)
        repository_context_summary = self._build_repository_context_summary(prompt_repository_context, runtime_tool_results)
        repo_review_instruction_summary = self._build_repo_review_instruction_summary(prompt_repository_context)
        repository_source_blocks = self._build_repository_source_blocks(prompt_repository_context, runtime_tool_results)
        hunk_summary = self._build_hunk_summary(target_hunk)
        hunk_batch_summary = self._build_hunk_batch_summary(target_hunks or [])
        review_spec_summary = self._build_review_spec_summary(expert.review_spec)
        bound_documents_summary = self._build_bound_documents_summary(bound_documents)
        rule_screening_summary = self._build_rule_screening_summary(rule_screening or {})
        active_skill_summary = self._build_active_skill_summary(active_skills)
        design_doc_summary = self._build_design_doc_summary(subject)
        language = self._infer_code_language(file_path)
        language_general_guidance = self._build_expert_language_general_guidance(language, expert.expert_id)
        java_ddd_focus = self._build_java_ddd_review_focus(language, expert.expert_id, prompt_repository_context)
        observation_review_summary = self._build_observation_review_summary(prompt_repository_context)
        review_learning_hints = ""
        if hasattr(self, "review_learning_service"):
            review_learning_hints = self.review_learning_service.build_prompt_hints(
                repo_id=str(subject.repo_id or ""),
                issue_types=self._extract_review_learning_issue_types(java_quality, rule_screening or {}),
                file_paths=[file_path, *[str(item) for item in subject.changed_files or []]],
                max_items=3,
                max_chars=600,
            )
        input_completeness_summary = self._build_review_input_completeness_summary(
            subject,
            file_path,
            line_start,
            prompt_repository_context,
            expert=expert,
            bound_documents=bound_documents,
            rule_screening=rule_screening or {},
            language=language,
        )
        has_design_docs = bool(self._review_design_docs(subject))
        business_changed_files = self._business_changed_files(subject)
        routing_reason = str(repository_context.get("routing_reason") or "").strip()
        design_contract = (
            '"design_alignment_status":"aligned|partially_aligned|misaligned",'
            '"matched_design_points":["已经实现的设计点"],'
            '"missing_design_points":["缺失的设计点"],'
            '"extra_implementation_points":["超出设计的实现"],'
            '"design_conflicts":["与设计冲突的实现"],'
            if has_design_docs
            else ""
        )
        design_instruction = (
            "本次已绑定详细设计文档，你需要严格核对实现与设计是否一致，并在 JSON 中输出设计一致性字段。\n"
            if has_design_docs
            else "本次未绑定详细设计文档，不要执行设计一致性检查，也不要输出任何 design_* / 设计一致性字段。\n"
        )
        disallowed_text = " / ".join(disallowed_inference[:5]).strip()
        disallowed_text = disallowed_text.replace(
            "证据不足时不要输出 finding",
            "没有当前变更代码位置时不要输出候选；有当前代码证据但缺上下文时保留 finding 并标记 verification_needed",
        )
        if not disallowed_text:
            disallowed_text = (
                "不要把没有当前变更代码位置的猜测输出为 finding；"
                "有当前变更代码位置但缺上下文时，保留 finding，设置 verification_needed=true 并写清 verification_plan。"
            )
        if analysis_mode == "light":
            light_sections, light_budget = self._apply_light_prompt_request_budget(
                expert_id=expert.expert_id,
                include_target_file_full_diff=include_target_file_full_diff,
                sections={
                    "review_spec_summary": review_spec_summary,
                    "active_skill_summary": active_skill_summary,
                    "bound_documents_summary": bound_documents_summary,
                    "rule_screening_summary": rule_screening_summary,
                    "input_completeness_summary": input_completeness_summary,
                    "language_general_guidance": language_general_guidance,
                    "design_doc_summary": design_doc_summary,
                    "hunk_summary": hunk_summary,
                    "hunk_batch_summary": hunk_batch_summary,
                    "target_file_full_diff": target_file_full_diff,
                    "related_diff_summary": related_diff_summary,
                    "runtime_tool_summary": runtime_tool_summary,
                    "repository_context_summary": repository_context_summary,
                    "repo_review_instruction_summary": repo_review_instruction_summary,
                    "repository_source_blocks": repository_source_blocks,
                    "code_excerpt": code_excerpt,
                    "observation_review_summary": observation_review_summary,
                    "review_learning_hints": review_learning_hints,
                },
            )
            review_spec_summary = light_sections["review_spec_summary"]
            active_skill_summary = light_sections["active_skill_summary"]
            bound_documents_summary = light_sections["bound_documents_summary"]
            rule_screening_summary = light_sections["rule_screening_summary"]
            input_completeness_summary = light_sections["input_completeness_summary"]
            language_general_guidance = light_sections["language_general_guidance"]
            design_doc_summary = light_sections["design_doc_summary"]
            hunk_summary = light_sections["hunk_summary"]
            hunk_batch_summary = light_sections["hunk_batch_summary"]
            target_file_full_diff = light_sections["target_file_full_diff"]
            related_diff_summary = light_sections["related_diff_summary"]
            runtime_tool_summary = light_sections["runtime_tool_summary"]
            repository_context_summary = light_sections["repository_context_summary"]
            repo_review_instruction_summary = light_sections["repo_review_instruction_summary"]
            repository_source_blocks = light_sections["repository_source_blocks"]
            code_excerpt = light_sections["code_excerpt"]
            observation_review_summary = light_sections["observation_review_summary"]
            review_learning_hints = light_sections["review_learning_hints"]
            prompt_repository_context["prompt_request_budget"] = light_budget
            if not include_target_file_full_diff:
                repository_source_blocks = "本轮为多文件批量模式，详细源码片段已在“多文件联合审查补充”逐文件提供，此处不重复展开。"
                related_diff_summary = "本轮为多文件批量模式，其他文件摘要已在附录逐文件提供。"
                repository_context_summary = self._compact_prompt_block(repository_context_summary, 1800)
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"专家: {expert.expert_id} / {expert.name_zh}\n"
            f"角色: {expert.role}\n"
            f"目标文件: {file_path}\n"
            f"目标行号: {line_start}\n"
            f"业务变更文件: {', '.join(business_changed_files) or '未提供'}\n"
            f"主Agent派工理由: {routing_reason or '未提供'}\n"
            f"能力约束:\n{capability_summary}\n"
            f"规范提要:\n{review_spec_summary}\n"
            f"已激活技能:\n{active_skill_summary}\n"
            f"已绑定参考文档:\n{bound_documents_summary}\n"
            f"附加产品/仓库规则遍历结果:\n{rule_screening_summary}\n"
            f"审查阶段说明:\n{self._build_analysis_stage_summary(prompt_repository_context)}\n"
            f"输入完整性校验:\n{input_completeness_summary}\n"
            f"语言通用规范提示:\n{language_general_guidance or '当前目标文件未命中已配置的语言通用规范提示，请仅依据专家规范、规则和代码证据审查。'}\n"
            f"本次审核绑定的详细设计文档:\n{design_doc_summary}\n"
            f"目标 hunk:\n{hunk_summary}\n"
            f"目标 hunk 判读规则: `| +` 行代表 MR 合入后的当前代码，`| -` 行代表旧代码；问题必须落在 `| +` 当前代码或其直接暴露的影响上，旧代码已被新代码修复时不要输出。\n"
            f"同文件其他变更 hunk:\n{hunk_batch_summary}\n"
            f"目标文件完整 diff:\n{target_file_full_diff}\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n"
            f"运行时工具调用结果:\n{runtime_tool_summary}\n"
            f"代码仓上下文:\n{repository_context_summary}\n"
            f"仓库内检视规则:\n{repo_review_instruction_summary or '当前目标文件未命中仓库内 REVIEW.md 或 .codereview.yaml 规则。'}\n"
            f"关键源码上下文:\n{repository_source_blocks}\n"
            f"当前代码片段:\n{code_excerpt}\n"
            f"结构化观察点:\n{observation_review_summary}\n"
            f"历史反馈边界:\n{review_learning_hints or '当前目标文件未命中可复用的历史人工反馈案例。'}\n"
            f"必查项: {' / '.join(expected_checks[:5]) or expert.role}\n"
            f"{java_ddd_focus}"
            f"候选边界: {disallowed_text}\n"
            f"你必须完整阅读并严格遵守系统提供的《审视规范文档》，再结合真实 diff、代码仓上下文和技能结果做审查。\n"
            f"规则分层说明：专家通用规范和语言/框架通用规范是基础审查依据；“附加产品/仓库规则”用于补充产品特有约束、优先级和误报保护，不是唯一准入条件。\n"
            f"无附加规则命中时，仍必须按专家通用规范审查真实缺陷；有附加规则命中时，必须逐条核对并只引用本轮提供的真实规则 ID，不要编造产品规则编号。\n"
            f"{'请优先基于目标文件完整 diff 做审查，再结合其他变更文件摘要和代码仓上下文判断影响范围，避免泛泛而谈，不要评论未涉及的文件，不要越过你的职责边界。' if include_target_file_full_diff else '本轮为多文件批量模式，请优先基于“本轮批量文件清单”和每个文件的 hunk 说明逐文件审查，再回到代码仓上下文交叉验证，不要遗漏任何文件。'}\n"
            f"{design_instruction}"
            f"如果你的结论已有当前变更代码位置但缺少关联上下文，请保留该 finding，并设置 verification_needed=true、写清 verification_plan；"
            f"只有完全没有当前变更代码位置、纯靠猜测的结论才不要输出。\n"
            f"对“结构化观察点”要逐条复核：它们只是主Agent提炼的可疑代码现象，不等于已经确认的问题。你可以否定观察点；若确认其成立并输出 finding，必须把对应 observation_id 写入 observation_ids。\n"
            f"必须按三段式深审：先观察当前 hunk 和风险候选，再判断是否构成真实问题，最后补齐文件/行号/代码锚点/影响/修复证据。\n"
            f"工具观察、SAST/linter、风险候选池都只是辅助证据，不是最终问题；如果采纳工具线索，必须写入 adopted_tool_observations；如果不采纳，不要输出对应 finding。\n"
            f"输出必须是 JSON（不要输出 Markdown / 额外解释）。\n"
            f"该规则在标准模式和轻量模式都必须遵守。\n"
            f"除 normalized_issue_type、代码标识、文件路径、接口名、类名和方法名外，所有面向用户展示的文字字段必须使用中文；不要输出英文修复说明或英文证据说明。\n"
            f"如果只发现 1 个问题，输出单个 JSON 对象；如果发现多个互不重复的问题，可输出 JSON 数组或 {{\"findings\":[...]}}，最多 5 条。\n"
            f"当提供了多个 hunk 时，必须按 hunk 逐段审查：每条 finding 必须定位到某个具体 hunk，并给出对应 line_start/line_end；无法定位到具体 hunk 行号的结论不要输出。\n"
            f"每条 finding 的 JSON 字段要求:\n"
            f'{{"ack":"先回应主Agent派工","title":"一句话问题标题","finding_type":"direct_defect|test_gap|design_concern","normalized_issue_type":"从枚举中选择或给出稳定英文短语","claim":"必须落在当前文件/行号的确定性结论","severity":"blocker|high|medium|low","target_id":"必须从目标 hunk 的 target_id 原样选择","file_path":"必须从目标 hunk 的 file_path 原样选择","line_start":"必须取该 hunk changed_lines 中的当前代码行号","line_end":"必须取该 hunk changed_lines 中的当前代码行号","matched_rules":["命中的专家通用规范、语言通用规范或本轮真实附加规则 ID"],"violated_guidelines":["违反的具体规范"],"rule_based_reasoning":"说明为何违反规范以及规范如何约束当前改动；若引用附加规则必须写出真实规则 ID","evidence":["至少2条具体代码证据"],"cross_file_evidence":["跨文件佐证"],"assumptions":[],"context_files":["引用的目标分支文件"],"observation_ids":["若该 finding 来自结构化观察点，必须填写对应 observation_id；否则留空数组"],"adopted_tool_observations":["若采纳工具观察或风险候选，填写 tool:rule_id 或 candidate_id；否则留空数组"],{design_contract}"why_it_matters":"影响说明","fix_strategy":"一句话说明修改思路","suggested_fix":"详细说明应该怎么改","change_steps":["按顺序写清楚 2-4 个修改步骤"],"suggested_code":"给出建议修改后的完整代码片段","confidence":0.0,"verification_needed":false,"verification_plan":""}}'
        )

    def _build_rule_guided_expert_prompt(
        self,
        *,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        runtime_tool_results: list[dict[str, object]],
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]],
        bound_documents: list[object],
        disallowed_inference: list[str],
        expected_checks: list[str],
        active_skills: list[object],
        rule_screening: dict[str, object],
        include_target_file_full_diff: bool,
        include_related_diff_summary: bool,
        max_context_chars: int,
        max_rules_per_prompt: int,
    ) -> str:
        language = self._infer_code_language(file_path)
        expert_scope_summary = self._compact_prompt_block(
            str(expert.system_prompt or f"你是{expert.name_zh}，职责是{expert.role}。"),
            1200,
        )
        expert_review_spec_summary = self._compact_prompt_block(
            self._build_review_spec_summary(str(expert.review_spec or "")),
            1800,
        )
        language_general_guidance = self._compact_prompt_block(
            self._build_expert_language_general_guidance(language, expert.expert_id),
            1200,
        )
        java_ddd_focus = self._compact_prompt_block(
            self._build_java_ddd_review_focus(language, expert.expert_id, repository_context),
            1200,
        )
        target_file_full_diff = (
            self._build_target_file_full_diff(subject, file_path)
            if include_target_file_full_diff
            else "多文件批量模式：目标文件完整 diff 在批次附录中展开。"
        )
        related_diff_summary = (
            self._build_related_diff_summary(subject, file_path)
            if include_related_diff_summary
            else "多文件批量模式：其他变更文件摘要由批次附录提供。"
        )
        java_quality = self.java_quality_signal_extractor.extract(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=target_file_full_diff,
        )
        prompt_repository_context = dict(repository_context or {})
        if list(java_quality.get("signals") or []):
            prompt_repository_context["java_quality_signals"] = list(java_quality.get("signals") or [])
        if str(java_quality.get("summary") or "").strip():
            prompt_repository_context["java_quality_signal_summary"] = str(java_quality.get("summary") or "").strip()
        if list(java_quality.get("observations") or []):
            prompt_repository_context["review_observations"] = self._normalize_review_observations(
                java_quality.get("observations")
            )
        if dict(java_quality.get("analysis_stages") or {}):
            prompt_repository_context["analysis_stages"] = dict(java_quality.get("analysis_stages") or {})
        prompt_repository_context = self._prepare_prompt_repository_context(
            expert=expert,
            repository_context=prompt_repository_context,
            rule_screening=rule_screening or {},
            analysis_mode="light",
        )
        input_completeness_summary = self._build_review_input_completeness_summary(
            subject,
            file_path,
            line_start,
            prompt_repository_context,
            expert=expert,
            bound_documents=bound_documents,
            rule_screening=rule_screening or {},
            language=language,
        )
        observation_review_summary = self._build_observation_review_summary(prompt_repository_context)
        review_learning_hints = ""
        if hasattr(self, "review_learning_service"):
            review_learning_hints = self.review_learning_service.build_prompt_hints(
                repo_id=str(subject.repo_id or ""),
                issue_types=self._extract_review_learning_issue_types(java_quality, rule_screening or {}),
                file_paths=[file_path, *[str(item) for item in subject.changed_files or []]],
                max_items=3,
                max_chars=600,
            )
        source_context_note = ""
        if not include_target_file_full_diff:
            source_context_note = "本轮为多文件批量模式，详细源码片段已在“多文件联合审查补充”逐文件提供，此处不重复展开。"
        context_packet = {
            "target_file": file_path,
            "target_line": line_start,
            "expert_scope": expert_scope_summary,
            "expert_review_spec": expert_review_spec_summary,
            "language_general_guidance": language_general_guidance,
            "java_ddd_focus": java_ddd_focus,
            "input_completeness": input_completeness_summary,
            "review_observations": observation_review_summary,
            "review_learning_hints": review_learning_hints or "当前目标文件未命中可复用的历史人工反馈案例。",
            "source_context_note": source_context_note,
            "target_hunk": self._build_hunk_summary(target_hunk),
            "same_file_hunks": self._build_hunk_batch_summary(target_hunks),
            "target_file_full_diff": target_file_full_diff,
            "related_diff_summary": related_diff_summary,
            "repository_context": self._build_repository_context_summary(prompt_repository_context, runtime_tool_results),
            "runtime_tools": self._build_runtime_tool_summary(runtime_tool_results),
            "code_excerpt": self._build_code_excerpt(subject, file_path, line_start, expert.expert_id),
            "active_skills": self._build_active_skill_summary(active_skills),
            "bound_documents": self._build_bound_documents_summary(bound_documents),
        }
        output_contract = {
            "rule_check_results": [
                {
                    "rule_id": "string",
                    "status": "violated|passed|not_applicable|insufficient_context",
                    "evidence": ["string"],
                    "missing_context": ["string"],
                    "reason": "string",
                }
            ],
            "candidate_findings": [
                {
                    "rule_id": "string",
                    "title": "string",
                    "target_id": "必须从 TARGET_HUNKS[].target_id 原样选择",
                    "file_path": "必须从 TARGET_HUNKS[].file_path 原样选择",
                    "line": "必须取对应 hunk changed_lines 中的当前代码行号",
                    "method_name": "当前问题所在方法名；无法确认时留空字符串",
                    "code_anchor": "当前代码中的最小问题片段，必须和 evidence 指向同一段代码",
                    "change_understanding_refs": ["target_id、文件路径、类名或方法名，用于说明本候选引用了哪段结构化变更理解"],
                    "evidence": "string",
                    "confidence": "high|medium|low",
                    "observation_ids": ["string"],
                    "adopted_tool_observations": ["采纳的工具观察或风险候选 ID；未采纳则为空数组"],
                }
            ],
            "context_requests": [
                {
                    "rule_id": "string",
                    "missing_context": "string",
                    "why_needed": "string",
                }
            ],
            "self_check": {
                "checked_all_rules": True,
                "used_context_files": ["string"],
                "unverified_assumptions": ["string"],
            },
        }
        disallowed_text = " / ".join(disallowed_inference[:5])
        disallowed_text = disallowed_text.replace(
            "证据不足时不要输出 finding",
            "没有当前变更代码位置时不要输出候选；有当前代码证据但缺上下文时写 context_requests",
        )
        lines = [
            "[SYSTEM RULES]",
            "你是代码审查专家。只能基于 EXPERT_PROFILE、DIFF、CONTEXT_PACKET、RULE_CARDS 判断，不要编造缺失上下文。",
            "必须同时遵守专家职责说明、专家审视规范、语言通用规范和 RULE_CARDS；绑定规范和专家画像都要参与检视，候选结果取并集。",
            "必须逐条检查 RULE_CARDS。每条适用规则都要输出 rule_check_results。",
            "如果 required_context 缺失或无法确认，规则状态必须是 insufficient_context，不能写 passed。",
            "必须按三段式深审：观察阶段识别当前 hunk 和风险候选，判断阶段确认是否构成真实问题，证据阶段补齐代码锚点、影响和修复方向。",
            "第一阶段请高召回列出 candidate_findings；宁可列可疑候选，不要因为不确定直接省略。",
            "candidate_findings 必须绑定真实 rule_id、file_path、line 和代码证据。",
            "candidate_findings 应尽量填写 method_name、code_anchor、change_understanding_refs；change_understanding_refs 至少包含 target_id 或文件/方法标识。",
            "工具观察、SAST/linter、风险候选池只是辅助证据，不是预设结论；采纳时必须填写 adopted_tool_observations，未采纳时不要输出对应候选。",
            "candidate_findings 的 file_path/line/target_id 必须来自 TARGET_HUNKS；禁止照抄 OUTPUT_JSON 中的占位说明或主任务默认文件。",
            "每条 candidate_finding 只能描述一个具体问题、一个主文件和一个主代码位置；不要把多个文件、多个风险点或多个修复方向合并成一条。",
            "如果同一 hunk 存在多个问题，请拆成多条 candidate_findings；每条的 title、evidence、reason、suggested_code 必须互相指向同一问题。",
            "如果缺少上下文但存在可疑代码证据，必须同时输出 candidate_findings 和 context_requests，不要静默省略。",
            "禁止输出 legacy {\"findings\":[...]}；缺少 rule_check_results 或 candidate_findings 会被系统拒收。",
            "所有面向用户的说明使用中文；normalized_issue_type、代码标识、路径可保留英文。",
            "",
            "[TASK]",
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}",
            f"专家: {expert.expert_id} / {expert.name_zh}",
            f"目标文件: {file_path}",
            f"目标行号: {line_start}",
            f"主Agent派工理由: {str(repository_context.get('routing_reason') or '').strip() or '未提供'}",
            f"必查项: {' / '.join(expected_checks[:5]) or expert.role}",
            f"候选边界: {disallowed_text or '不要把没有当前变更代码位置的猜测输出为候选；有当前代码证据但缺上下文时，保留 candidate_findings 并写 context_requests。'}",
            "",
            "[EXPERT_PROFILE]",
            f"专家职责说明:\n{expert_scope_summary}",
            f"专家审视规范摘要:\n{expert_review_spec_summary}",
            f"语言通用规范:\n{language_general_guidance}",
            f"Java/DDD 增强提示:\n{java_ddd_focus or '未命中 Java/DDD 增强提示。'}",
            "",
            "[RULE_CARDS]",
            self._build_rule_guided_rule_cards(rule_screening, expected_checks, max_rules_per_prompt=max_rules_per_prompt),
            "",
            "[QUALITY_INPUTS]",
            f"已激活技能:\n{context_packet['active_skills']}",
            f"运行时工具调用结果:\n{context_packet['runtime_tools']}",
            f"本次审核绑定的详细设计文档:\n{self._build_design_doc_summary(subject)}",
            f"输入完整性校验:\n{input_completeness_summary}",
            f"结构化观察点:\n{observation_review_summary}",
            f"历史人工反馈:\n{review_learning_hints or '当前目标文件未命中可复用的历史人工反馈案例。'}",
            f"附加产品/仓库规则遍历结果:\n{self._build_rule_screening_summary(rule_screening or {})}",
            "审查阶段说明:\n规则阶段：逐条检查 RULE_CARDS 和专家绑定规范；通用阶段：按专家画像、专家审视规范和语言通用规范扫描目标 hunk；两部分候选取并集，最终由收敛层去重。",
            source_context_note,
            f"目标文件完整 diff:\n{target_file_full_diff}",
            f"其他变更文件摘要:\n{related_diff_summary}",
            "",
            "[CONTEXT_PACKET]",
            self._compact_prompt_block(
                json.dumps(context_packet, ensure_ascii=False, indent=2),
                max_context_chars,
            ),
            "",
            "[OUTPUT_JSON]",
            json.dumps(output_contract, ensure_ascii=False, indent=2),
            "只输出一个 JSON 对象，不要输出 Markdown，不要添加额外解释。",
        ]
        return "\n".join(lines)

    def _build_rule_guided_rule_cards(
        self,
        rule_screening: dict[str, object],
        expected_checks: list[str],
        *,
        max_rules_per_prompt: int,
    ) -> str:
        matched_rules = [
            item
            for item in list(rule_screening.get("matched_rules_for_llm") or [])
            if isinstance(item, dict)
        ][: max(1, max_rules_per_prompt)]
        cards: list[dict[str, object]] = []
        for item in matched_rules:
            rule_id = str(item.get("rule_id") or item.get("id") or "").strip()
            if not rule_id:
                continue
            cards.append(
                {
                    "rule_id": rule_id,
                    "title": str(item.get("title") or "").strip(),
                    "severity": str(item.get("priority") or item.get("severity") or "P2").strip(),
                    "must_check": self._extract_rule_guided_rule_list(item, "must_check_items", "must_check", fallback=[]),
                    "required_context": self._extract_rule_guided_rule_list(
                        item,
                        "required_context",
                        fallback=["changed_file_full_content", "repository_context"],
                    ),
                    "evidence_required": self._extract_rule_guided_rule_list(
                        item,
                        "evidence_required",
                        fallback=["明确代码行", "违反规则的原因"],
                    ),
                    "false_positive_guards": self._extract_rule_guided_rule_list(item, "false_positive_guards", fallback=[]),
                    "normalized_issue_type": str(item.get("normalized_issue_type") or "").strip(),
                }
            )
        if not cards:
            cards.append(
                {
                    "rule_id": "GENERAL-EXPERT-CHECKS",
                    "title": "专家通用必查项",
                    "severity": "P2",
                    "must_check": [str(item).strip() for item in expected_checks if str(item).strip()],
                    "required_context": ["target_hunks", "current_code_excerpt", "compact_repository_context"],
                    "evidence_required": ["具体代码行", "违反专家职责或通用规范的原因"],
                    "false_positive_guards": [
                        "缺少直接代码证据时不要输出候选",
                        "不能只因为缺少完整文件、Schema 或配置就把已能由当前 hunk 证明的问题写成待确认",
                    ],
                    "normalized_issue_type": "general_expert_rule_violation",
                }
            )
        return json.dumps(cards, ensure_ascii=False, indent=2)

    def _extract_rule_guided_rule_list(
        self,
        source: dict[str, object],
        *keys: str,
        fallback: list[str],
    ) -> list[str]:
        values: list[str] = []
        for key in keys:
            raw = source.get(key)
            if isinstance(raw, list):
                values.extend(str(item).strip() for item in raw if str(item).strip())
            elif str(raw or "").strip():
                values.append(str(raw).strip())
        if not values:
            values = list(fallback)
        return list(dict.fromkeys(values))

    def _build_expert_language_general_guidance(self, language: str, expert_id: str) -> str:
        """按专家职责裁剪语言通用规范，避免弱模型把其他专家的问题串到当前专家。"""

        normalized_language = str(language or "").strip().lower()
        normalized_expert = str(expert_id or "").strip().lower()
        if normalized_language != "java":
            return self._build_language_general_guidance(language)

        base_lines = [
            "- 以《阿里巴巴 Java 开发手册》作为 Java 代码最低通用规范基线；本专家只审查自己职责边界内的问题。",
            "- 结论必须绑定当前 `| +` 代码行或由当前新增代码直接暴露的问题；不能把 `| -` 删除代码当作当前问题。",
            "- 若结论依赖调用链、ORM 映射或事务传播，必须结合已提供源码上下文和工具证据；证据不足的条目不要输出。",
        ]
        expert_lines: dict[str, list[str]] = {
            "correctness_business": [
                "- 重点检查业务规则、状态流转、交易金额、订单/支付/退款/库存状态、返回语义、异常分支、副作用和注释/TODO/接口承诺是否真正落地。",
                "- 不主提命名、代码风格、索引、分页、N+1、事务性能等问题；这些交给通用编码规范、数据库或性能专家。",
            ],
            "ddd_architecture": [
                "- 重点检查聚合工厂、聚合边界、分层职责、依赖方向、领域事件和不变量是否被绕过，尤其是订单、支付、库存、账户等核心聚合。",
                "- 不主提命名、普通代码风格、SQL 分页、N+1、循环写入等问题；除非它们直接破坏 DDD 边界。",
            ],
            "architecture_design": [
                "- 重点检查命名、常量、判空、异常写法、日志字段、集合/枚举使用和阿里巴巴 Java 开发手册范围内的通用编码规范问题。",
                "- 不主提业务语义、DDD 边界、SQL/事务、系统性能或安全漏洞问题；这些交给对应专项专家。",
            ],
            "database_analysis": [
                "- 重点检查 Repository / JPA / MyBatis / Criteria / EntityManager 查询是否存在查询范围放宽、无分页、全表扫描、N+1、批量逐条写、索引或事务一致性风险。",
                "- 对订单、支付、库存、账户等核心表写入，要核对事务、唯一约束、版本号、锁和回滚语义是否支撑交易一致性。",
                "- 不主提聚合工厂、领域事件、业务承诺未实现或命名规范问题，除非它们直接导致数据一致性问题。",
            ],
            "performance_reliability": [
                "- 重点检查批量交易、支付回调、库存预占、订单状态推进中的循环体 Repository / Service / Client / HTTP / SQL / MQ 调用、批量串行放大、事务内外部 IO、锁保护移除和可靠性降级。",
                "- 不主提聚合边界、普通命名、SQL 语义正确性或业务承诺未实现，除非它们直接导致性能或可靠性风险。",
            ],
            "maintainability_code_health": [
                "- 重点检查复杂度、方法职责、重复代码、隐式耦合、难测结构和长期演化成本，尤其是 Controller/Service 同时承担校验、编排、持久化、缓存、MQ 和外部调用。",
                "- 不主提命名、魔法值、日志、普通判空等通用编码规范问题，也不主提业务语义、DDD 边界、数据库性能或可靠性问题。",
            ],
            "security_compliance": [
                "- 重点检查 Controller/API 入口、输入校验、权限/租户隔离、资源级鉴权、回调验签、敏感信息、SQL/Like 注入风险、日志脱敏和合规边界。",
                "- 对订单、支付、退款、账户、库存等资源查询或操作，必须关注是否从用户/租户/资源组合校验退化为只按 id、状态或模糊条件处理。",
                "- 不主提普通性能、DDD 边界或命名规范问题，除非它们直接造成安全或合规风险。",
            ],
            "test_verification": [
                "- 重点检查订单、支付、退款、库存、权限、幂等、事务回滚、消息重试、缓存一致性、批量和并发等关键路径是否有测试保护。",
                "- 不主提生产代码实现风格、SQL 性能或 DDD 边界本身；只评价验证缺口和测试建议。",
            ],
        }
        return "\n".join([*base_lines, *expert_lines.get(normalized_expert, [])])

    def _extract_review_learning_issue_types(
        self,
        java_quality: dict[str, object],
        rule_screening: dict[str, object],
    ) -> list[str]:
        issue_types: list[str] = []
        for signal in list(java_quality.get("signals") or []):
            normalized = str(signal.get("normalized_issue_type") or "").strip() if isinstance(signal, dict) else ""
            if normalized:
                issue_types.append(normalized)
        for observation in list(java_quality.get("observations") or []):
            normalized = (
                str(observation.get("normalized_issue_type") or "").strip()
                if isinstance(observation, dict)
                else ""
            )
            if normalized:
                issue_types.append(normalized)
        for finding in list(rule_screening.get("findings") or []):
            normalized = str(finding.get("normalized_issue_type") or "").strip() if isinstance(finding, dict) else ""
            if normalized:
                issue_types.append(normalized)
        return list(dict.fromkeys(issue_types))

    def _normalize_expert_batch_items(
        self,
        batch_items: list[dict[str, object]] | None,
        *,
        fallback_file_path: str,
        fallback_line_start: int,
        fallback_repository_context: dict[str, object] | None,
        fallback_target_hunk: dict[str, object] | None,
        fallback_target_hunks: list[dict[str, object]] | None,
        fallback_related_files: list[str] | None,
    ) -> list[dict[str, object]]:
        normalized = [
            dict(item)
            for item in list(batch_items or [])
            if isinstance(item, dict) and str(item.get("file_path") or "").strip()
        ]
        if normalized:
            return normalized
        return [
            {
                "file_path": str(fallback_file_path or "").strip(),
                "line_start": int(fallback_line_start or 1),
                "repository_context": dict(fallback_repository_context or {}),
                "target_hunk": dict(fallback_target_hunk or {}),
                "target_hunks": [
                    dict(item)
                    for item in list(fallback_target_hunks or [])
                    if isinstance(item, dict)
                ],
                "related_files": [str(item).strip() for item in list(fallback_related_files or []) if str(item).strip()],
            }
        ]

    def _count_batch_hunks(
        self,
        batch_items: list[dict[str, object]],
        *,
        fallback_target_hunks: list[dict[str, object]] | None = None,
    ) -> int:
        count = 0
        for item in batch_items:
            count += len(
                [
                    hunk
                    for hunk in list(item.get("target_hunks") or [])
                    if isinstance(hunk, dict)
                ]
            )
        if count > 0:
            return count
        return len([item for item in list(fallback_target_hunks or []) if isinstance(item, dict)])

    def _find_batch_item_for_file(
        self,
        batch_items: list[dict[str, object]],
        file_path: str,
    ) -> dict[str, object] | None:
        normalized_file_path = str(file_path or "").strip()
        if not normalized_file_path:
            return None
        for item in batch_items:
            if str(item.get("file_path") or "").strip() == normalized_file_path:
                return item
        return None

    def _resolve_finding_file_path(
        self,
        parsed: dict[str, object],
        *,
        fallback_file_path: str,
        batch_items: list[dict[str, object]],
    ) -> str:
        allowed_paths = sorted(
            {
            str(item.get("file_path") or "").strip()
            for item in batch_items
            if str(item.get("file_path") or "").strip()
            }
        )
        parsed_path = str(parsed.get("file_path") or "").strip()
        if parsed_path and parsed_path in set(allowed_paths):
            return parsed_path
        if len(allowed_paths) <= 1:
            if fallback_file_path in set(allowed_paths):
                return str(fallback_file_path)
            return allowed_paths[0] if allowed_paths else str(fallback_file_path)
        inferred_path = self._infer_finding_file_path_from_batch_semantics(parsed, batch_items)
        if inferred_path:
            return inferred_path
        # 多文件批量下如果无法可靠定位具体文件，宁可丢弃该 finding，避免串线污染。
        return ""

    def _infer_finding_file_path_from_batch_semantics(
        self,
        parsed: dict[str, object],
        batch_items: list[dict[str, object]],
    ) -> str | None:
        semantic_parts: list[str] = []
        for key in ("title", "claim", "summary", "fix_strategy", "suggested_fix", "rule_based_reasoning"):
            value = str(parsed.get(key) or "").strip()
            if value:
                semantic_parts.append(value)
        for key in ("evidence", "cross_file_evidence", "assumptions", "change_steps", "matched_rules", "violated_guidelines"):
            semantic_parts.extend(str(item).strip() for item in list(parsed.get(key) or []) if str(item).strip())
        finding_tokens = self._extract_anchor_tokens("\n".join(semantic_parts))
        if not finding_tokens:
            return None

        scored: list[tuple[str, int]] = []
        for item in batch_items:
            file_path = str(item.get("file_path") or "").strip()
            if not file_path:
                continue
            target_hunk = dict(item.get("target_hunk") or {})
            hunk_tokens = self._extract_anchor_tokens(
                "\n".join(
                    [
                        file_path,
                        str(target_hunk.get("hunk_header") or ""),
                        str(target_hunk.get("excerpt") or ""),
                    ]
                )
            )
            overlap = finding_tokens & hunk_tokens
            score = 0
            for token in overlap:
                score += 3 if len(token) >= 8 or any(char.isdigit() for char in token) else 1
            scored.append((file_path, score))
        if not scored:
            return None
        scored.sort(key=lambda item: item[1], reverse=True)
        best_path, best_score = scored[0]
        second_score = scored[1][1] if len(scored) > 1 else 0
        if best_score <= 0:
            return None
        if second_score and best_score == second_score:
            return None
        return best_path

    def _build_multi_file_prompt_appendix(
        self,
        subject: ReviewSubject,
        expert: ExpertProfile,
        batch_items: list[dict[str, object]],
        *,
        analysis_mode: Literal["standard", "light"] = "standard",
    ) -> str:
        is_light = analysis_mode == "light"
        lines: list[str] = []
        lines.append("【多文件联合审查补充】")
        lines.append(
            "本轮为多文件批量审查，请在同一次输出中覆盖所有高风险点；同一文件可以返回多个问题。"
        )
        lines.append("本轮批量文件清单（file_path 只能从这里选）：")
        for index, item in enumerate(batch_items, start=1):
            file_path = str(item.get("file_path") or "").strip()
            line_start = int(item.get("line_start") or 1)
            target_hunk = dict(item.get("target_hunk") or {})
            target_hunks = [dict(hunk) for hunk in list(item.get("target_hunks") or []) if isinstance(hunk, dict)]
            repository_context = dict(item.get("repository_context") or {})
            lines.append(f"{index}. {file_path} @L{line_start}")
            lines.append(f"   重点 hunk:\n{self._build_hunk_summary(target_hunk)}")
            lines.append(f"   同文件 hunk:\n{self._build_hunk_batch_summary(target_hunks)}")
            lines.append(
                f"   代码仓上下文摘要:\n{self._compact_prompt_block(self._build_repository_context_summary(repository_context, []), 1800 if is_light else 3200)}"
            )
            lines.append(
                f"   结构化观察点:\n{self._build_observation_review_summary(repository_context)}"
            )
            if not is_light:
                lines.append(f"   目标文件完整 diff:\n{self._build_target_file_full_diff(subject, file_path)}")
                lines.append(
                    f"   当前代码片段:\n{self._compact_prompt_block(self._build_code_excerpt(subject, file_path, line_start, expert.expert_id), 2200)}"
                )
        lines.append("JSON 字段补充：每条 finding 都必须包含 file_path（来自上述清单），并给出该文件对应的 line_start/line_end。")
        lines.append("多 hunk 强约束：每条 finding 的 line_start/line_end 必须落在该文件某个 hunk 的 changed_lines/start_line/end_line 范围。")
        lines.append("当前代码强约束：hunk 中 `| +` 是合入后的当前代码，`| -` 是旧代码；不要把只存在于 `| -` 的旧代码问题输出为 finding。")
        lines.append("无法定位到明确 hunk 行号的结论，不要输出。")
        return "\n".join(lines)

    def _build_review_input_completeness_summary(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        *,
        expert: ExpertProfile,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        language: str,
    ) -> str:
        payload = self._build_review_input_completeness(
            subject,
            file_path,
            line_start,
            repository_context,
            expert=expert,
            bound_documents=bound_documents,
            rule_screening=rule_screening,
            language=language,
        )
        lines = [
            f"- 专家规范: {'已提供' if payload['review_spec_present'] else '缺失'}",
            f"- 语言通用规范提示: {'已提供' if payload['language_guidance_present'] else '缺失'}",
            f"- 绑定规则: {payload['matched_rule_count']} 条命中 / {payload['enabled_rule_count']} 条启用",
            f"- 绑定参考文档: {payload['bound_document_count']} 份",
            f"- 变更代码原文: {'已提供' if payload['target_file_diff_present'] else '缺失'}",
            f"- 当前源码上下文: {'已提供' if payload['source_context_present'] else '缺失'}",
            f"- 关联源码上下文: {payload['related_context_count']} 段",
        ]
        missing_sections = list(payload.get("missing_sections") or [])
        if missing_sections:
            lines.append(f"- 缺失项: {' / '.join(missing_sections[:6])}")
            lines.append("- 若结论依赖缺失项，不要输出不确定结论；仅输出可由当前证据直接证明的问题。")
        else:
            lines.append("- 当前未缺失关键输入，可基于规范、规则、变更代码和关联上下文直接审查。")
        return "\n".join(lines)

    def _prepare_prompt_repository_context(
        self,
        *,
        expert: ExpertProfile,
        repository_context: dict[str, object],
        rule_screening: dict[str, object],
        analysis_mode: Literal["standard", "light"],
    ) -> dict[str, object]:
        context = dict(repository_context or {})
        observations = self._normalize_review_observations(context.get("review_observations"))
        section_order = self._determine_prompt_context_section_order(
            expert_id=expert.expert_id,
            rule_screening=rule_screening,
            observations=observations,
        )
        focused_observations = self._prioritize_observations_for_expert(
            observations,
            expert_id=expert.expert_id,
            rule_screening=rule_screening,
        )
        context["prompt_context_section_order"] = section_order
        context["review_observations"] = focused_observations
        context["risk_candidates"] = RiskCandidateService().build(
            change_understanding=dict(context.get("change_understanding") or {}),
            tool_observations=[
                dict(item)
                for item in list(context.get("tool_observations") or [])
                if isinstance(item, dict)
            ],
            code_graph_context=context,
            feedback_quality_profiles=dict(context.get("feedback_quality_profiles") or {}),
            existing_candidates=[
                dict(item)
                for item in list(context.get("risk_candidates") or [])
                if isinstance(item, dict)
            ],
        )
        if analysis_mode == "light":
            context = self._trim_prompt_repository_context_for_light(
                context,
                section_order,
                expert_id=expert.expert_id,
                rule_screening=rule_screening,
            )
        return context

    def _determine_prompt_context_section_order(
        self,
        *,
        expert_id: str,
        rule_screening: dict[str, object],
        observations: list[dict[str, object]],
    ) -> list[str]:
        default_order = [
            "current_class_context",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "transaction_context",
            "persistence_contexts",
            "parent_contract_contexts",
            "related_contexts",
            "related_source_snippets",
            "symbol_contexts",
        ]
        by_expert: dict[str, list[str]] = {
            "security_compliance": [
                "caller_contexts",
                "callee_contexts",
                "persistence_contexts",
                "transaction_context",
                "current_class_context",
                "domain_model_contexts",
            ],
            "performance_reliability": [
                "transaction_context",
                "persistence_contexts",
                "callee_contexts",
                "caller_contexts",
                "current_class_context",
                "domain_model_contexts",
            ],
            "database_analysis": [
                "persistence_contexts",
                "transaction_context",
                "callee_contexts",
                "current_class_context",
                "caller_contexts",
                "domain_model_contexts",
            ],
            "architecture_design": [
                "current_class_context",
                "parent_contract_contexts",
                "caller_contexts",
                "same_file_other_hunks",
                "callee_contexts",
                "domain_model_contexts",
            ],
            "ddd_architecture": [
                "domain_model_contexts",
                "current_class_context",
                "caller_contexts",
                "callee_contexts",
                "transaction_context",
                "parent_contract_contexts",
            ],
            "ddd_specification": [
                "domain_model_contexts",
                "current_class_context",
                "caller_contexts",
                "callee_contexts",
                "transaction_context",
                "parent_contract_contexts",
            ],
            "correctness_business": [
                "current_class_context",
                "caller_contexts",
                "callee_contexts",
                "transaction_context",
                "domain_model_contexts",
            ],
            "maintainability_code_health": [
                "current_class_context",
                "parent_contract_contexts",
                "caller_contexts",
                "callee_contexts",
                "domain_model_contexts",
            ],
        }
        order = list(by_expert.get(expert_id, default_order))
        observation_kinds = {str(item.get("kind") or "").strip() for item in observations if str(item.get("kind") or "").strip()}
        matched_rule_blob = " ".join(
            [
                str(item.get("rule_id") or "").strip()
                + " "
                + str(item.get("title") or "").strip()
                + " "
                + str(item.get("reason") or "").strip()
                for item in list(rule_screening.get("matched_rules_for_llm") or [])[:8]
                if isinstance(item, dict)
            ]
        ).lower()
        if observation_kinds & {
            "query_plan_risk",
            "query_semantics_changed",
            "bulk_processing_boundary_missing",
            "transactional_side_effect",
        } or any(token in matched_rule_blob for token in ("sql", "query", "transaction", "batch")):
            for key in ("persistence_contexts", "transaction_context"):
                if key in order:
                    order.remove(key)
                order.insert(0, key)
        if observation_kinds & {
            "cross_layer_dependency",
            "control_flow_with_external_call",
            "declared_intent_without_implementation",
        } or any(token in matched_rule_blob for token in ("aggregate", "applicationservice", "controller", "domain")):
            for key in ("caller_contexts", "callee_contexts", "domain_model_contexts"):
                if key in order:
                    order.remove(key)
                order.insert(0, key)
        final_order: list[str] = []
        for key in order + default_order:
            if key not in final_order:
                final_order.append(key)
        return final_order

    def _prioritize_observations_for_expert(
        self,
        observations: list[dict[str, object]],
        *,
        expert_id: str,
        rule_screening: dict[str, object],
    ) -> list[dict[str, object]]:
        if not observations:
            return []
        preferred_kinds: dict[str, set[str]] = {
            "security_compliance": {"query_semantics_changed", "cross_layer_dependency", "error_semantics_changed"},
            "performance_reliability": {"control_flow_with_external_call", "bulk_processing_boundary_missing", "query_plan_risk", "transactional_side_effect"},
            "database_analysis": {"query_plan_risk", "bulk_processing_boundary_missing", "transactional_side_effect", "query_semantics_changed"},
            "architecture_design": {"weak_identifier_signal", "literal_embedded_in_business_logic", "declared_intent_without_implementation"},
            "ddd_architecture": {"cross_layer_dependency", "transactional_side_effect", "declared_intent_without_implementation"},
            "ddd_specification": {"cross_layer_dependency", "transactional_side_effect", "declared_intent_without_implementation"},
            "correctness_business": {"declared_intent_without_implementation", "error_semantics_changed", "query_semantics_changed"},
            "maintainability_code_health": {"weak_identifier_signal", "literal_embedded_in_business_logic", "declared_intent_without_implementation"},
        }
        matched_rule_blob = " ".join(
            [
                str(item.get("rule_id") or "").strip()
                + " "
                + str(item.get("title") or "").strip()
                for item in list(rule_screening.get("matched_rules_for_llm") or [])[:8]
                if isinstance(item, dict)
            ]
        ).lower()
        preferred = set(preferred_kinds.get(expert_id, set()))
        if "sql" in matched_rule_blob or "query" in matched_rule_blob:
            preferred.update({"query_plan_risk", "query_semantics_changed"})
        if "transaction" in matched_rule_blob:
            preferred.add("transactional_side_effect")
        if "aggregate" in matched_rule_blob or "domain" in matched_rule_blob:
            preferred.update({"cross_layer_dependency", "declared_intent_without_implementation"})

        def _score(item: dict[str, object]) -> tuple[int, int]:
            kind = str(item.get("kind") or "").strip()
            score = 3 if kind in preferred else 0
            score += 1 if float(item.get("confidence") or 0.0) >= 0.75 else 0
            return (score, -int(item.get("line_start") or 0))

        ordered = sorted(observations, key=_score, reverse=True)
        return ordered[:8]

    def _trim_prompt_repository_context_for_light(
        self,
        repository_context: dict[str, object],
        section_order: list[str],
        *,
        expert_id: str = "",
        rule_screening: dict[str, object] | None = None,
    ) -> dict[str, object]:
        context = dict(repository_context or {})
        blocks = self._build_prompt_repository_context_blocks(
            repository_context=context,
            section_order=section_order,
            expert_id=expert_id,
            rule_screening=rule_screening or {},
        )
        total_budget = max(
            600,
            int(os.getenv("REVIEW_LIGHT_REPOSITORY_CONTEXT_BUDGET_TOKENS", "1800") or 1800),
        )
        plan = self.prompt_budget_planner.plan(
            blocks,
            expert_id=expert_id,
            total_budget=total_budget,
        )
        kept_ids = {block.block_id for block in plan.kept_blocks}
        list_sections = {
            "related_contexts",
            "related_source_snippets",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "persistence_contexts",
            "parent_contract_contexts",
            "symbol_contexts",
            "review_observations",
        }
        scalar_sections = {
            "primary_context",
            "current_class_context",
            "transaction_context",
            "target_hunk",
        }
        for key in list_sections:
            value = context.get(key)
            if isinstance(value, list):
                kept_items = []
                for index, item in enumerate(value):
                    block_id = f"{key}:{index}"
                    if block_id in kept_ids:
                        kept_items.append(dict(item) if isinstance(item, dict) else item)
                context[key] = kept_items
        for key in scalar_sections:
            if context.get(key) and f"{key}:0" not in kept_ids:
                context.pop(key, None)
        context["prompt_context_budget"] = {
            "total_budget": plan.total_budget,
            "used_budget": plan.used_budget,
            "must_keep_blocks": [block.block_id for block in plan.must_keep_blocks],
            "kept_blocks": [block.block_id for block in plan.kept_blocks],
            "compressed_blocks": [block.block_id for block in plan.compressed_blocks],
            "dropped_blocks": [block.block_id for block in plan.dropped_blocks],
        }
        return context

    def _build_prompt_repository_context_blocks(
        self,
        *,
        repository_context: dict[str, object],
        section_order: list[str],
        expert_id: str,
        rule_screening: dict[str, object],
    ) -> list[ContextBlock]:
        blocks: list[ContextBlock] = []

        def _append_scalar_block(section_key: str, payload: dict[str, object], *, fallback_summary: str = "") -> None:
            snippet = str(payload.get("snippet") or payload.get("excerpt") or payload.get("transaction_boundary_snippet") or "").strip()
            if not snippet:
                return
            block_type = self._prompt_context_section_block_type(section_key)
            priority = priority_for_block_type(block_type)
            must_keep = priority == "P0"
            blocks.append(
                ContextBlock(
                    block_id=f"{section_key}:0",
                    type=block_type,
                    priority=priority,
                    expert_relevance=self._estimate_expert_relevance_for_context_block(
                        section_key,
                        expert_id=expert_id,
                        rule_screening=rule_screening,
                    ),
                    evidence_strength=0.9 if section_key in {"target_hunk", "primary_context"} else 0.7,
                    must_keep=must_keep,
                    token_cost=self._estimate_context_block_token_cost(snippet),
                    source="repository_context",
                    file_path=str(payload.get("path") or payload.get("transactional_path") or "").strip(),
                    line_start=int(payload.get("line_start") or payload.get("start_line") or 1),
                    line_end=int(payload.get("line_end") or payload.get("end_line") or payload.get("start_line") or 1),
                    summary=fallback_summary or str(payload.get("symbol") or payload.get("class_name") or section_key),
                    content=snippet,
                )
            )

        def _append_list_blocks(section_key: str, values: list[object]) -> None:
            block_type = self._prompt_context_section_block_type(section_key)
            priority = priority_for_block_type(block_type)
            for index, item in enumerate(values):
                if not isinstance(item, dict):
                    continue
                snippet = str(item.get("snippet") or "").strip()
                if not snippet and section_key != "symbol_contexts" and section_key != "review_observations":
                    continue
                if section_key == "symbol_contexts":
                    snippet = self._build_symbol_context_snippet(item)
                    if not snippet:
                        continue
                if section_key == "review_observations":
                    snippet = self._build_single_observation_summary(item)
                    if not snippet:
                        continue
                blocks.append(
                    ContextBlock(
                        block_id=f"{section_key}:{index}",
                        type=block_type,
                        priority=priority_for_block_type(block_type),
                        expert_relevance=self._estimate_expert_relevance_for_context_block(
                            section_key,
                            expert_id=expert_id,
                            rule_screening=rule_screening,
                        ),
                        evidence_strength=float(item.get("confidence") or 0.85 if section_key == "review_observations" else 0.65),
                        must_keep=False,
                        token_cost=self._estimate_context_block_token_cost(snippet),
                        source="repository_context",
                        file_path=str(item.get("path") or item.get("file_path") or "").strip(),
                        line_start=int(item.get("line_start") or 1),
                        line_end=int(item.get("line_end") or item.get("line_start") or 1),
                        summary=str(item.get("symbol") or item.get("summary") or section_key),
                        content=snippet,
                        related_observation_ids=[
                            str(item.get("observation_id") or "").strip()
                        ]
                        if section_key == "review_observations" and str(item.get("observation_id") or "").strip()
                        else [],
                    )
                )

        primary_context = repository_context.get("primary_context")
        if isinstance(primary_context, dict):
            _append_scalar_block("primary_context", primary_context, fallback_summary="目标文件源码")
        current_class_context = repository_context.get("current_class_context")
        if isinstance(current_class_context, dict):
            _append_scalar_block("current_class_context", current_class_context, fallback_summary="当前类问题片段")
        transaction_context = repository_context.get("transaction_context")
        if isinstance(transaction_context, dict):
            _append_scalar_block("transaction_context", transaction_context, fallback_summary="事务边界")
        target_hunk = repository_context.get("target_hunk")
        if isinstance(target_hunk, dict):
            _append_scalar_block("target_hunk", target_hunk, fallback_summary="目标 hunk")
        for section_key in section_order:
            values = repository_context.get(section_key)
            if isinstance(values, list):
                _append_list_blocks(section_key, values)
        observations = repository_context.get("review_observations")
        if isinstance(observations, list):
            _append_list_blocks("review_observations", observations)
        return blocks

    def _prompt_context_section_block_type(self, section_key: str) -> str:
        mapping = {
            "primary_context": "current_code",
            "target_hunk": "target_hunk",
            "current_class_context": "current_class_context",
            "caller_contexts": "caller_context",
            "callee_contexts": "callee_context",
            "transaction_context": "transaction_context",
            "persistence_contexts": "persistence_context",
            "domain_model_contexts": "domain_model_context",
            "parent_contract_contexts": "parent_contract_context",
            "related_contexts": "related_source_snippet",
            "related_source_snippets": "related_source_snippet",
            "symbol_contexts": "symbol_context",
            "review_observations": "critical_observations",
        }
        return mapping.get(str(section_key or "").strip(), "secondary_context_note")

    def _estimate_context_block_token_cost(self, text: str) -> int:
        raw = str(text or "").strip()
        if not raw:
            return 0
        return max(1, (len(raw) + 3) // 4)

    def _estimate_expert_relevance_for_context_block(
        self,
        section_key: str,
        *,
        expert_id: str,
        rule_screening: dict[str, object],
    ) -> float:
        top_sections = self._determine_prompt_context_section_order(
            expert_id=expert_id,
            rule_screening=rule_screening,
            observations=[],
        )[:4]
        if section_key in {"primary_context", "target_hunk"}:
            return 1.0
        if section_key in top_sections[:2]:
            return 0.95
        if section_key in top_sections:
            return 0.82
        return 0.55

    def _build_symbol_context_snippet(self, item: dict[str, object]) -> str:
        lines: list[str] = []
        symbol = str(item.get("symbol") or "").strip()
        if symbol:
            lines.append(f"symbol: {symbol}")
        for definition in list(item.get("definitions") or [])[:2]:
            if not isinstance(definition, dict):
                continue
            path = str(definition.get("path") or "").strip()
            snippet = str(definition.get("snippet") or "").strip()
            if path and snippet:
                lines.append(f"definition: {path}")
                lines.extend(snippet.splitlines()[:6])
        for reference in list(item.get("references") or [])[:2]:
            if not isinstance(reference, dict):
                continue
            path = str(reference.get("path") or "").strip()
            snippet = str(reference.get("snippet") or "").strip()
            if path and snippet:
                lines.append(f"reference: {path}")
                lines.extend(snippet.splitlines()[:4])
        return "\n".join(lines).strip()

    def _build_single_observation_summary(self, item: dict[str, object]) -> str:
        observation_id = str(item.get("observation_id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        summary = str(item.get("summary") or "").strip()
        parts = [part for part in (observation_id, kind, summary) if part]
        if not parts:
            return ""
        return " | ".join(parts)

    def _build_review_input_completeness(
        self,
        subject: ReviewSubject,
        file_path: str,
        line_start: int,
        repository_context: dict[str, object],
        *,
        expert: ExpertProfile | None,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        language: str,
    ) -> dict[str, object]:
        target_file_diff_present = bool(self._build_target_file_full_diff(subject, file_path).strip())
        language_guidance_present = bool(self._build_language_general_guidance(language).strip())
        primary_context = repository_context.get("primary_context")
        current_class_context = repository_context.get("current_class_context")
        target_hunk_context = repository_context.get("target_hunk")
        target_hunks_context = [
            item
            for item in list(repository_context.get("target_hunks") or [])[:6]
            if isinstance(item, dict)
        ]
        source_context_present = bool(
            self._load_repository_problem_context(subject, file_path, line_start, {}).get("snippet")
            or self._load_repository_source_excerpt(subject, file_path, line_start).strip()
            or (self._context_item_has_snippet(primary_context))
            or (self._context_item_has_snippet(current_class_context))
            or str(repository_context.get("target_hunk_excerpt") or "").strip()
            or (self._context_item_has_snippet(target_hunk_context))
            or any(self._context_item_has_snippet(item) for item in target_hunks_context)
        )
        related_context_count = sum(
            1
            for collection in [
                list(repository_context.get("related_source_snippets") or []),
                list(repository_context.get("related_contexts") or []),
                list(repository_context.get("caller_contexts") or []),
                list(repository_context.get("callee_contexts") or []),
                list(repository_context.get("domain_model_contexts") or []),
                list(repository_context.get("persistence_contexts") or []),
            ]
            for item in collection[:6]
            if self._context_item_has_snippet(item)
        )
        enabled_rule_count = int(rule_screening.get("enabled_rules") or 0)
        matched_rule_count = len(list(rule_screening.get("matched_rules_for_llm") or []))
        review_spec_present = bool(str(getattr(expert, "review_spec", "") or "").strip())
        bound_document_count = len([item for item in bound_documents if item is not None])
        missing_sections: list[str] = []
        if not review_spec_present:
            missing_sections.append("专家规范")
        if not language_guidance_present:
            missing_sections.append("语言通用规范提示")
        if enabled_rule_count <= 0:
            missing_sections.append("绑定规则")
        if not target_file_diff_present:
            missing_sections.append("变更代码原文")
        if not source_context_present:
            missing_sections.append("当前源码上下文")
        if related_context_count <= 0:
            missing_sections.append("关联源码上下文")
        return {
            "review_spec_present": review_spec_present,
            "language_guidance_present": language_guidance_present,
            "enabled_rule_count": enabled_rule_count,
            "matched_rule_count": matched_rule_count,
            "bound_document_count": bound_document_count,
            "target_file_diff_present": target_file_diff_present,
            "source_context_present": source_context_present,
            "related_context_count": related_context_count,
            "missing_sections": missing_sections,
        }

    def _build_review_input_trace(
        self,
        *,
        expert: ExpertProfile | None,
        bound_documents: list[object],
        rule_screening: dict[str, object],
        repository_context: dict[str, object],
        language: str,
    ) -> dict[str, object]:
        matched_rules = []
        for item in list(rule_screening.get("matched_rules_for_llm") or [])[:8]:
            if not isinstance(item, dict):
                continue
            matched_rules.append(
                {
                    "rule_id": str(item.get("rule_id") or "").strip(),
                    "title": str(item.get("title") or "").strip(),
                    "priority": str(item.get("priority") or "").strip(),
                }
            )
        bound_doc_titles = []
        for item in bound_documents[:6]:
            title = str(getattr(item, "title", "") or "").strip()
            if title:
                bound_doc_titles.append(title)
        cross_file_impact_hints = build_cross_file_impact_hints(
            file_path=str((repository_context.get("primary_context") or {}).get("path") or ""),
            repository_context=repository_context,
        )
        return {
            "expert_id": str(getattr(expert, "expert_id", "") or "").strip(),
            "review_spec_present": bool(str(getattr(expert, "review_spec", "") or "").strip()),
            "language_guidance_language": str(language or "").strip(),
            "language_guidance_present": bool(self._build_language_general_guidance(language).strip()),
            "language_guidance_topics": self._build_language_guidance_topics(language),
            "bound_document_titles": bound_doc_titles,
            "matched_rules": matched_rules,
            "context_files": [
                str(item).strip()
                for item in list(repository_context.get("context_files") or [])[:10]
                if str(item).strip()
            ],
            "cross_file_impact_hints": cross_file_impact_hints,
            "analysis_stages": {
                str(key).strip(): str(value).strip()
                for key, value in dict(repository_context.get("analysis_stages") or {}).items()
                if str(key).strip() and str(value).strip()
            },
        }

    def _build_analysis_stage_summary(self, repository_context: dict[str, object]) -> str:
        stages = {
            str(key).strip(): str(value).strip()
            for key, value in dict(repository_context.get("analysis_stages") or {}).items()
            if str(key).strip() and str(value).strip()
        }
        if not stages:
            return "当前未提供显式阶段化提示，请按规则、上下文和 observation 逐层审查。"
        lines = []
        labels = {
            "rule_stage": "规则阶段",
            "observation_stage": "观察点阶段",
            "llm_stage": "LLM 深审阶段",
        }
        for key in ("rule_stage", "observation_stage", "llm_stage"):
            value = stages.get(key)
            if value:
                lines.append(f"- {labels.get(key, key)}: {value}")
        return "\n".join(lines) if lines else "当前未提供显式阶段化提示，请按规则、上下文和 observation 逐层审查。"

    def _build_language_guidance_topics(self, language: str) -> list[str]:
        normalized = str(language or "").strip().lower()
        if normalized == "java":
            return ["命名与职责", "常量与魔法值", "输入校验与安全边界", "事务与副作用", "Repository/ORM 查询风险"]
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return ["命名与职责", "输入校验与鉴权边界", "异步错误处理", "数据访问与性能边界"]
        return []

    def _build_java_ddd_review_focus(
        self,
        language: str,
        expert_id: str,
        repository_context: dict[str, object],
    ) -> str:
        if language != "java":
            return ""
        java_review_mode = str(repository_context.get("java_review_mode") or "").strip() or "general"
        java_context_signals = [
            str(item).strip()
            for item in list(repository_context.get("java_context_signals") or [])
            if str(item).strip()
        ]
        java_quality_signals = [
            str(item).strip()
            for item in list(repository_context.get("java_quality_signals") or [])
            if str(item).strip()
        ]
        available_sections: list[str] = []
        for key, label in [
            ("current_class_context", "当前类问题片段"),
            ("parent_contract_contexts", "父接口/抽象类"),
            ("caller_contexts", "调用方 Controller/ApplicationService"),
            ("callee_contexts", "被调方 Repository/DomainService"),
            ("domain_model_contexts", "Aggregate/Entity/ValueObject/DomainEvent"),
            ("transaction_context", "事务边界与调用链"),
            ("persistence_contexts", "ORM/SQL/Mapper"),
        ]:
            value = repository_context.get(key)
            if isinstance(value, dict) and value:
                available_sections.append(label)
            if isinstance(value, list) and value:
                available_sections.append(label)
        base = [
            "Java 通用审查要求：",
            f"- 当前模式: {'Java DDD 增强模式' if java_review_mode == 'ddd_enhanced' else 'Java 通用模式'}",
            f"- 可用上下文: {' / '.join(available_sections) or '仅有基础 diff 与代码片段'}",
            f"- 已识别信号: {' / '.join(java_context_signals[:8]) or '未识别到额外 Java 结构信号'}",
            f"- 已识别通用质量信号: {' / '.join(java_quality_signals[:8]) or '未识别到额外 Java 通用质量信号'}",
            "- 不要只基于单个 diff hunk 下结论，必须结合当前类、调用方、被调方、事务边界和持久化层判断。",
        ]
        if expert_id == "security_compliance":
            base.extend(
                [
                    "- 重点检查 Controller/ApplicationService 入口是否完成参数校验、权限校验、租户隔离和敏感字段脱敏。",
                    "- 重点检查 Repository/SQL/Mapper 是否存在拼接查询、越权查询、批量更新越边界、日志泄漏敏感信息。",
                    "- 有当前代码证据但缺少鉴权、租户或输入校验上下文时，保留 candidate_findings，设置 verification_needed=true，并在 context_requests 写清需要补充的上下文。",
                    "- 只有完全没有当前变更代码位置、也无法指出具体安全边界被削弱的猜测，才不要输出候选。",
                ]
            )
        elif expert_id == "performance_reliability":
            base.extend(
                [
                    "- 重点检查事务边界内是否包含远程调用、消息发送、循环写库、多仓储写入和大对象装载。",
                    "- 重点检查 ORM/Mapper 是否引入 N+1、全表扫描、隐式 EAGER 加载、批量场景逐条写入。",
                    "- 必须判断当前聚合/仓储调用链是否会放大锁竞争、超时重试或资源占用。",
                    "- 如果批量更新、回填脚本、事务范围或并发路径可能拉长锁持有时间，可以保留为需要验证的高价值风险，不要因为还要补上下文就直接丢弃。",
                ]
            )
        elif expert_id == "correctness_business":
            base.extend(
                [
                    "- 重点检查注释、方法名、接口说明或 TODO 明确承诺了某个行为，但实现缺失或与承诺不一致的情况。",
                    "- 如果当前改动只保留了说明、占位或半截逻辑，必须明确指出“承诺与实现不一致”这一点。",
                    "- 如果是接口/抽象类新增方法，必须先核对实现类；只要关联上下文显示实现类已 implements 对应接口并提供同名 @Override 方法体，就不要输出“承诺未落地”。",
                    "- 只有能证明具体实现类缺少方法、方法体仍是空/TODO/UnsupportedOperationException，或实现行为和接口说明直接矛盾时，才允许输出 comment_contract_unimplemented。",
                ]
            )
        elif expert_id == "architecture_design":
            base.extend(
                [
                    "- 重点检查命名是否表达真实业务语义，是否出现 tmp/data/value/obj 之类弱语义命名。",
                    "- 重点检查魔法值、硬编码状态值、判空写法、异常语义和关键日志字段是否存在通用高价值问题。",
                    "- 只评论通用编码规范和工程一致性问题，不要越界评论聚合边界、依赖方向或业务规则正确性。",
                ]
            )
        elif expert_id in DDD_ARCHITECTURE_EXPERT_IDS:
            base.extend(
                [
                    "- 重点检查聚合根是否真正维护不变量，ValueObject 是否保持不可变语义。",
                    "- 重点检查 DomainEvent 是否在正确边界发布，Repository 是否只服务聚合根而不是应用层拼装。",
                    "- 必须说明当前改动是落在领域层、应用层还是基础设施层，以及是否破坏 DDD 分层职责。",
                ]
            )
        elif expert_id == "maintainability_code_health":
            base.extend(
                [
                    "- 重点检查新增变量、方法、常量命名是否表达真实业务语义，是否出现 tmp/data/value/obj 等弱语义命名。",
                    "- 重点检查 if/switch/查询/构造函数参数中的数字、字符串、状态码是否属于应被提取的魔法值。",
                    "- 如果命名或魔法值会直接提高理解成本、修改风险或误用概率，可以输出 direct_defect 或 design_concern，不要一律降级成纯提示。",
                ]
            )
        else:
            base.append("- 若上下文中存在 Java 结构化上下文，请优先引用这些结构化上下文，而不是只看单行改动。")
        if java_review_mode == "ddd_enhanced":
            base.extend(
                [
                    "- Java DDD 增强要求: 当前变更命中了领域建模信号，请额外检查聚合边界、领域事件和值对象语义。",
                    "- 若结论涉及领域层责任划分，必须明确说明改动落在领域层、应用层还是基础设施层。",
                ]
            )
        return "\n".join(base) + "\n"

    def _build_expert_system_prompt(
        self,
        expert: ExpertProfile,
        bound_documents: list[object],
        active_skills: list[object] | None = None,
        rule_screening: dict[str, object] | None = None,
        *,
        analysis_mode: Literal["standard", "light"] = "standard",
        model_name: str | None = None,
        prompt_profile_name: str | None = "auto",
    ) -> str:
        prompt_profile = resolve_model_prompt_profile(model_name or expert.model, profile_name=prompt_profile_name)
        if prompt_profile.avoid_long_system_prompt:
            review_spec_text = self._compact_prompt_block(
                self._build_review_spec_summary(str(expert.review_spec or "").strip()),
                1200,
            )
            bound_documents_text = self._compact_prompt_block(
                (
                    self._build_bound_documents_summary(bound_documents)
                    if analysis_mode == "light"
                    else self._build_bound_documents_fulltext(bound_documents)
                ),
                1800 if analysis_mode == "light" else 2600,
            )
            active_skill_text = self._compact_prompt_block(
                self._build_active_skill_summary(active_skills or []),
                1000,
            )
            rule_screening_text = self._compact_prompt_block(
                self._build_rule_screening_summary(rule_screening or {}),
                1400,
            )
            return (
                f"你是{expert.name_zh}，职责是{expert.role}。\n"
                "严格遵守用户提示中的 [SYSTEM RULES]、[RULE_CARDS]、[CONTEXT_PACKET] 和 [OUTPUT_JSON]。\n"
                "必须输出 rule_check_results、candidate_findings、context_requests、self_check。\n"
                "禁止输出 legacy findings 根结构；只输出 JSON，不输出 Markdown 或额外解释。\n"
                "规则分层：专家绑定规范、RULE_CARDS、专家画像和专家审视规范都参与检视；"
                "附加产品/仓库规则负责产品或仓库特有约束，专家画像负责该专家通用职责，两部分候选取并集并由收敛层去重。\n\n"
                "《审视规范文档》开始\n"
                f"{review_spec_text or '未提供额外规范文档，请至少遵守专家职责与证据优先原则。'}\n"
                "《审视规范文档》结束\n\n"
                "《已激活 Skills 摘要》开始\n"
                f"{active_skill_text}\n"
                "《已激活 Skills 摘要》结束\n\n"
                "《专家绑定参考文档》开始\n"
                "《专家绑定参考文档摘要》开始\n"
                f"{bound_documents_text}\n"
                "《专家绑定参考文档摘要》结束\n"
                "《专家绑定参考文档》结束\n\n"
                "《规则遍历结果摘要》开始\n"
                f"{rule_screening_text}\n"
                "《规则遍历结果摘要》结束\n\n"
                "结构化审查步骤：\n"
                "1. 先判断本轮改动是否落在你的职责范围内；不在范围内时返回空 candidate_findings。\n"
                "2. 对每个候选问题先找代码位置：file_path、line、当前代码片段、相关调用链或配置证据。\n"
                "3. 目标 hunk 中 `| +` 是修改后的当前代码，`| -` 是旧代码，只能作为对比证据。\n"
                "4. 如果旧代码里的问题已经被新代码修复，必须返回空 candidate_findings；只针对已删除代码、历史旧代码或未变更代码下结论的 finding 必须丢弃。\n"
                "5. 缺少关联上下文但已有当前代码证据时，保留 candidate_findings 并写 context_requests。\n\n"
                "置信度口径：0.90-1.00 表示直接代码证据充分；0.75-0.89 表示需要少量上下文补充；"
                "0.50-0.74 是风险假设；低于 0.50 不要输出。normalized_issue_type 必须稳定。"
            )
        base_prompt = expert.system_prompt or f"你是{expert.name_zh}，你的职责是{expert.role}。"
        if analysis_mode == "light":
            review_spec_text = self._build_review_spec_summary(str(expert.review_spec or "").strip())
            bound_documents_text = "《专家绑定参考文档摘要》开始\n" + self._build_bound_documents_summary(bound_documents) + "\n《专家绑定参考文档摘要》结束"
            active_skill_text = "《已激活 Skills 摘要》开始\n" + self._build_active_skill_summary(active_skills or []) + "\n《已激活 Skills 摘要》结束"
            rule_screening_text = "《规则遍历结果摘要》开始\n" + self._build_rule_screening_summary(rule_screening or {}) + "\n《规则遍历结果摘要》结束"
        else:
            review_spec_text = expert.review_spec or "未提供额外规范文档，请至少遵守专家职责与证据优先原则。"
            bound_documents_text = self._build_bound_documents_fulltext(bound_documents)
            active_skill_text = self._build_active_skill_fulltext(active_skills or [])
            rule_screening_text = self._build_rule_screening_fulltext(rule_screening or {})
        return (
            f"{base_prompt}\n\n"
            f"《审视规范文档》开始\n"
            f"{review_spec_text}\n"
            f"《审视规范文档》结束\n\n"
            f"{active_skill_text}\n\n"
            f"{bound_documents_text}\n\n"
            f"{rule_screening_text}\n\n"
            f"执行纪律：\n"
            f"1. 只在你的职责边界内下结论。\n"
            f"2. 结论必须绑定具体文件和代码行，禁止泛化空谈。\n"
            f"3. 没有代码证据时，只能提出“需要验证”，不能伪造确定性结论。\n"
            f"4. 修复建议必须可执行，不能只写“建议优化”。\n"
            f"5. 必须讲清楚怎么改，并给出建议修改后的完整代码片段。\n"
            f"6. 必须显式引用命中的规范条款和违反的规范要求。\n"
            f"7. 输出必须遵守 JSON contract。\n"
            f"8. 每条 finding 必须填写 matched_rules 和 normalized_issue_type；不确定时也要给出稳定英文短语，便于后续去重和阈值过滤。\n"
            f"9. 除 normalized_issue_type、代码标识、文件路径、接口名、类名和方法名外，面向用户的说明字段必须使用中文。\n\n"
            f"规则分层：专家绑定规范、RULE_CARDS、专家画像和专家审视规范都参与检视；"
            f"绑定规范负责产品/仓库特有约束，专家画像负责该专家通用职责，两部分候选取并集并由收敛层去重；"
            f"引用附加规则时只能引用本轮规则遍历结果里的真实 rule_id。\n\n"
            f"结构化审查步骤：\n"
            f"1. 先判断本轮改动是否落在你的职责范围内；不在范围内时返回空 findings，不要顺手评论其他专家负责的问题。\n"
            f"2. 对每个候选问题先找代码位置：file_path、line_start/line_end、当前代码片段、相关调用链或配置证据。\n"
            f"3. 再判断问题是否由本次 diff 引入或暴露；目标 hunk 中 `| +` 是修改后的当前代码，`| -` 是旧代码，只能作为对比证据，不能作为问题主张本身。\n"
            f"4. 如果旧代码里的问题已经被 `| +` 新代码修复，必须返回空 findings；只针对已删除代码、历史旧代码或未变更代码下结论的 finding 必须丢弃。\n"
            f"5. 如果结论依赖“调用方可能传空、配置可能缺失、线上流量可能很大”等外部条件，必须降级为 needs_verification，不能写成确定 issue。\n"
            f"6. 最后输出修复建议：说明为什么错、怎么改、改完后的关键代码形态。\n\n"
            f"置信度口径：\n"
            f"- 0.90-1.00: diff 中有直接代码证据，且不依赖外部条件。\n"
            f"- 0.75-0.89: 有明确代码位置和代码片段，但需要少量上下文补充。\n"
            f"- 0.50-0.74: 只是合理风险假设，应标记 needs_verification。\n"
            f"- 低于 0.50: 不要输出为 issue。\n\n"
            f"反例约束：\n"
            f"- 不要输出“可能存在、建议确认、需结合实际场景判断”但没有代码证据的问题。\n"
            f"- 不要把通用命名、格式、日志文案问题跨专家重复提出；这些只属于通用编码规范/可维护性职责。\n"
            f"- 不要为了凑数量输出低价值意见；没有高价值发现时返回空 findings。"
        )

    def _compact_prompt_block(self, text: str, limit: int) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        safe_limit = max(400, int(limit or 0))
        if len(raw) <= safe_limit:
            return raw
        return raw[:safe_limit].rstrip() + "\n...<truncated for light mode>"

    def _build_expert_prompt_budget_metadata(
        self,
        *,
        subject: ReviewSubject,
        expert: ExpertProfile,
        file_path: str,
        line_start: int,
        runtime_tool_results: list[dict[str, object]],
        repository_context: dict[str, object],
        target_hunk: dict[str, object],
        target_hunks: list[dict[str, object]] | None,
        bound_documents: list[object],
        active_skills: list[object],
        rule_screening: dict[str, object] | None,
        analysis_mode: Literal["standard", "light"],
        include_target_file_full_diff: bool,
        include_related_diff_summary: bool,
    ) -> dict[str, object]:
        if analysis_mode != "light":
            return {}
        prompt_repository_context = self._prepare_prompt_repository_context(
            expert=expert,
            repository_context=dict(repository_context or {}),
            rule_screening=rule_screening or {},
            analysis_mode=analysis_mode,
        )
        repository_context_budget = dict(prompt_repository_context.get("prompt_context_budget") or {})
        light_sections, request_budget = self._apply_light_prompt_request_budget(
            expert_id=expert.expert_id,
            include_target_file_full_diff=include_target_file_full_diff,
            sections={
                "review_spec_summary": self._build_review_spec_summary(expert.review_spec),
                "active_skill_summary": self._build_active_skill_summary(active_skills),
                "bound_documents_summary": self._build_bound_documents_summary(bound_documents),
                "rule_screening_summary": self._build_rule_screening_summary(rule_screening or {}),
                "input_completeness_summary": self._build_review_input_completeness_summary(
                    subject,
                    file_path,
                    line_start,
                    prompt_repository_context,
                    expert=expert,
                    bound_documents=bound_documents,
                    rule_screening=rule_screening or {},
                    language=self._infer_code_language(file_path),
                ),
                "language_general_guidance": self._build_language_general_guidance(
                    self._infer_code_language(file_path)
                ),
                "design_doc_summary": self._build_design_doc_summary(subject),
                "hunk_summary": self._build_hunk_summary(target_hunk),
                "hunk_batch_summary": self._build_hunk_batch_summary(target_hunks or []),
                "target_file_full_diff": (
                    self._build_target_file_full_diff(subject, file_path)
                    if include_target_file_full_diff
                    else ""
                ),
                "related_diff_summary": (
                    self._build_related_diff_summary(subject, file_path)
                    if include_related_diff_summary
                    else ""
                ),
                "runtime_tool_summary": self._build_runtime_tool_summary(runtime_tool_results),
                "repository_context_summary": self._build_repository_context_summary(
                    prompt_repository_context,
                    runtime_tool_results,
                ),
                "repository_source_blocks": self._build_repository_source_blocks(
                    prompt_repository_context,
                    runtime_tool_results,
                ),
                "code_excerpt": self._build_code_excerpt(subject, file_path, line_start, expert.expert_id),
                "observation_review_summary": self._build_observation_review_summary(prompt_repository_context),
            },
        )
        return {
            "repository_context_budget": repository_context_budget,
            "prompt_request_budget": request_budget,
            "retained_light_sections": [
                key
                for key, value in light_sections.items()
                if str(value or "").strip()
            ],
        }

    def _apply_light_prompt_request_budget(
        self,
        *,
        expert_id: str,
        include_target_file_full_diff: bool,
        sections: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, object]]:
        ordered_keys = [
            "review_spec_summary",
            "active_skill_summary",
            "bound_documents_summary",
            "rule_screening_summary",
            "input_completeness_summary",
            "language_general_guidance",
            "design_doc_summary",
            "hunk_summary",
            "hunk_batch_summary",
            "target_file_full_diff",
            "related_diff_summary",
            "runtime_tool_summary",
            "repository_context_summary",
            "repo_review_instruction_summary",
            "repository_source_blocks",
            "code_excerpt",
            "observation_review_summary",
        ]
        blocks: list[ContextBlock] = []
        for key in ordered_keys:
            content = str(sections.get(key) or "").strip()
            if not content:
                continue
            block_type, must_keep = self._light_prompt_section_policy(
                key,
                include_target_file_full_diff=include_target_file_full_diff,
            )
            blocks.append(
                ContextBlock(
                    block_id=key,
                    type=block_type,
                    priority=priority_for_block_type(block_type),
                    expert_relevance=self._light_prompt_section_relevance(
                        key,
                        expert_id=expert_id,
                    ),
                    evidence_strength=self._light_prompt_section_evidence_strength(key),
                    must_keep=must_keep,
                    token_cost=self._estimate_context_block_token_cost(content),
                    source="expert_prompt",
                    summary=key,
                    content=content,
                )
            )
        total_budget = max(
            400,
            int(os.getenv("REVIEW_LIGHT_EXPERT_REQUEST_BUDGET_TOKENS", "9000") or 9000),
        )
        plan = self.prompt_budget_planner.plan(
            blocks,
            expert_id=expert_id,
            total_budget=total_budget,
        )
        kept_map = {block.block_id: block for block in plan.kept_blocks}
        rendered = dict(sections)
        for key in ordered_keys:
            block = kept_map.get(key)
            rendered[key] = block.content if block is not None else ""
        metadata = {
            "total_budget": plan.total_budget,
            "used_budget": plan.used_budget,
            "must_keep_blocks": [block.block_id for block in plan.must_keep_blocks],
            "kept_blocks": [block.block_id for block in plan.kept_blocks],
            "compressed_blocks": [block.block_id for block in plan.compressed_blocks],
            "dropped_blocks": [block.block_id for block in plan.dropped_blocks],
        }
        return rendered, metadata

    def _light_prompt_section_policy(
        self,
        section_key: str,
        *,
        include_target_file_full_diff: bool,
    ) -> tuple[str, bool]:
        mapping: dict[str, tuple[str, bool]] = {
            "review_spec_summary": ("expert_review_spec", True),
            "active_skill_summary": ("matched_bound_doc_section", False),
            "bound_documents_summary": ("matched_bound_doc_section", False),
            "rule_screening_summary": ("matched_rules", True),
            "input_completeness_summary": ("output_contract", True),
            "language_general_guidance": ("language_guidance", True),
            "design_doc_summary": ("design_doc_summary", False),
            "hunk_summary": ("target_hunk", True),
            "hunk_batch_summary": ("same_file_other_hunks", False),
            "target_file_full_diff": ("same_file_other_hunks", bool(include_target_file_full_diff)),
            "related_diff_summary": ("related_diff_summary", False),
            "runtime_tool_summary": ("runtime_tool_evidence", False),
            "repository_context_summary": ("repository_context_summary", False),
            "repo_review_instruction_summary": ("matched_rules", True),
            "repository_source_blocks": ("related_source_snippet", False),
            "code_excerpt": ("current_code", True),
            "observation_review_summary": ("critical_observations", False),
        }
        return mapping.get(section_key, ("secondary_context_note", False))

    def _light_prompt_section_relevance(
        self,
        section_key: str,
        *,
        expert_id: str,
    ) -> float:
        if section_key in {"hunk_summary", "code_excerpt"}:
            return 1.0
        if section_key in {
            "review_spec_summary",
            "rule_screening_summary",
            "input_completeness_summary",
            "language_general_guidance",
        }:
            return 0.98
        if expert_id == "performance_reliability" and section_key in {
            "runtime_tool_summary",
            "repository_source_blocks",
            "target_file_full_diff",
            "observation_review_summary",
        }:
            return 0.95
        if expert_id == "security_compliance" and section_key in {
            "runtime_tool_summary",
            "repository_source_blocks",
            "observation_review_summary",
        }:
            return 0.95
        if section_key in {"repository_source_blocks", "target_file_full_diff", "runtime_tool_summary"}:
            return 0.88
        if section_key in {"hunk_batch_summary", "design_doc_summary", "observation_review_summary"}:
            return 0.75
        return 0.55

    def _light_prompt_section_evidence_strength(self, section_key: str) -> float:
        if section_key in {
            "review_spec_summary",
            "rule_screening_summary",
            "input_completeness_summary",
            "language_general_guidance",
            "hunk_summary",
            "code_excerpt",
            "target_file_full_diff",
        }:
            return 0.98
        if section_key in {"runtime_tool_summary", "repository_source_blocks", "observation_review_summary"}:
            return 0.85
        if section_key in {"hunk_batch_summary", "design_doc_summary"}:
            return 0.7
        return 0.45

    def _build_active_skill_summary(self, active_skills: list[object]) -> str:
        if not active_skills:
            return "本轮未激活额外 skill。"
        lines: list[str] = []
        for skill in active_skills:
            skill_id = str(getattr(skill, "skill_id", "") or "").strip()
            description = str(getattr(skill, "description", "") or "").strip()
            required_tools = [str(item).strip() for item in list(getattr(skill, "required_tools", []) or []) if str(item).strip()]
            lines.append(f"- {skill_id}: {description or '无描述'}")
            if required_tools:
                lines.append(f"  * tools: {' / '.join(required_tools[:6])}")
        return "\n".join(lines)

    def _build_active_skill_fulltext(self, active_skills: list[object]) -> str:
        if not active_skills:
            return "《已激活 Skills》开始\n本轮未激活额外 skill。\n《已激活 Skills》结束"
        sections = ["《已激活 Skills》开始"]
        for index, skill in enumerate(active_skills, start=1):
            skill_id = str(getattr(skill, "skill_id", "") or "").strip() or f"skill-{index}"
            name = str(getattr(skill, "name", "") or "").strip() or skill_id
            sections.append(f"## Skill {index}: {name} ({skill_id})")
            sections.append(str(getattr(skill, "prompt_body", "") or "").strip() or "无额外 skill 正文。")
        sections.append("《已激活 Skills》结束")
        return "\n".join(sections)

    def _collect_skill_tools(self, active_skills: list[object]) -> list[str]:
        tool_names: list[str] = []
        for skill in active_skills:
            for item in list(getattr(skill, "required_tools", []) or []):
                tool_name = str(item).strip()
                if tool_name and tool_name not in tool_names:
                    tool_names.append(tool_name)
        return tool_names

    def _review_design_docs(self, subject: ReviewSubject) -> list[dict[str, object]]:
        value = subject.metadata.get("design_docs", [])
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _build_design_doc_summary(self, subject: ReviewSubject) -> str:
        """把本次审核绑定的详细设计文档压缩成适合给专家阅读的摘要。"""
        design_docs = self._review_design_docs(subject)
        if not design_docs:
            return "本次审核未绑定详细设计文档。"
        lines: list[str] = []
        for index, item in enumerate(design_docs[:4], start=1):
            title = str(item.get("title") or item.get("filename") or f"设计文档 {index}").strip()
            filename = str(item.get("filename") or "").strip()
            content = str(item.get("content") or "").strip()
            line = f"- {title}"
            if filename:
                line += f" · {filename}"
            lines.append(line)
            if content:
                excerpt_lines = [text.strip() for text in content.splitlines() if text.strip()]
                if excerpt_lines:
                    lines.append(f"  * 摘要: {' '.join(excerpt_lines[:3])[:220]}")
        return "\n".join(lines)
