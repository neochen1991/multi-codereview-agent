from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.services.java_quality_signal_extractor import JavaQualitySignalExtractor


class CodeObservationExtractor:
    """语言无关的 observation 提取入口。"""

    def __init__(self) -> None:
        self._java_extractor = JavaQualitySignalExtractor()

    def extract(
        self,
        *,
        file_path: str,
        target_hunk: dict[str, Any] | None = None,
        repository_context: dict[str, Any] | None = None,
        full_diff: str = "",
    ) -> dict[str, object]:
        language = self._infer_language(file_path)
        if language == "java":
            payload = self._java_extractor.extract(
                file_path=file_path,
                target_hunk=target_hunk,
                repository_context=repository_context,
                full_diff=full_diff,
            )
            normalized = dict(payload or {})
            normalized["language"] = "java"
            return normalized
        return self._extract_generic(
            language=language,
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            full_diff=full_diff,
        )

    def _infer_language(self, file_path: str) -> str:
        suffix = Path(str(file_path or "")).suffix.lower()
        if suffix == ".java":
            return "java"
        if suffix in {".ts", ".tsx", ".js", ".jsx"}:
            return "typescript"
        if suffix == ".py":
            return "python"
        if suffix == ".go":
            return "go"
        if suffix in {".sql"}:
            return "sql"
        if suffix in {".yaml", ".yml", ".json", ".toml", ".ini", ".properties"}:
            return "config"
        return "text"

    def _extract_generic(
        self,
        *,
        language: str,
        file_path: str,
        target_hunk: dict[str, Any] | None,
        repository_context: dict[str, Any] | None,
        full_diff: str,
    ) -> dict[str, object]:
        target_hunk = dict(target_hunk or {})
        repository_context = dict(repository_context or {})
        diff_excerpt = str(target_hunk.get("excerpt") or "").strip()
        primary_context = dict(repository_context.get("primary_context") or {})
        combined = "\n".join(
            part
            for part in [
                diff_excerpt,
                str(full_diff or "").strip(),
                str(primary_context.get("snippet") or "").strip(),
            ]
            if part
        )
        if not combined.strip():
            return self._empty_payload(language)

        signals: list[str] = []
        matched_terms: list[str] = []
        signal_terms: dict[str, list[str]] = {}
        summary_parts: list[str] = []

        detectors = [
            ("comment_contract_unimplemented", self._detect_generic_comment_contract(combined)),
            ("exception_swallowed", self._detect_generic_exception_swallowed(language, combined)),
            ("exception_semantics_weakened", self._detect_generic_exception_semantics(language, combined)),
            ("dead_code_after_return", self._detect_dead_code_after_return(combined)),
            ("magic_value_literal", self._detect_generic_magic_values(combined)),
            ("loop_call_amplification", self._detect_generic_loop_call_amplification(language, combined)),
            ("python_mutable_default_arg", self._detect_python_mutable_default_arg(language, combined)),
            ("go_unchecked_error_return", self._detect_go_unchecked_error_return(language, combined)),
            ("go_goroutine_leak_risk", self._detect_go_goroutine_leak_risk(language, combined)),
            ("typescript_any_type", self._detect_typescript_any_type(language, combined)),
            ("typescript_non_null_assertion", self._detect_typescript_non_null_assertion(language, combined)),
        ]
        summaries = {
            "comment_contract_unimplemented": "检测到注释、TODO 或占位实现承诺的行为没有落地",
            "exception_swallowed": "检测到异常被吞掉或错误处理被弱化",
            "exception_semantics_weakened": "检测到异常后返回成功、空值或默认值，错误语义被弱化",
            "dead_code_after_return": "检测到 return/throw 后仍存在新增可执行代码",
            "magic_value_literal": "检测到条件判断或业务逻辑中新增魔法值",
            "loop_call_amplification": "检测到循环中新增仓储、网络或外部依赖调用",
            "python_mutable_default_arg": "检测到 Python 函数新增可变默认参数",
            "go_unchecked_error_return": "检测到 Go 错误返回值被忽略",
            "go_goroutine_leak_risk": "检测到 Go goroutine 缺少退出或上下文控制",
            "typescript_any_type": "检测到 TypeScript 新增 any 类型逃逸",
            "typescript_non_null_assertion": "检测到 TypeScript 新增非空断言",
        }
        for signal_name, terms in detectors:
            normalized_terms = self._dedupe([str(item).strip() for item in terms if str(item).strip()])
            if not normalized_terms:
                continue
            signals.append(signal_name)
            matched_terms.extend(normalized_terms)
            signal_terms[signal_name] = normalized_terms
            summary_parts.append(summaries[signal_name])

        deduped_signals = self._dedupe(signals)
        observations = self._build_generic_observations(
            language=language,
            file_path=file_path,
            target_hunk=target_hunk,
            combined_text=combined,
            signal_terms=signal_terms,
        )
        return {
            "language": language,
            "signals": deduped_signals,
            "deterministic_signals": deduped_signals,
            "contextual_signals": [],
            "signal_strengths": {signal: "deterministic" for signal in deduped_signals},
            "analysis_stages": {
                "rule_stage": (
                    f"已命中 {len(deduped_signals)} 个语言通用确定性信号"
                    if deduped_signals
                    else "未命中语言通用确定性信号"
                ),
                "observation_stage": (
                    f"已生成 {len(observations)} 个通用结构化观察点，供专家逐条复核和引用"
                    if observations
                    else "未提取到结构化观察点，后续由规则、上下文和 LLM 深审补充判断"
                ),
                "llm_stage": "LLM 负责结合通用 observation、专家职责和源码上下文做最终判断",
            },
            "summary": "；".join(summary_parts),
            "matched_terms": self._dedupe(matched_terms)[:12],
            "signal_terms": {key: self._dedupe(value)[:8] for key, value in signal_terms.items()},
            "observations": observations,
        }

    def _detect_generic_comment_contract(self, text: str) -> list[str]:
        lines = self._added_or_context_lines(text)
        comment_lines = [
            line
            for line in lines
            if self._is_comment_line(line) or re.search(r"\b(todo|fixme|hack|xxx)\b", line, re.IGNORECASE)
        ]
        if not comment_lines:
            return []
        code_blob = "\n".join(line for line in lines if line not in comment_lines).lower()
        contract_pairs = [
            (("扣减库存", "库存", "deduct inventory", "reserve"), ("inventory", "reserve", "deduct", "库存")),
            (("发送事件", "事件", "publish event", "domain event"), ("publish", "eventbus", "emit", "dispatch", "outbox")),
            (("发送通知", "notify", "通知"), ("notify", "message", "publish", "send")),
            (("缓存", "cache"), ("cache", "redis", "set", "put")),
            (("调用接口", "调用下游", "remote", "invoke", "call api"), ("client", "api", "gateway", "fetch", "request", "axios")),
            (("重试", "retry"), ("retry", "backoff", "attempt")),
            (("校验", "validate"), ("validate", "check", "assert")),
        ]
        for comment in comment_lines:
            lowered = comment.lower()
            for source_tokens, impl_tokens in contract_pairs:
                if any(token in comment or token in lowered for token in source_tokens):
                    if not any(token in code_blob for token in impl_tokens):
                        return [comment[:80]]
        stub_terms = [
            line[:80]
            for line in lines
            if not self._is_comment_line(line)
            and (
                re.search(r"\b(pass|todo|notimplemented|not implemented)\b", line, re.IGNORECASE)
                or re.search(r"throw\s+new\s+Error\s*\([^)]*(todo|not implemented|未实现)", line, re.IGNORECASE)
            )
        ]
        return stub_terms[:1]

    def _detect_generic_exception_swallowed(self, language: str, text: str) -> list[str]:
        normalized = "\n".join(self._added_or_context_lines(text))
        lowered = normalized.lower()
        terms: list[str] = []
        if language == "python" and re.search(r"except\b[^:\n]*:\s*(pass|return\s+none)?", lowered, re.DOTALL):
            terms.extend(["except", "pass"])
        if language in {"typescript", "javascript"} and re.search(r"catch\s*\([^)]*\)\s*\{\s*(//[^\n]*)?\s*\}", normalized, re.DOTALL):
            terms.extend(["catch", "empty"])
        if language == "go" and re.search(r"if\s+err\s*!=\s*nil\s*\{\s*(return\s+nil|return\s*$)?\s*\}", normalized, re.DOTALL):
            terms.extend(["err", "return nil"])
        if re.search(r"catch|except|err\s*!=\s*nil", lowered) and any(
            token in lowered for token in ["pass", "return none", "return null", "return nil", "console.log", "print(", "logger.debug"]
        ):
            terms.append("swallowed-error")
        return self._dedupe(terms)

    def _detect_generic_exception_semantics(self, language: str, text: str) -> list[str]:
        normalized = "\n".join(self._added_or_context_lines(text))
        lowered = normalized.lower()
        if not any(token in lowered for token in ["catch", "except", "err !=", "rescue"]):
            return []
        terms: list[str] = []
        if re.search(r"return\s+(true|success|ok|null|none|nil|\[\]|\{\}|0)\b", lowered):
            terms.append("fallback_return")
        if language == "go" and re.search(r"return\s+[^,\n]+,\s*nil\b", lowered):
            terms.append("nil_error_return")
        return terms

    def _detect_dead_code_after_return(self, text: str) -> list[str]:
        lines = self._added_or_context_lines(text)
        terms: list[str] = []
        for index, line in enumerate(lines[:-1]):
            lowered = line.strip().lower()
            if not re.match(r"^(return|throw|raise)\b", lowered):
                continue
            next_line = lines[index + 1].strip()
            if next_line and not self._is_comment_line(next_line) and next_line not in {"}", "};"}:
                terms.extend([line[:48], next_line[:48]])
                break
        return terms

    def _detect_generic_magic_values(self, text: str) -> list[str]:
        terms: list[str] = []
        for line in self._added_or_context_lines(text):
            stripped = line.strip()
            if self._is_comment_line(stripped):
                continue
            if re.search(r"\b(if|else if|case|switch|return)\b", stripped) and re.search(r"(?<![\w.])-?\d{2,}\b|['\"][A-Z0-9_-]{3,}['\"]", stripped):
                terms.append(stripped[:80])
        return terms[:3]

    def _detect_generic_loop_call_amplification(self, language: str, text: str) -> list[str]:
        joined = "\n".join(self._added_or_context_lines(text))
        loop_pattern = re.compile(r"\b(for|while|forEach|map|filter|reduce|range)\b", re.IGNORECASE)
        call_pattern = re.compile(
            r"\b(fetch|axios|request|http|client|gateway|repository|repo|dao|mapper|query|execute|save|insert|update|delete|select)\b",
            re.IGNORECASE,
        )
        for match in loop_pattern.finditer(joined):
            window = joined[match.start() : match.start() + 700]
            call_match = call_pattern.search(window)
            if call_match:
                return [match.group(1), call_match.group(1)]
        return []

    def _detect_python_mutable_default_arg(self, language: str, text: str) -> list[str]:
        if language != "python":
            return []
        terms: list[str] = []
        for line in self._added_or_context_lines(text):
            stripped = line.strip()
            if re.search(r"def\s+\w+\([^)]*=\s*(\[\]|\{\}|set\(\)|dict\(\)|list\(\))", stripped):
                terms.append(stripped[:100])
        return terms[:2]

    def _detect_go_unchecked_error_return(self, language: str, text: str) -> list[str]:
        if language != "go":
            return []
        terms: list[str] = []
        for line in self._added_or_context_lines(text):
            stripped = line.strip()
            if re.search(r"(^|[=:]\s*)_,\s*err\s*:=", stripped) or re.search(r"(^|[=:]\s*)\w+\s*,\s*_\s*:=", stripped):
                terms.append(stripped[:100])
            elif re.search(r"\b_\s*=\s*\w+\([^)]*\)", stripped):
                terms.append(stripped[:100])
        return terms[:3]

    def _detect_go_goroutine_leak_risk(self, language: str, text: str) -> list[str]:
        if language != "go":
            return []
        joined = "\n".join(self._added_or_context_lines(text))
        terms: list[str] = []
        for match in re.finditer(r"\bgo\s+[\w.]+\([^)]*\)", joined):
            window = joined[max(0, match.start() - 180) : match.end() + 360].lower()
            if not any(token in window for token in ["ctx", "context", "done", "cancel", "select", "defer"]):
                terms.append(match.group(0)[:100])
        return terms[:3]

    def _detect_typescript_any_type(self, language: str, text: str) -> list[str]:
        if language != "typescript":
            return []
        terms: list[str] = []
        for line in self._added_or_context_lines(text):
            stripped = line.strip()
            if self._is_comment_line(stripped):
                continue
            if re.search(r"(:\s*any\b|as\s+any\b|<any>)", stripped):
                terms.append(stripped[:100])
        return terms[:3]

    def _detect_typescript_non_null_assertion(self, language: str, text: str) -> list[str]:
        if language != "typescript":
            return []
        terms: list[str] = []
        for line in self._added_or_context_lines(text):
            stripped = line.strip()
            if self._is_comment_line(stripped):
                continue
            if re.search(r"\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*!\.", stripped) or re.search(
                r"\b[A-Za-z_$][\w$]*!\)", stripped
            ):
                terms.append(stripped[:100])
        return terms[:3]

    def _build_generic_observations(
        self,
        *,
        language: str,
        file_path: str,
        target_hunk: dict[str, Any],
        combined_text: str,
        signal_terms: dict[str, list[str]],
    ) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        for signal_name, terms in signal_terms.items():
            profile = self._generic_observation_profile(signal_name)
            if not profile:
                continue
            line_start = self._locate_line_start(terms, target_hunk, combined_text)
            normalized_terms = self._dedupe(terms)
            observations.append(
                {
                    "observation_id": self._build_observation_id(signal_name, file_path, line_start, normalized_terms),
                    "kind": str(profile.get("kind") or signal_name),
                    "signal": signal_name,
                    "language": language,
                    "file_path": str(file_path or "").strip(),
                    "line_start": line_start,
                    "line_end": line_start,
                    "summary": str(profile.get("summary") or "").format(terms=" / ".join(normalized_terms[:2])),
                    "evidence": self._build_evidence(normalized_terms, target_hunk, combined_text)[:3],
                    "risk_hints": [str(item).strip() for item in list(profile.get("risk_hints") or []) if str(item).strip()][:4],
                    "related_symbols": normalized_terms[:3],
                    "tags": [str(item).strip() for item in list(profile.get("tags") or []) if str(item).strip()][:4],
                    "confidence": float(profile.get("confidence") or 0.7),
                }
            )
        return observations

    def _generic_observation_profile(self, signal_name: str) -> dict[str, object]:
        profiles: dict[str, dict[str, object]] = {
            "comment_contract_unimplemented": {
                "kind": "declared_intent_without_implementation",
                "summary": "检测到注释、TODO 或占位实现承诺的行为没有落地：{terms}",
                "risk_hints": ["承诺未落地", "语义误导", "行为缺失"],
                "confidence": 0.82,
                "tags": ["contract", "comment"],
            },
            "exception_swallowed": {
                "kind": "error_handling_weakened",
                "summary": "检测到异常处理被削弱或吞掉异常：{terms}",
                "risk_hints": ["异常丢失", "排障困难", "补偿风险"],
                "confidence": 0.76,
                "tags": ["exception"],
            },
            "exception_semantics_weakened": {
                "kind": "error_semantics_changed",
                "summary": "检测到异常后返回语义被弱化：{terms}",
                "risk_hints": ["错误被伪装成成功", "补偿链路失真"],
                "confidence": 0.79,
                "tags": ["exception", "semantics"],
            },
            "dead_code_after_return": {
                "kind": "dead_code_after_terminal_statement",
                "summary": "检测到 return/throw 后仍存在可执行代码：{terms}",
                "risk_hints": ["不可达代码", "实现遗漏", "控制流错误"],
                "confidence": 0.8,
                "tags": ["control-flow"],
            },
            "magic_value_literal": {
                "kind": "literal_embedded_in_business_logic",
                "summary": "检测到业务逻辑中嵌入字面量：{terms}",
                "risk_hints": ["魔法值", "配置收敛不足", "维护成本"],
                "confidence": 0.72,
                "tags": ["maintainability"],
            },
            "loop_call_amplification": {
                "kind": "control_flow_with_external_call",
                "summary": "检测到循环体中的外部依赖调用：{terms}",
                "risk_hints": ["批量路径放大", "N+1 或串行调用风险"],
                "confidence": 0.84,
                "tags": ["performance"],
            },
            "python_mutable_default_arg": {
                "kind": "mutable_default_argument",
                "summary": "检测到 Python 可变默认参数，跨请求/调用可能共享状态：{terms}",
                "risk_hints": ["状态泄漏", "并发污染", "跨调用副作用"],
                "confidence": 0.82,
                "tags": ["python", "correctness"],
            },
            "go_unchecked_error_return": {
                "kind": "unchecked_error_result",
                "summary": "检测到 Go 错误返回值被忽略：{terms}",
                "risk_hints": ["错误丢失", "补偿缺失", "状态不一致"],
                "confidence": 0.8,
                "tags": ["go", "exception"],
            },
            "go_goroutine_leak_risk": {
                "kind": "goroutine_without_lifecycle_control",
                "summary": "检测到 goroutine 缺少上下文取消或退出控制：{terms}",
                "risk_hints": ["goroutine 泄漏", "资源占用", "后台任务失控"],
                "confidence": 0.74,
                "tags": ["go", "performance"],
            },
            "typescript_any_type": {
                "kind": "type_safety_escape",
                "summary": "检测到 TypeScript any 类型逃逸：{terms}",
                "risk_hints": ["类型约束失效", "运行时错误", "接口契约弱化"],
                "confidence": 0.76,
                "tags": ["typescript", "maintainability"],
            },
            "typescript_non_null_assertion": {
                "kind": "null_safety_bypass",
                "summary": "检测到 TypeScript 非空断言绕过空值保护：{terms}",
                "risk_hints": ["空值崩溃", "运行时异常", "契约假设未验证"],
                "confidence": 0.78,
                "tags": ["typescript", "correctness"],
            },
        }
        return dict(profiles.get(signal_name) or {})

    def _added_or_context_lines(self, text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in str(text or "").splitlines():
            if raw_line.startswith("+++") or raw_line.startswith("---") or raw_line.startswith("@@"):
                continue
            line = raw_line[1:] if raw_line.startswith(("+", "-", " ")) else raw_line
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        return lines

    def _is_comment_line(self, line: str) -> bool:
        stripped = str(line or "").strip()
        return stripped.startswith(("//", "#", "/*", "*", "<!--")) or stripped.endswith("-->")

    def _locate_line_start(self, terms: list[str], target_hunk: dict[str, Any], combined_text: str) -> int:
        changed_lines = [int(item) for item in list(target_hunk.get("changed_lines") or []) if str(item).isdigit()]
        start_line = int(target_hunk.get("start_line") or (changed_lines[0] if changed_lines else 1) or 1)
        excerpt = str(target_hunk.get("excerpt") or combined_text or "")
        lowered_terms = [term.lower() for term in terms if term]
        current_line = start_line
        for raw_line in excerpt.splitlines():
            if raw_line.startswith("@@"):
                match = re.search(r"\+(\d+)", raw_line)
                if match:
                    current_line = int(match.group(1))
                continue
            visible_line = raw_line[1:] if raw_line.startswith(("+", "-", " ")) else raw_line
            if raw_line.startswith("+") and not raw_line.startswith("+++"):
                lowered = visible_line.lower()
                if any(term in lowered for term in lowered_terms):
                    return current_line
                current_line += 1
            elif not raw_line.startswith("-"):
                current_line += 1
        return start_line

    def _build_evidence(self, terms: list[str], target_hunk: dict[str, Any], combined_text: str) -> list[str]:
        evidence: list[str] = []
        text = str(target_hunk.get("excerpt") or combined_text or "")
        lowered_terms = [term.lower() for term in terms if term]
        for line in text.splitlines():
            visible = line[1:].strip() if line.startswith(("+", "-", " ")) else line.strip()
            if visible and any(term in visible.lower() for term in lowered_terms):
                evidence.append(visible[:160])
        if not evidence and terms:
            evidence.append("命中通用质量信号：" + " / ".join(terms[:3]))
        return self._dedupe(evidence)

    def _build_observation_id(self, signal_name: str, file_path: str, line_start: int, terms: list[str]) -> str:
        raw = f"{signal_name}|{file_path}|{line_start}|{'/'.join(terms[:3])}"
        return "obs_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    def _empty_payload(self, language: str) -> dict[str, object]:
        return {
            "language": str(language or "text"),
            "signals": [],
            "deterministic_signals": [],
            "contextual_signals": [],
            "signal_strengths": {},
            "analysis_stages": {
                "rule_stage": "未启用语言特定的确定性规则或信号提取",
                "observation_stage": "未生成结构化观察点",
                "llm_stage": "仅能依赖通用专家提示做语义审查",
            },
            "summary": "",
            "matched_terms": [],
            "signal_terms": {},
            "observations": [],
        }
