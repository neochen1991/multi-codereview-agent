from __future__ import annotations

import json

from app.domain.models.expert_profile import ExpertProfile
from app.domain.models.review import ReviewSubject


def build_routing_system_prompt() -> str:
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


def build_expert_selection_system_prompt() -> str:
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


def build_routing_user_prompt(
    *,
    subject: ReviewSubject,
    experts: list[ExpertProfile],
    candidate_hunks: list[dict[str, object]],
    business_changed_files: list[str],
    target_file_full_diff: str,
    related_diff_summary: str,
    source_context_summary: str,
    language_general_guidance: str,
    format_repo_matches,
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
        candidate_sections.append(
            "\n".join(
                [
                    f"- candidate_id: {item['candidate_id']}",
                    f"  file_path: {item['file_path']}",
                    f"  line_start: {item['line_start']}",
                    f"  hunk_header: {item['hunk_header']}",
                    f"  excerpt: {str(item['excerpt'])[:700]}",
                    f"  repo_context: {format_repo_matches(dict(item.get('repo_hits') or {}))[:500]}",
                ]
            )
        )
    primary_file_path = (
        str(candidate_hunks[0]["file_path"])
        if candidate_hunks
        else str(business_changed_files[0])
        if business_changed_files
        else ""
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


def build_expert_selection_user_prompt(
    *,
    subject: ReviewSubject,
    experts: list[ExpertProfile],
    requested_expert_ids: list[str],
    business_changed_files: list[str],
    target_file_full_diff: str,
    related_diff_summary: str,
    java_quality_summary: str,
    language_general_guidance: str,
) -> str:
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
        f"Java 质量信号摘要:\n{java_quality_summary}\n\n"
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
