from __future__ import annotations

import json
import re
import sys
from typing import Any


SECTION_MAP = {
    "api": "api_definitions",
    "接口": "api_definitions",
    "request": "request_fields",
    "请求": "request_fields",
    "入参": "request_fields",
    "response": "response_fields",
    "响应": "response_fields",
    "出参": "response_fields",
    "table": "table_definitions",
    "表结构": "table_definitions",
    "schema": "table_definitions",
    "sequence": "business_sequences",
    "时序": "business_sequences",
    "业务流程": "business_sequences",
    "流程": "business_sequences",
    "performance": "performance_requirements",
    "性能": "performance_requirements",
    "security": "security_requirements",
    "安全": "security_requirements",
}


def normalize_lines(content: str) -> list[str]:
    return [line.rstrip() for line in content.replace("\r", "").split("\n")]


def detect_section(line: str) -> str | None:
    raw = line.strip()
    if not raw.startswith("#"):
        return None
    lowered = raw.lower().lstrip("#").strip()
    for keyword, target in SECTION_MAP.items():
        if keyword in lowered:
            return target
    return None


def parse_design_doc(content: str) -> dict[str, list[str]]:
    structured = {
        "api_definitions": [],
        "request_fields": [],
        "response_fields": [],
        "table_definitions": [],
        "business_sequences": [],
        "performance_requirements": [],
        "security_requirements": [],
    }
    current_section: str | None = None
    for line in normalize_lines(content):
        stripped = line.strip()
        if not stripped:
            continue
        section = detect_section(stripped)
        if section:
            current_section = section
            continue
        if current_section is None:
            continue
        if stripped.startswith(("-", "*")) or re.match(r"^\d+\.", stripped):
            structured[current_section].append(stripped.lstrip("-*0123456789. ").strip())
            continue
        if "|" in stripped:
            structured[current_section].append(stripped)
            continue
        if len(stripped) <= 120:
            structured[current_section].append(stripped)
    return structured


def build_requirement_points(structured_design: dict[str, list[str]]) -> list[str]:
    requirements: list[str] = []
    for key in (
        "api_definitions",
        "request_fields",
        "response_fields",
        "table_definitions",
        "business_sequences",
        "performance_requirements",
        "security_requirements",
    ):
        for item in structured_design.get(key, [])[:8]:
            if item not in requirements:
                requirements.append(item)
    return requirements


def flatten_repo_context(repo_context: dict[str, Any]) -> str:
    text_parts: list[str] = []
    for key in ("primary_context",):
        value = repo_context.get(key)
        if isinstance(value, dict):
            text_parts.append(str(value.get("snippet") or ""))
    for item in repo_context.get("related_contexts", []) or []:
        if isinstance(item, dict):
            text_parts.append(str(item.get("snippet") or ""))
    for item in repo_context.get("matches", []) or []:
        if isinstance(item, dict):
            text_parts.append(str(item.get("snippet") or ""))
    return "\n".join(part for part in text_parts if part)


def compare_requirements(requirements: list[str], corpus: str) -> tuple[list[str], list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    extra: list[str] = []
    lowered_corpus = corpus.lower()
    for item in requirements:
        tokens = [token for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", item) if len(token) >= 2]
        if not tokens:
            continue
        overlap = sum(1 for token in tokens[:6] if token.lower() in lowered_corpus)
        if overlap >= max(1, min(2, len(tokens[:6]))):
            matched.append(item)
        else:
            missing.append(item)
    for path in re.findall(r"([A-Za-z0-9_./-]+\.(?:ts|tsx|js|jsx|py|java|sql|prisma))", corpus):
        if path not in extra and not any(path in item for item in requirements):
            extra.append(path)
    return matched[:10], missing[:10], extra[:6]


def infer_conflicts(structured_design: dict[str, list[str]], diff_text: str) -> list[str]:
    conflicts: list[str] = []
    for item in structured_design.get("response_fields", [])[:8]:
        field_tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", item)
        if not field_tokens:
            continue
        token = field_tokens[0]
        if token.lower() not in diff_text.lower():
            conflicts.append(f"设计中定义的返回字段未在本次实现上下文中明确出现：{item}")
    for item in structured_design.get("security_requirements", [])[:4]:
        if "鉴权" in item or "授权" in item or "permission" in item.lower():
            if not re.search(r"auth|permission|token|role|guard", diff_text, re.IGNORECASE):
                conflicts.append(f"设计包含安全要求，但当前实现片段未看到明确安全控制：{item}")
    return conflicts[:8]


def main() -> int:
    payload = json.loads(sys.stdin.read() or "{}")
    design_docs = [
        item
        for item in payload.get("design_docs", [])
        if isinstance(item, dict) and str(item.get("content") or "").strip()
    ]
    diff_text = str(payload.get("subject", {}).get("unified_diff") or payload.get("diff") or "")
    repo_context = payload.get("repo_context") if isinstance(payload.get("repo_context"), dict) else {}
    if not repo_context and isinstance(payload.get("related_files"), list):
        repo_context = {"related_contexts": payload.get("related_files")}
    structured_design = {
        "api_definitions": [],
        "request_fields": [],
        "response_fields": [],
        "table_definitions": [],
        "business_sequences": [],
        "performance_requirements": [],
        "security_requirements": [],
    }
    for doc in design_docs:
        extracted = parse_design_doc(str(doc.get("content") or ""))
        for key, values in extracted.items():
            for value in values:
                if value not in structured_design[key]:
                    structured_design[key].append(value)
    requirements = build_requirement_points(structured_design)
    corpus = "\n".join(
        [
            diff_text,
            flatten_repo_context(repo_context),
            "\n".join(str(item) for item in payload.get("changed_files", []) or []),
        ]
    )
    matched, missing, extra = compare_requirements(requirements, corpus)
    conflicts = infer_conflicts(structured_design, corpus)
    alignment_status = "insufficient_design_context"
    if design_docs:
        alignment_status = "aligned"
        if missing and matched:
            alignment_status = "partially_aligned"
        elif missing or conflicts:
            alignment_status = "misaligned"
    result = {
        "summary": f"已读取 {len(design_docs)} 份详细设计文档，提取 {len(requirements)} 个关键设计点，命中 {len(matched)} 个，实现缺口 {len(missing)} 个。",
        "design_doc_titles": [str(item.get("title") or item.get("filename") or "详细设计文档") for item in design_docs],
        "structured_design": structured_design,
        "design_alignment_status": alignment_status,
        "matched_implementation_points": matched,
        "missing_implementation_points": missing,
        "extra_implementation_points": extra,
        "conflicting_implementation_points": conflicts,
        "uncertain_points": [],
    }
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
