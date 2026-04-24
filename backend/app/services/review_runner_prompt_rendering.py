from __future__ import annotations


def build_active_skill_summary(active_skills: list[object]) -> str:
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


def build_active_skill_fulltext(active_skills: list[object]) -> str:
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


def build_design_doc_summary(design_docs: list[dict[str, object]]) -> str:
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


def build_review_spec_summary(review_spec: str) -> str:
    if not review_spec.strip():
        return "未提供额外规范文档，请至少遵守职责边界、证据优先、修复建议可执行三条规则。"
    lines = [line.strip() for line in review_spec.splitlines() if line.strip()]
    return "\n".join(lines[:18])


def build_bound_documents_summary(bound_documents: list[object]) -> str:
    if not bound_documents:
        return "未绑定额外专家参考文档。"
    lines: list[str] = []
    for item in bound_documents[:8]:
        title = str(getattr(item, "title", "") or "").strip() or "未命名文档"
        doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
        source_filename = str(getattr(item, "source_filename", "") or "").strip()
        tags = [str(tag).strip() for tag in list(getattr(item, "tags", []) or []) if str(tag).strip()]
        line = f"- [{doc_type}] {title}"
        if source_filename:
            line += f" · {source_filename}"
        if tags:
            line += f" · 标签: {' / '.join(tags[:4])}"
        matched_sections = list(getattr(item, "matched_sections", []) or [])
        outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
        if matched_sections:
            matched_paths = [
                str(getattr(section, "path", "") or "").strip()
                for section in matched_sections[:2]
                if str(getattr(section, "path", "") or "").strip()
            ]
            if matched_paths:
                line += f" · 命中章节: {' / '.join(matched_paths)}"
        elif outline:
            line += f" · 章节索引: {' / '.join(outline[:3])}"
        lines.append(line)
    return "\n".join(lines)


def build_bound_documents_fulltext(bound_documents: list[object]) -> str:
    if not bound_documents:
        return "《专家绑定参考文档》开始\n未绑定额外专家参考文档。\n《专家绑定参考文档》结束"
    sections: list[str] = ["《专家绑定参考文档》开始"]
    for index, item in enumerate(bound_documents, start=1):
        title = str(getattr(item, "title", "") or "").strip() or f"文档 {index}"
        doc_type = str(getattr(item, "doc_type", "") or "reference").strip()
        source_filename = str(getattr(item, "source_filename", "") or "").strip()
        matched_sections = list(getattr(item, "matched_sections", []) or [])
        outline = [str(value).strip() for value in list(getattr(item, "indexed_outline", []) or []) if str(value).strip()]
        sections.append(f"## 文档 {index}: {title}")
        sections.append(f"- 类型: {doc_type}")
        if source_filename:
            sections.append(f"- 来源文件: {source_filename}")
        if matched_sections:
            sections.append("- 命中章节如下：")
            for section in matched_sections[:6]:
                path = str(getattr(section, "path", "") or "").strip() or str(getattr(section, "title", "") or "").strip()
                summary = str(getattr(section, "summary", "") or "").strip()
                content = str(getattr(section, "content", "") or "").strip() or "空章节"
                sections.append(f"### {path}")
                if summary:
                    sections.append(f"摘要: {summary}")
                sections.append(content[:1600])
        elif outline:
            sections.append("- 未命中具体章节，以下为文档目录索引：")
            sections.extend([f"  - {value}" for value in outline[:12]])
        else:
            content = str(getattr(item, "content", "") or "").strip() or "空文档"
            sections.append(content[:2000])
    sections.append("《专家绑定参考文档》结束")
    return "\n".join(sections)


def build_rule_screening_summary(rule_screening: dict[str, object]) -> str:
    total_rules = int(rule_screening.get("total_rules") or 0)
    if total_rules <= 0:
        return "当前未绑定可执行规则卡。"
    must_review_count = int(rule_screening.get("must_review_count") or 0)
    possible_hit_count = int(rule_screening.get("possible_hit_count") or 0)
    lines = [
        f"- 已遍历规则: {total_rules}",
        f"- 强命中规则: {must_review_count}",
        f"- 候选规则: {possible_hit_count}",
    ]
    matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
    if matched_rules:
        lines.append("- 本轮优先带入审查的规则:")
        for item in matched_rules[:5]:
            title = str(item.get("title") or item.get("rule_id") or "").strip()
            priority = str(item.get("priority") or "P2").strip()
            scene_path = str(item.get("scene_path") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if title:
                label = f"[{priority}] {title}"
                if scene_path:
                    label = f"{label}（{scene_path}）"
                lines.append(f"  - {label} · {reason or '命中规则信号'}")
    return "\n".join(lines)


def build_rule_screening_fulltext(rule_screening: dict[str, object]) -> str:
    total_rules = int(rule_screening.get("total_rules") or 0)
    if total_rules <= 0:
        return "《规则遍历结果》开始\n当前未绑定可执行规则卡。\n《规则遍历结果》结束"
    sections = [
        "《规则遍历结果》开始",
        f"- 已遍历规则总数: {total_rules}",
        f"- 强命中规则数: {int(rule_screening.get('must_review_count') or 0)}",
        f"- 候选规则数: {int(rule_screening.get('possible_hit_count') or 0)}",
    ]
    matched_rules = list(rule_screening.get("matched_rules_for_llm", []) or [])
    if matched_rules:
        sections.append("- 本轮应优先遵守并逐条核查的规则卡：")
        for item in matched_rules[:6]:
            title = str(item.get("title") or item.get("rule_id") or "").strip()
            priority = str(item.get("priority") or "P2").strip()
            scene_path = str(item.get("scene_path") or "").strip()
            description = str(item.get("description") or "").strip()
            language = str(item.get("language") or "").strip()
            matched_terms = [
                str(value).strip()
                for value in list(item.get("matched_terms", []) or [])[:6]
                if str(value).strip()
            ]
            sections.append(f"## [{priority}] {title}")
            if scene_path:
                sections.append(f"场景路径: {scene_path}")
            if description:
                sections.append(f"规则描述: {description}")
            if language:
                sections.append(f"语言: {language}")
            if matched_terms:
                sections.append(f"命中关键词: {' / '.join(matched_terms)}")
            problem_code_example = str(item.get("problem_code_example") or "").strip()
            problem_code_line = str(item.get("problem_code_line") or "").strip()
            false_positive_code = str(item.get("false_positive_code") or "").strip()
            if problem_code_example:
                sections.append("问题代码示例:")
                sections.append(problem_code_example[:800])
            if problem_code_line:
                sections.append(f"重点关注代码行模式: {problem_code_line[:500]}")
            if false_positive_code:
                sections.append("误报代码参考:")
                sections.append(false_positive_code[:800])
    sections.append("《规则遍历结果》结束")
    return "\n".join(sections)
