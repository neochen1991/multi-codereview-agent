from __future__ import annotations

import json
import re
import sys
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.domain.models.runtime_settings import RuntimeSettings
from app.services.llm_chat_service import LLMChatService


class DesignAPI(BaseModel):
    name: str = ""
    method: str = ""
    path: str = ""
    purpose: str = ""
    source_quote: str = ""


class DesignField(BaseModel):
    name: str = ""
    location: str = ""
    field_type: str = ""
    required: str = ""
    description: str = ""
    source_quote: str = ""


class DesignTable(BaseModel):
    table_name: str = ""
    fields: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    indexes: list[str] = Field(default_factory=list)
    source_quote: str = ""


class DesignSequenceStep(BaseModel):
    step: str = ""
    actor: str = ""
    action: str = ""
    expected_result: str = ""
    source_quote: str = ""


class DesignRequirement(BaseModel):
    title: str = ""
    requirement: str = ""
    source_quote: str = ""


class StructuredDesignDoc(BaseModel):
    document_title: str = ""
    business_goal: str = ""
    api_definitions: list[DesignAPI] = Field(default_factory=list)
    request_fields: list[DesignField] = Field(default_factory=list)
    response_fields: list[DesignField] = Field(default_factory=list)
    table_definitions: list[DesignTable] = Field(default_factory=list)
    business_sequences: list[DesignSequenceStep] = Field(default_factory=list)
    performance_requirements: list[DesignRequirement] = Field(default_factory=list)
    security_requirements: list[DesignRequirement] = Field(default_factory=list)
    unknown_or_ambiguous_points: list[str] = Field(default_factory=list)


StructuredDesignDoc.model_rebuild()


def _build_parser_prompt(design_docs: list[dict[str, Any]]) -> tuple[str, str]:
    system_prompt = """
你是企业研发体系中的“详细设计文档语义解析器”。

你的唯一任务是：从输入的 Markdown 详细设计文档中提取固定结构的 JSON。

严格要求：
1. 只能根据文档原文提取，不允许脑补未出现的设计要求。
2. 必须输出一个 JSON 对象，不能输出 Markdown、解释、前后缀、代码块围栏。
3. 如果某项信息不存在，返回空字符串、空数组，不允许编造。
4. source_quote 必须尽量保留原文中的短句或字段描述，便于追溯。
5. 关注这些语义维度：
   - api_definitions
   - request_fields
   - response_fields
   - table_definitions
   - business_sequences
   - performance_requirements
   - security_requirements
   - unknown_or_ambiguous_points
""".strip()
    doc_blocks: list[str] = []
    for index, doc in enumerate(design_docs, start=1):
        title = str(doc.get("title") or doc.get("filename") or f"设计文档 {index}")
        content = str(doc.get("content") or "").strip()
        doc_blocks.append(f"## 文档 {index}: {title}\n{content}")
    user_prompt = f"""
请把下面的详细设计文档解析成固定 JSON，字段必须完全包含以下结构：

{{
  "document_title": "string",
  "business_goal": "string",
  "api_definitions": [
    {{
      "name": "string",
      "method": "string",
      "path": "string",
      "purpose": "string",
      "source_quote": "string"
    }}
  ],
  "request_fields": [
    {{
      "name": "string",
      "location": "string",
      "field_type": "string",
      "required": "string",
      "description": "string",
      "source_quote": "string"
    }}
  ],
  "response_fields": [
    {{
      "name": "string",
      "location": "string",
      "field_type": "string",
      "required": "string",
      "description": "string",
      "source_quote": "string"
    }}
  ],
  "table_definitions": [
    {{
      "table_name": "string",
      "fields": ["string"],
      "constraints": ["string"],
      "indexes": ["string"],
      "source_quote": "string"
    }}
  ],
  "business_sequences": [
    {{
      "step": "string",
      "actor": "string",
      "action": "string",
      "expected_result": "string",
      "source_quote": "string"
    }}
  ],
  "performance_requirements": [
    {{
      "title": "string",
      "requirement": "string",
      "source_quote": "string"
    }}
  ],
  "security_requirements": [
    {{
      "title": "string",
      "requirement": "string",
      "source_quote": "string"
    }}
  ],
  "unknown_or_ambiguous_points": ["string"]
}}

如果同类信息在多个文档中重复出现，请去重并合并。

详细设计文档如下：
{chr(10).join(doc_blocks)}
""".strip()
    return system_prompt, user_prompt


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```$", "", cleaned)
    return cleaned.strip()


def _parse_design_docs_with_llm(design_docs: list[dict[str, Any]], runtime: RuntimeSettings) -> StructuredDesignDoc:
    if not design_docs:
        raise RuntimeError("未上传详细设计文档，无法执行设计一致性检查")
    llm_service = LLMChatService()
    resolution = llm_service.resolve_main_agent(runtime)
    timeout_seconds = (
        runtime.light_llm_timeout_seconds
        if runtime.default_analysis_mode == "light"
        else runtime.standard_llm_timeout_seconds
    )
    max_attempts = (
        runtime.light_llm_retry_count
        if runtime.default_analysis_mode == "light"
        else runtime.standard_llm_retry_count
    )
    system_prompt, user_prompt = _build_parser_prompt(design_docs)
    result = llm_service.complete_text(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        resolution=resolution,
        runtime_settings=runtime,
        fallback_text="",
        temperature=0.0,
        allow_fallback=False,
        timeout_seconds=float(max(timeout_seconds, 60)),
        max_attempts=max(1, min(max_attempts, 2)),
        log_context={
            "phase": "design_spec_parse",
            "tool_name": "design_spec_alignment",
            "design_doc_count": len(design_docs),
        },
    )
    raw_json = _strip_code_fence(result.text)
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"详细设计文档语义解析失败，模型未返回合法 JSON: {exc}") from exc
    try:
        return StructuredDesignDoc.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError(f"详细设计文档语义解析失败，结构不符合预期: {exc}") from exc


def _flatten_design_requirements(structured: StructuredDesignDoc) -> list[str]:
    points: list[str] = []
    for api in structured.api_definitions:
        points.append(" ".join(part for part in [api.method, api.path, api.purpose] if part).strip())
    for field in structured.request_fields + structured.response_fields:
        points.append(" ".join(part for part in [field.name, field.field_type, field.description] if part).strip())
    for table in structured.table_definitions:
        points.append(" ".join([table.table_name, *table.fields, *table.constraints, *table.indexes]).strip())
    for step in structured.business_sequences:
        points.append(" ".join(part for part in [step.actor, step.action, step.expected_result] if part).strip())
    deduped: list[str] = []
    for item in [point.strip() for point in points if point.strip()]:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _collect_nonfunctional_observations(structured: StructuredDesignDoc, corpus: str) -> list[str]:
    observations: list[str] = []
    lowered_corpus = corpus.lower()
    for requirement in structured.performance_requirements[:4]:
        text = " ".join(part for part in [requirement.title, requirement.requirement] if part).strip()
        if not text:
            continue
        observations.append(f"性能要求待专项验证：{text}")
    for requirement in structured.security_requirements[:4]:
        text = " ".join(part for part in [requirement.title, requirement.requirement] if part).strip()
        if not text:
            continue
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)
        if any(token.lower() in lowered_corpus for token in tokens[:4]):
            continue
        observations.append(f"安全要求待专项验证：{text}")
    return observations[:8]


def _flatten_repo_context(repo_context: dict[str, Any]) -> str:
    text_parts: list[str] = []
    primary = repo_context.get("primary_context")
    if isinstance(primary, dict):
        text_parts.append(str(primary.get("snippet") or ""))
    for key in ("related_contexts", "matches", "symbol_contexts"):
        for item in repo_context.get(key, []) or []:
            if isinstance(item, dict):
                text_parts.append(str(item.get("snippet") or ""))
                for nested in ("definitions", "references"):
                    for child in item.get(nested, []) or []:
                        if isinstance(child, dict):
                            text_parts.append(str(child.get("snippet") or ""))
    return "\n".join(part for part in text_parts if part)


def _compare_requirements(requirements: list[str], corpus: str) -> tuple[list[str], list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    extra: list[str] = []
    lowered_corpus = corpus.lower()
    for item in requirements:
        tokens = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", item) if len(token) >= 2]
        if not tokens:
            continue
        overlap = sum(1 for token in tokens[:8] if token.lower() in lowered_corpus)
        if overlap >= max(1, min(2, len(tokens[:8]))):
            matched.append(item)
        else:
            missing.append(item)
    for path in re.findall(r"([A-Za-z0-9_./-]+\.(?:ts|tsx|js|jsx|py|java|sql|prisma))", corpus):
        if path not in extra and not any(path in item for item in requirements):
            extra.append(path)
    return matched[:12], missing[:12], extra[:8]


def _infer_conflicts(structured: StructuredDesignDoc, diff_text: str) -> list[str]:
    conflicts: list[str] = []
    lowered_diff = diff_text.lower()
    for field in structured.response_fields[:10]:
        if field.name and field.name.lower() not in lowered_diff:
            conflicts.append(f"设计定义的返回字段未在本次实现片段中明确出现：{field.name}")
    for step in structured.business_sequences[:6]:
        if step.action:
            action_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", step.action)
            if action_tokens and not any(token.lower() in lowered_diff for token in action_tokens[:4]):
                conflicts.append(f"设计中的关键业务步骤未在本次实现片段中明确体现：{step.action}")
    for requirement in structured.security_requirements[:4]:
        text = f"{requirement.title} {requirement.requirement}".lower()
        if any(token in text for token in ["auth", "permission", "鉴权", "授权", "role"]) and not re.search(
            r"auth|permission|token|role|guard", diff_text, re.IGNORECASE
        ):
            conflicts.append(f"设计包含安全要求，但当前实现片段未看到明确控制：{requirement.requirement or requirement.title}")
    return conflicts[:10]


def _alignment_status(
    design_docs: list[dict[str, Any]],
    matched: list[str],
    missing: list[str],
    conflicts: list[str],
) -> str:
    if not design_docs:
        return "insufficient_design_context"
    if conflicts or (missing and not matched):
        return "misaligned"
    if missing:
        return "partially_aligned"
    return "aligned"


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    design_docs = [
        item
        for item in payload.get("design_docs", [])
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    runtime = RuntimeSettings.model_validate(dict(payload.get("runtime") or {}))
    structured_design = _parse_design_docs_with_llm(design_docs, runtime)

    diff_text = str(payload.get("subject", {}).get("unified_diff") or payload.get("diff") or "")
    repo_context = payload.get("repo_context") if isinstance(payload.get("repo_context"), dict) else {}
    if not repo_context and isinstance(payload.get("related_files"), list):
        repo_context = {"related_contexts": payload.get("related_files")}
    corpus = "\n".join(
        [
            diff_text,
            _flatten_repo_context(repo_context),
            "\n".join(str(item) for item in payload.get("changed_files", []) or []),
        ]
    )

    requirements = _flatten_design_requirements(structured_design)
    matched, missing, extra = _compare_requirements(requirements, corpus)
    conflicts = _infer_conflicts(structured_design, corpus)
    nonfunctional_observations = _collect_nonfunctional_observations(structured_design, corpus)
    result = {
        "summary": (
            f"已通过大模型解析 {len(design_docs)} 份详细设计文档，抽取 {len(requirements)} 个关键设计点，"
            f"命中 {len(matched)} 个，缺口 {len(missing)} 个，冲突 {len(conflicts)} 个，"
            f"另有 {len(nonfunctional_observations)} 个非功能要求待专项验证。"
        ),
        "design_doc_titles": [str(item.get("title") or item.get("filename") or "详细设计文档") for item in design_docs],
        "structured_design": structured_design.model_dump(mode="json"),
        "design_alignment_status": _alignment_status(design_docs, matched, missing, conflicts),
        "matched_implementation_points": matched,
        "missing_implementation_points": missing,
        "extra_implementation_points": extra,
        "conflicting_implementation_points": conflicts,
        "uncertain_points": [
            *structured_design.unknown_or_ambiguous_points[:4],
            *nonfunctional_observations,
        ][:8],
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
