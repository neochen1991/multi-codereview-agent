from __future__ import annotations

import json

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.cross_file_impact import build_cross_file_impact_hints
from app.services.main_agent_routing_support import filter_primary_signals_for_expert


class MainAgentPromptingMixin:
    """Prompt construction, LLM JSON parsing, and route-plan normalization for MainAgentService."""

    def _build_routing_plan_payload(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        routes: dict[str, dict[str, object]],
        candidate_hunks: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "expert_routes": [
                {
                    "expert_id": expert.expert_id,
                    "file_path": str(routes.get(expert.expert_id, {}).get("file_path") or ""),
                    "line_start": int(routes.get(expert.expert_id, {}).get("line_start") or 0),
                    "candidate_id": self._match_candidate_id(candidate_hunks, routes.get(expert.expert_id, {})),
                    "routeable": bool(routes.get(expert.expert_id, {}).get("routeable", True)),
                    "reason": str(routes.get(expert.expert_id, {}).get("routing_reason") or ""),
                    "confidence": float(routes.get(expert.expert_id, {}).get("confidence") or 0.0),
                }
                for expert in experts
            ],
            "skipped_experts": [
                {
                    "expert_id": expert.expert_id,
                    "reason": str(routes.get(expert.expert_id, {}).get("skip_reason") or ""),
                }
                for expert in experts
                if not bool(routes.get(expert.expert_id, {}).get("routeable", True))
            ],
        }

    def _build_routing_system_prompt(self) -> str:
        return (
            "你是多专家代码审查系统的主Agent，职责是根据完整代码变更和专家职责进行派工。"
            "请只输出 JSON，不要输出任何解释性文字。"
            "派工原则：1. 优先依据代码语义和变更内容，而不是路径；"
            "2. 非 test_verification 专家默认避开 test/spec 文件；"
            "3. 每个专家只选择一个主焦点 hunk，但后续会收到完整业务变更信息；"
            "4. 若当前变更与专家职责不符，可以 routeable=false 并给出 skip reason；"
            "5. 输出必须使用提供的 candidate_id 或 file_path+line_start 对应真实候选 hunk；"
            "6. 同一类问题尽量只派给一个主责专家，不要把高度重叠的问题同时派给多个相近专家。"
            "主责划分参考："
            "业务规则、状态流转、注释或接口承诺未实现 -> correctness_business；"
            "聚合边界、应用服务职责、依赖方向、分层边界 -> ddd_architecture；"
            "命名、日志、判空、异常写法、魔法值 -> architecture_design；"
            "复杂度、重复代码、长期演化成本 -> maintainability_code_health；"
            "SQL、事务、schema、索引 -> database_analysis；"
            "批处理、锁竞争、超时重试、故障放大 -> performance_reliability；"
            "影响范围、调用链、测试范围 -> change_impact_analysis。"
        )

    def _build_expert_selection_system_prompt(self) -> str:
        return (
            "你是多专家代码审查系统的主Agent。"
            "在正式派工前，你需要先根据 MR 信息、完整 diff 和专家画像，决定本次真正需要参与审核的专家集合。"
            "请只输出 JSON，不要输出解释。"
            "选择原则：1. 必须依据真实变更内容和专家职责边界选择；"
            "2. 专家数量应尽量精简，只保留真正相关的专家；"
            "3. 非前端改动不要选择前端专家；非安全线索不要强行选择安全专家；"
            "4. 变更涉及跨文件契约、业务逻辑、结构设计时，应优先保留正确性/架构/可维护性等通用专家；"
            "5. 如果某专家不需要参与，写入 skipped_experts 并说明原因；"
            "6. selected_experts 至少返回 1 个；"
            "7. 对高度重叠的问题类别，只保留一个主责专家，避免把同类问题同时分给多个相近专家。"
            "主责划分参考："
            "业务规则、状态流转、注释或接口承诺未实现 -> correctness_business；"
            "聚合边界、应用服务职责、依赖方向、分层边界 -> ddd_architecture；"
            "命名、日志、判空、异常写法、魔法值 -> architecture_design；"
            "复杂度、重复代码、长期演化成本 -> maintainability_code_health；"
            "SQL、事务、schema、索引 -> database_analysis；"
            "批处理、锁竞争、超时重试、故障放大 -> performance_reliability；"
            "影响范围、调用链、测试范围 -> change_impact_analysis。"
        )

    def _infer_code_language(self, file_path: str) -> str:
        lowered = str(file_path or "").lower()
        if lowered.endswith(".tsx"):
            return "tsx"
        if lowered.endswith(".ts"):
            return "typescript"
        if lowered.endswith(".jsx"):
            return "jsx"
        if lowered.endswith(".js"):
            return "javascript"
        if lowered.endswith(".java"):
            return "java"
        return "text"

    def _build_language_general_guidance(self, language: str) -> str:
        normalized = str(language or "").strip().lower()
        if normalized == "java":
            return (
                "- 参考 Java / Spring 通用代码规范：命名清晰，职责单一，校验、事务、持久化、远程调用不要无边界混合，避免临时变量名、弱语义命名和魔法值直接写进业务逻辑。\n"
                "- 关注输入校验、空值与异常处理、日志脱敏、权限/租户隔离，以及 @Transactional 内的副作用。\n"
                "- 检查 Repository / JPA / MyBatis 查询是否存在无分页、全表扫描、N+1、批量逐条写等常见质量风险。\n"
                "- 检查条件分支、阈值、状态码、字符串标识是否以魔法值形式散落在代码中，是否应提取为常量、枚举或具名配置。"
            )
        if normalized in {"javascript", "jsx", "typescript", "tsx"}:
            return (
                "- 参考 JavaScript / TypeScript 通用代码规范：命名清晰，副作用显式，异步错误必须处理，避免隐式 any 和不透明的数据流。\n"
                "- 关注输入校验、鉴权边界、敏感信息暴露、Promise/await 错误传播、竞态和资源泄露。\n"
                "- 检查数据库/HTTP/缓存调用是否存在未分页查询、无边界重试、串行批处理或阻塞主路径的问题。"
            )
        return ""

    def _build_language_general_guidance_summary(self, file_paths: list[str]) -> str:
        sections: list[str] = []
        seen_languages: set[str] = set()
        for path in file_paths:
            language = self._infer_code_language(path)
            if language in seen_languages:
                continue
            seen_languages.add(language)
            guidance = self._build_language_general_guidance(language)
            if not guidance:
                continue
            sections.append(f"# {language}\n{guidance}")
        return "\n\n".join(sections) if sections else "当前变更文件未命中已配置的语言通用规范提示。"

    def _build_routing_user_prompt(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        candidate_hunks: list[dict[str, object]],
        runtime_settings: RuntimeSettings,
    ) -> str:
        expert_sections = []
        for expert in experts:
            expert_sections.append(
                "\n".join(
                    [
                        f"- expert_id: {expert.expert_id}",
                        f"  名称: {expert.name_zh}",
                        f"  职责重点: {' / '.join(expert.focus_areas) or expert.role}",
                        f"  触发线索: {' / '.join(expert.activation_hints) or '按代码语义判断'}",
                        f"  必查项: {' / '.join(expert.required_checks) or '无'}",
                        f"  禁止越界: {' / '.join(expert.out_of_scope) or '无'}",
                    ]
                )
            )
        candidate_sections = []
        for item in candidate_hunks:
            cross_file_impact_hints = [
                str(value).strip()
                for value in list(item.get("cross_file_impact_hints") or [])
                if str(value).strip()
            ]
            candidate_sections.append(
                "\n".join(
                    [
                        f"- candidate_id: {item['candidate_id']}",
                        f"  file_path: {item['file_path']}",
                        f"  line_start: {item['line_start']}",
                        f"  hunk_header: {item['hunk_header']}",
                        f"  excerpt: {str(item['excerpt'])[:700]}",
                        f"  repo_context: {self._format_repo_matches(dict(item.get('repo_hits') or {}))[:500]}",
                        f"  cross_file_impact: {' / '.join(cross_file_impact_hints[:3]) or '未识别到明确跨文件传播线索'}",
                    ]
                )
            )
        business_changed_files = self._candidate_changed_files(subject, "")
        primary_file_path = str(candidate_hunks[0]["file_path"]) if candidate_hunks else str(business_changed_files[0]) if business_changed_files else ""
        target_file_full_diff = self._build_file_diff_context(subject, primary_file_path, max_lines=100)
        related_diff_summary = self._build_related_diff_summary(subject, primary_file_path, max_files=3, max_lines_per_file=16)
        source_context_summary = self._build_main_agent_source_context_summary(
            subject,
            runtime_settings,
            primary_file_path=primary_file_path,
            related_file_paths=business_changed_files,
            primary_radius=10,
            primary_max_lines=16,
            related_radius=6,
            related_max_files=2,
            related_max_lines=8,
        )
        language_general_guidance = self._build_language_general_guidance_summary(
            [str(item.get("file_path") or "") for item in candidate_hunks] + business_changed_files
        )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"目标文件完整 diff:\n{target_file_full_diff}\n\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n\n"
            f"变更源码与关联上下文:\n{source_context_summary}\n\n"
            f"语言通用规范提示:\n{language_general_guidance}\n\n"
            "主责专家速查：\n"
            "- correctness_business: 业务规则、状态流转、注释或接口承诺未实现\n"
            "- ddd_architecture: 聚合边界、应用服务职责、依赖方向、分层边界\n"
            "- architecture_design: 命名、日志、判空、异常写法、魔法值\n"
            "- maintainability_code_health: 复杂度、重复代码、长期演化成本\n"
            "- database_analysis: SQL、事务、schema、索引\n"
            "- performance_reliability: 批处理、锁竞争、超时重试、故障放大\n"
            "- change_impact_analysis: 影响范围、调用链、测试范围\n\n"
            f"可用专家:\n{chr(10).join(expert_sections)}\n\n"
            f"候选 hunk:\n{chr(10).join(candidate_sections)}\n\n"
            "请输出 JSON，格式为：\n"
            "{\n"
            '  "expert_routes": [\n'
            "    {\n"
            '      "expert_id": "correctness_business",\n'
            '      "candidate_id": "path:line:index",\n'
            '      "routeable": true,\n'
            '      "reason": "为什么这个专家应该看这个 hunk",\n'
            '      "confidence": 0.91\n'
            "    }\n"
            "  ],\n"
            '  "skipped_experts": [\n'
            '    {"expert_id": "ddd_architecture", "reason": "未命中DDD边界变化"}\n'
            "  ]\n"
            "}"
        )

    def _build_expert_selection_user_prompt(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        requested_expert_ids: list[str],
        runtime_settings: RuntimeSettings,
    ) -> str:
        business_changed_files = self._candidate_changed_files(subject, "")
        expert_sections = []
        for expert in experts:
            expert_sections.append(
                "\n".join(
                    [
                        f"- expert_id: {expert.expert_id}",
                        f"  名称: {expert.name_zh}",
                        f"  角色: {expert.role}",
                        f"  职责重点: {' / '.join(expert.focus_areas) or expert.role}",
                        f"  触发线索: {' / '.join(expert.activation_hints) or '按变更语义判断'}",
                        f"  必查项: {' / '.join(expert.required_checks) or '无'}",
                        f"  越界边界: {' / '.join(expert.out_of_scope) or '无'}",
                    ]
                )
        )
        primary_file_path = str(business_changed_files[0]) if business_changed_files else str(subject.changed_files[0]) if subject.changed_files else ""
        target_file_full_diff = self._build_file_diff_context(subject, primary_file_path, max_lines=80)
        related_diff_summary = self._build_related_diff_summary(subject, primary_file_path, max_files=2, max_lines_per_file=12)
        java_quality = self._collect_java_quality_signals(subject)
        java_quality_summary = self._build_java_quality_signal_summary(java_quality)
        language_general_guidance = self._build_language_general_guidance_summary(
            business_changed_files or [str(item) for item in list(subject.changed_files or [])]
        )
        selection_cross_file_hints = build_cross_file_impact_hints(
            file_path=primary_file_path,
            related_files=business_changed_files[1:],
            changed_files=business_changed_files,
        )
        return (
            f"审核对象: {subject.title or subject.mr_url or subject.source_ref}\n"
            f"MR 链接: {subject.mr_url}\n"
            f"源分支: {subject.source_ref}\n"
            f"目标分支: {subject.target_ref}\n"
            f"全部变更文件: {json.dumps(list(subject.changed_files), ensure_ascii=False)}\n"
            f"业务变更文件: {json.dumps(business_changed_files, ensure_ascii=False)}\n"
            f"用户原始选择: {json.dumps(requested_expert_ids, ensure_ascii=False)}\n"
            f"业务变更文件完整 diff:\n{target_file_full_diff}\n\n"
            f"其他变更文件摘要:\n{related_diff_summary}\n\n"
            f"通用质量信号摘要:\n{java_quality_summary}\n\n"
            f"跨文件影响提示:\n{chr(10).join(f'- {item}' for item in selection_cross_file_hints) or '- 当前未识别到明确的跨文件传播线索'}\n\n"
            f"语言通用规范提示:\n{language_general_guidance}\n\n"
            "主责专家速查：\n"
            "- correctness_business: 业务规则、状态流转、注释或接口承诺未实现\n"
            "- ddd_architecture: 聚合边界、应用服务职责、依赖方向、分层边界\n"
            "- architecture_design: 命名、日志、判空、异常写法、魔法值\n"
            "- maintainability_code_health: 复杂度、重复代码、长期演化成本\n"
            "- database_analysis: SQL、事务、schema、索引\n"
            "- performance_reliability: 批处理、锁竞争、超时重试、故障放大\n"
            "- change_impact_analysis: 影响范围、调用链、测试范围\n\n"
            f"可用专家画像:\n{chr(10).join(expert_sections)}\n\n"
            "请输出 JSON，格式为：\n"
            "{\n"
            '  "selected_experts": [\n'
            '    {"expert_id": "correctness_business", "reason": "跨文件字段契约和业务语义变化明显", "confidence": 0.93}\n'
            "  ],\n"
            '  "skipped_experts": [\n'
            '    {"expert_id": "security_compliance", "reason": "当前 diff 未出现认证、权限、密钥或输入校验相关信号"}\n'
            "  ]\n"
            "}"
        )

    def _build_java_quality_signal_summary(self, java_quality: dict[str, list[str]]) -> str:
        signals = [str(item).strip() for item in list(java_quality.get("signals") or []) if str(item).strip()]
        matched_terms = [str(item).strip() for item in list(java_quality.get("matched_terms") or []) if str(item).strip()]
        if not signals and not matched_terms:
            return "当前变更未提取到额外的语言通用质量信号。"
        lines: list[str] = []
        if signals:
            lines.append(f"- signals: {', '.join(signals)}")
        if matched_terms:
            lines.append(f"- matched_terms: {', '.join(matched_terms[:12])}")
        return "\n".join(lines)

    def _build_file_diff_context(self, subject: ReviewSubject, file_path: str, *, max_lines: int = 160) -> str:
        if not file_path:
            return "未定位到主要变更文件。"
        full_diff = self._diff_excerpt_service.extract_file_diff(subject.unified_diff, file_path)
        if not full_diff:
            return f"未从 unified diff 中提取到 {file_path} 的文件级变更。"
        lines = full_diff.splitlines()
        if len(lines) <= max_lines:
            return full_diff
        return "\n".join(lines[:max_lines]) + f"\n... [目标文件完整 diff 过长，已截断展示前 {max_lines} 行]"

    def _build_related_diff_summary(
        self,
        subject: ReviewSubject,
        primary_file_path: str,
        *,
        max_files: int = 4,
        max_lines_per_file: int = 24,
    ) -> str:
        related_paths = [
            str(path).strip()
            for path in list(subject.changed_files or [])
            if str(path).strip() and str(path).strip() != primary_file_path
        ]
        if not related_paths:
            return "除主要变更文件外无其他变更文件。"
        sections: list[str] = []
        for path in related_paths[:max_files]:
            full_diff = self._diff_excerpt_service.extract_file_diff(subject.unified_diff, path)
            if not full_diff:
                sections.append(f"# {path}\n未提取到该文件 diff。")
                continue
            preview_lines = full_diff.splitlines()
            display_lines = preview_lines[:max_lines_per_file]
            suffix = "\n... [摘要已截断]" if len(preview_lines) > max_lines_per_file else ""
            sections.append(f"# {path}\n" + "\n".join(display_lines) + suffix)
        remaining = len(related_paths) - min(len(related_paths), max_files)
        if remaining > 0:
            sections.append(f"... 其余 {remaining} 个变更文件未展开，请结合 changed_files 判断全局影响。")
        return "\n\n".join(sections)

    def _build_main_agent_source_context_summary(
        self,
        subject: ReviewSubject,
        runtime_settings: RuntimeSettings,
        *,
        primary_file_path: str,
        related_file_paths: list[str],
        primary_radius: int = 16,
        primary_max_lines: int = 24,
        related_radius: int = 10,
        related_max_files: int = 3,
        related_max_lines: int = 16,
    ) -> str:
        service = self._build_repository_service(runtime_settings, subject)
        if not service.is_ready():
            return "代码仓上下文未配置或本地仓不可用；当前只提供真实 diff，未补充目标分支源码。"
        sections: list[str] = []
        if primary_file_path:
            primary_line = self._pick_line_start(subject, "", primary_file_path)
            primary_context = service.load_file_context(primary_file_path, max(1, primary_line), radius=primary_radius)
            primary_snippet = str((primary_context or {}).get("snippet") or "").strip()
            if primary_snippet:
                sections.append(f"# 目标文件源码\n{primary_file_path}")
                sections.extend(primary_snippet.splitlines()[:primary_max_lines])
        for path in [
            item
            for item in related_file_paths
            if str(item).strip() and str(item).strip() != primary_file_path
        ][:related_max_files]:
            line_start = self._pick_line_start(subject, "", path)
            context = service.load_file_context(path, max(1, line_start), radius=related_radius)
            snippet = str((context or {}).get("snippet") or "").strip()
            if not snippet:
                continue
            if sections:
                sections.append("")
            sections.append(f"# 关联源码\n{path}")
            sections.extend(snippet.splitlines()[:related_max_lines])
        return "\n".join(sections) if sections else "未从目标分支源码仓加载到可用源码片段。"

    def _parse_json_payload(self, text: str) -> dict[str, object]:
        content = str(text or "").strip()
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif content.startswith("```"):
            content = content.split("```", 1)[1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _parse_routing_plan(self, text: str) -> dict[str, object]:
        return self._parse_json_payload(text)

    def _merge_expert_selection(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        requested_expert_ids: list[str],
        llm_payload: dict[str, object],
        fallback_ids: list[str],
    ) -> dict[str, object]:
        experts_by_id = {expert.expert_id: expert for expert in experts}
        selected_entries: list[dict[str, object]] = []
        selected_ids: list[str] = []
        for item in list(llm_payload.get("selected_experts", []) or []):
            if not isinstance(item, dict):
                continue
            expert_id = str(item.get("expert_id") or "").strip()
            if not expert_id or expert_id not in experts_by_id or expert_id in selected_ids:
                continue
            selected_ids.append(expert_id)
            selected_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": str(item.get("reason") or "").strip(),
                    "confidence": float(item.get("confidence") or 0.0),
                    "source": "llm_selected",
                }
            )
        if not selected_ids:
            selected_ids = list(fallback_ids)
            selected_entries = [
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": "LLM 未返回有效专家集合，已使用兜底集合",
                    "confidence": 0.5,
                    "source": "fallback_selected",
                }
                for expert_id in selected_ids
                if expert_id in experts_by_id
            ]
        skipped_entries: list[dict[str, object]] = []
        explicit_skipped_ids: set[str] = set()
        for item in list(llm_payload.get("skipped_experts", []) or []):
            if not isinstance(item, dict):
                continue
            expert_id = str(item.get("expert_id") or "").strip()
            if not expert_id or expert_id not in experts_by_id or expert_id in explicit_skipped_ids:
                continue
            explicit_skipped_ids.add(expert_id)
            skipped_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": experts_by_id[expert_id].name_zh,
                    "reason": str(item.get("reason") or "").strip() or "大模型判定当前 MR 与该专家职责不匹配",
                }
            )
        for expert in experts:
            if expert.expert_id in selected_ids or expert.expert_id in explicit_skipped_ids:
                continue
            skipped_entries.append(
                {
                    "expert_id": expert.expert_id,
                    "expert_name": expert.name_zh,
                    "reason": "大模型未将该专家纳入本次 MR 的参与集合",
                }
            )
        if "security_compliance" in requested_expert_ids and "security_compliance" not in selected_ids:
            security_expert = experts_by_id.get("security_compliance")
            if security_expert is not None:
                file_path = self._pick_file_path(subject, security_expert)
                target_focus = self._build_rule_route(subject, security_expert, self._build_repository_service(RuntimeSettings(), subject))
                if self._has_security_signal(subject, file_path, dict(target_focus.get("target_hunk") or {})):
                    selected_ids.append("security_compliance")
                    selected_entries.append(
                        {
                            "expert_id": "security_compliance",
                            "expert_name": security_expert.name_zh,
                            "reason": "命中 Java 入口校验/输入验证安全线索，系统补入安全与合规专家复核。",
                            "confidence": 0.78,
                            "source": "heuristic_selected",
                        }
                    )
                    skipped_entries = [item for item in skipped_entries if str(item.get("expert_id") or "") != "security_compliance"]
        java_quality = self._collect_java_quality_signals(subject)
        selected_ids, selected_entries, skipped_entries = self._apply_java_signal_expert_retention(
            requested_expert_ids=requested_expert_ids,
            experts_by_id=experts_by_id,
            selected_ids=selected_ids,
            selected_entries=selected_entries,
            skipped_entries=skipped_entries,
            java_quality_signals=java_quality["signals"],
        )
        return {
            "requested_expert_ids": requested_expert_ids,
            "candidate_expert_ids": [expert.expert_id for expert in experts],
            "selected_expert_ids": selected_ids,
            "selected_experts": selected_entries,
            "skipped_experts": skipped_entries,
        }

    def _collect_java_quality_signals(self, subject: ReviewSubject) -> dict[str, list[str]]:
        signals: list[str] = []
        matched_terms: list[str] = []
        for file_path in list(subject.changed_files or []):
            file_diff = self._diff_excerpt_service.extract_file_diff(str(subject.unified_diff or ""), str(file_path))
            if not file_diff.strip():
                continue
            extracted = self._java_quality_signal_extractor.extract(
                file_path=str(file_path),
                target_hunk={"excerpt": file_diff},
                full_diff=file_diff,
            )
            for signal in list(extracted.get("signals") or []):
                normalized = str(signal).strip()
                if normalized and normalized not in signals:
                    signals.append(normalized)
            for term in list(extracted.get("matched_terms") or []):
                normalized = str(term).strip()
                if normalized and normalized not in matched_terms:
                    matched_terms.append(normalized)
        return {"signals": signals, "matched_terms": matched_terms}

    def _apply_java_signal_expert_retention(
        self,
        *,
        requested_expert_ids: list[str],
        experts_by_id: dict[str, ExpertProfile],
        selected_ids: list[str],
        selected_entries: list[dict[str, object]],
        skipped_entries: list[dict[str, object]],
        java_quality_signals: list[str],
    ) -> tuple[list[str], list[dict[str, object]], list[dict[str, object]]]:
        signal_set = {str(item).strip() for item in java_quality_signals if str(item).strip()}
        if not signal_set:
            return selected_ids, selected_entries, skipped_entries
        available_expert_ids = set(requested_expert_ids) & set(experts_by_id)

        def _primary_signals(expert_id: str, signals: set[str]) -> set[str]:
            return filter_primary_signals_for_expert(
                signals=signals & signal_set,
                expert_id=expert_id,
                available_expert_ids=available_expert_ids,
            )

        def _add_if_requested(expert_id: str, reason: str, confidence: float) -> None:
            if expert_id in selected_ids:
                return
            expert = experts_by_id.get(expert_id)
            if expert is None:
                return
            selected_ids.append(expert_id)
            selected_entries.append(
                {
                    "expert_id": expert_id,
                    "expert_name": expert.name_zh,
                    "reason": reason,
                    "confidence": confidence,
                    "source": "heuristic_selected",
                }
            )

        if _primary_signals("ddd_architecture", {"factory_bypass", "event_ordering_risk"}):
            _add_if_requested(
                "ddd_architecture",
                "检测到聚合工厂绕过或事件发布顺序风险，系统补入DDD架构专家复核聚合边界、应用编排和事件顺序。",
                0.81,
            )

        if _primary_signals("performance_reliability", {"loop_call_amplification"}):
            _add_if_requested(
                "performance_reliability",
                "检测到循环内调用放大，系统补入性能与可靠性专家复核数据库往返、远程调用和超时风险。",
                0.84,
            )
        if _primary_signals("database_analysis", {"unbounded_query_risk"}):
            _add_if_requested(
                "database_analysis",
                "检测到查询边界缺失，系统补入数据库专家复核查询路径、索引命中和批量访问模式。",
                0.8,
            )

        if _primary_signals("correctness_business", {"comment_contract_unimplemented"}):
            _add_if_requested(
                "correctness_business",
                "检测到注释或 TODO 承诺未落地，系统补入正确性与业务专家复核承诺与实现是否一致。",
                0.79,
            )

        if "security_guard_removed" in signal_set:
            _add_if_requested(
                "security_compliance",
                "检测到入口保护删除，系统补入安全与合规专家复核安全边界。",
                0.82,
            )

        if _primary_signals("database_analysis", {"query_semantics_weakened"}):
            _add_if_requested(
                "database_analysis",
                "检测到查询语义放宽，系统补入数据库专家复核结果集扩大、索引命中和访问边界。",
                0.76,
            )

        if _primary_signals(
            "performance_reliability",
            {"idempotency_guard_removed", "lock_guard_removed", "bulk_processing_risk", "transactional_side_effect"},
        ):
            _add_if_requested(
                "performance_reliability",
                "检测到幂等/锁/批量/事务副作用风险，系统补入性能与可靠性专家复核并发、超时和失败语义。",
                0.82,
            )

        maintainability_signals = _primary_signals(
            "maintainability_code_health",
            {
                "naming_convention_violation",
                "magic_value_literal",
                "exception_swallowed",
                "comment_contract_unimplemented",
                "python_mutable_default_arg",
                "go_unchecked_error_return",
                "typescript_any_type",
                "typescript_non_null_assertion",
            },
        )
        if maintainability_signals:
            _add_if_requested(
                "maintainability_code_health",
                "检测到命名规范、魔法值或异常处理质量退化，系统补入可维护性与代码健康专家复核语言层质量问题。",
                0.72,
            )

        if _primary_signals(
            "correctness_business",
            {"python_mutable_default_arg", "go_unchecked_error_return", "typescript_non_null_assertion"},
        ):
            _add_if_requested(
                "correctness_business",
                "检测到可变默认参数、错误返回被忽略或非空断言等正确性风险，系统补入正确性与业务专家复核运行时行为。",
                0.77,
            )

        if _primary_signals("performance_reliability", {"go_goroutine_leak_risk"}):
            _add_if_requested(
                "performance_reliability",
                "检测到 goroutine 生命周期控制不足，系统补入性能与可靠性专家复核资源释放和退出路径。",
                0.79,
            )

        selected_set = set(selected_ids)
        filtered_skipped = [
            item for item in skipped_entries if str(item.get("expert_id") or "").strip() not in selected_set
        ]
        return selected_ids, selected_entries, filtered_skipped

    def _merge_routing_plan(
        self,
        *,
        subject: ReviewSubject,
        experts: list[ExpertProfile],
        baseline_routes: dict[str, dict[str, object]],
        candidate_hunks: list[dict[str, object]],
        llm_plan: dict[str, object],
    ) -> dict[str, dict[str, object]]:
        candidate_index = {str(item["candidate_id"]): item for item in candidate_hunks}
        skipped_by_id = {
            str(item.get("expert_id") or ""): str(item.get("reason") or "").strip()
            for item in list(llm_plan.get("skipped_experts", []) or [])
            if isinstance(item, dict)
        }
        route_entries = {
            str(item.get("expert_id") or ""): item
            for item in list(llm_plan.get("expert_routes", []) or [])
            if isinstance(item, dict)
        }
        merged: dict[str, dict[str, object]] = {}
        for expert in experts:
            baseline = dict(baseline_routes.get(expert.expert_id) or {})
            entry = route_entries.get(expert.expert_id)
            if not entry:
                if expert.expert_id in skipped_by_id:
                    baseline["routeable"] = False
                    baseline["skip_reason"] = skipped_by_id[expert.expert_id]
                    baseline["routing_source"] = "llm_skip"
                    baseline["confidence"] = 0.35
                merged[expert.expert_id] = baseline
                continue
            candidate = candidate_index.get(str(entry.get("candidate_id") or ""))
            if not candidate:
                merged[expert.expert_id] = baseline
                continue
            routeable = bool(entry.get("routeable", True))
            changed_lines = self._normalize_changed_lines(candidate.get("line_start"))
            merged[expert.expert_id] = {
                **baseline,
                "expert_id": expert.expert_id,
                "file_path": str(candidate.get("file_path") or baseline.get("file_path") or ""),
                "line_start": int(candidate.get("line_start") or baseline.get("line_start") or 1),
                "target_hunk": {
                    "file_path": candidate.get("file_path"),
                    "hunk_header": candidate.get("hunk_header"),
                    "start_line": candidate.get("line_start"),
                    "end_line": candidate.get("line_start"),
                    "changed_lines": changed_lines,
                    "excerpt": candidate.get("excerpt"),
                },
                "repo_hits": dict(candidate.get("repo_hits") or {}),
                "routing_reason": str(entry.get("reason") or baseline.get("routing_reason") or ""),
                "confidence": float(entry.get("confidence") or baseline.get("confidence") or 0.0),
                "routeable": routeable,
                "skip_reason": "" if routeable else str(skipped_by_id.get(expert.expert_id) or entry.get("reason") or baseline.get("skip_reason") or ""),
                "routing_source": "llm",
            }
        return merged

    def _normalize_changed_lines(self, line_start: object) -> list[int]:
        try:
            value = int(line_start or 1)
        except (TypeError, ValueError):
            value = 1
        return [value]

    def _match_candidate_id(
        self,
        candidate_hunks: list[dict[str, object]],
        route: dict[str, object] | None,
    ) -> str:
        route = route or {}
        file_path = str(route.get("file_path") or "")
        line_start = int(route.get("line_start") or 0)
        for item in candidate_hunks:
            if str(item.get("file_path") or "") == file_path and int(item.get("line_start") or 0) == line_start:
                return str(item.get("candidate_id") or "")
        return ""

    def _format_repo_matches(self, repo_hits: dict[str, object]) -> str:
        matches = list(repo_hits.get("matches", []) or [])
        fragments: list[str] = []
        for item in matches[:4]:
            path = str(item.get("path") or "").strip()
            snippet = str(item.get("snippet") or "").strip()
            if path:
                fragments.append(f"{path} {snippet}".strip() if snippet else path)
        for symbol_context in list(repo_hits.get("symbol_contexts", []) or [])[:2]:
            if not isinstance(symbol_context, dict):
                continue
            symbol = str(symbol_context.get("symbol") or "").strip()
            definitions = list(symbol_context.get("definitions", []) or [])
            references = list(symbol_context.get("references", []) or [])
            if symbol:
                fragments.append(f"symbol:{symbol} defs={len(definitions)} refs={len(references)}")
        return "\n".join(fragments)
