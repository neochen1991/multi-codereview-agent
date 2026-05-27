from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


class JavaQualitySignalExtractor:
    """提取 Java 通用质量信号。"""

    DETERMINISTIC_SIGNALS = {
        "query_semantics_weakened",
        "unbounded_query_risk",
        "exception_swallowed",
        "event_ordering_risk",
        "loop_call_amplification",
        "comment_contract_unimplemented",
        "lock_guard_removed",
    }

    def extract(
        self,
        *,
        file_path: str,
        target_hunk: dict[str, Any] | None = None,
        repository_context: dict[str, Any] | None = None,
        full_diff: str = "",
    ) -> dict[str, object]:
        if Path(str(file_path or "")).suffix.lower() != ".java":
            return self._empty_payload(language="text")

        target_hunk = dict(target_hunk or {})
        repository_context = dict(repository_context or {})
        current_class = dict(repository_context.get("current_class_context") or {})
        primary_context = dict(repository_context.get("primary_context") or {})

        diff_excerpt = str(target_hunk.get("excerpt") or "").strip()
        current_snippet = str(current_class.get("snippet") or "").strip()
        primary_snippet = str(primary_context.get("snippet") or "").strip()
        related_snippets = self._collect_repository_context_snippets(repository_context)
        combined = "\n".join(
            part
            for part in [diff_excerpt, full_diff, current_snippet, primary_snippet, *related_snippets]
            if str(part).strip()
        )
        diff_lower = diff_excerpt.lower()
        combined_lower = combined.lower()

        signals: list[str] = []
        matched_terms: list[str] = []
        signal_terms: dict[str, list[str]] = {}
        summary_parts: list[str] = []

        if self._detect_query_semantics_weakened(diff_lower):
            signals.append("query_semantics_weakened")
            query_terms = ["equal", "like", "contains"]
            matched_terms.extend(query_terms)
            signal_terms["query_semantics_weakened"] = query_terms
            summary_parts.append("检测到查询语义从精确匹配放宽为模糊匹配")

        if self._detect_unbounded_query_risk(diff_lower, combined_lower):
            signals.append("unbounded_query_risk")
            query_risk_terms = ["limit", "page", "chunk"]
            matched_terms.extend(query_risk_terms)
            signal_terms["unbounded_query_risk"] = query_risk_terms
            summary_parts.append("检测到分页或 limit 保护被移除")

        security_guard_terms = self._detect_security_guard_removed(diff_excerpt)
        if security_guard_terms:
            signals.append("security_guard_removed")
            matched_terms.extend(security_guard_terms)
            signal_terms["security_guard_removed"] = security_guard_terms
            summary_parts.append("检测到入口校验、权限或身份一致性保护被删除")

        idempotency_terms = self._detect_idempotency_guard_removed(diff_excerpt)
        if idempotency_terms:
            signals.append("idempotency_guard_removed")
            matched_terms.extend(idempotency_terms)
            signal_terms["idempotency_guard_removed"] = idempotency_terms
            summary_parts.append("检测到幂等或重复处理保护被删除")

        lock_guard_terms = self._detect_lock_guard_removed(diff_excerpt)
        if lock_guard_terms:
            signals.append("lock_guard_removed")
            matched_terms.extend(lock_guard_terms)
            signal_terms["lock_guard_removed"] = lock_guard_terms
            summary_parts.append("检测到锁或并发保护被删除")

        query_plan_terms = self._detect_query_plan_risk(diff_excerpt, combined)
        if query_plan_terms:
            signals.append("query_plan_risk")
            matched_terms.extend(query_plan_terms)
            signal_terms["query_plan_risk"] = query_plan_terms
            summary_parts.append("检测到查询条件、排序或模糊匹配可能带来索引失配与计划退化")

        naming_violation = self._detect_naming_convention_violation(diff_excerpt)
        if naming_violation:
            signals.append("naming_convention_violation")
            matched_terms.extend(naming_violation)
            signal_terms["naming_convention_violation"] = naming_violation
            summary_parts.append("检测到常量或标识符命名退化")

        magic_value_terms = self._detect_magic_value_literals(diff_excerpt)
        if magic_value_terms:
            signals.append("magic_value_literal")
            matched_terms.extend(magic_value_terms)
            signal_terms["magic_value_literal"] = magic_value_terms
            summary_parts.append("检测到疑似魔法值直接落在业务逻辑中")

        if self._detect_exception_swallowed(diff_lower, combined_lower):
            signals.append("exception_swallowed")
            swallow_terms = ["catch", "printstacktrace", "throw", "logger"]
            matched_terms.extend(swallow_terms)
            signal_terms["exception_swallowed"] = swallow_terms
            summary_parts.append("检测到 catch 块吞异常或异常处理被移除")

        exception_semantics_terms = self._detect_exception_semantics_weakened(diff_excerpt, combined)
        if exception_semantics_terms:
            signals.append("exception_semantics_weakened")
            matched_terms.extend(exception_semantics_terms)
            signal_terms["exception_semantics_weakened"] = exception_semantics_terms
            summary_parts.append("检测到异常后的返回语义被弱化或成功语义被误保留")

        if self._detect_event_ordering_risk(diff_lower):
            signals.append("event_ordering_risk")
            event_terms = ["publish", "save", "pullDomainEvents"]
            matched_terms.extend(event_terms)
            signal_terms["event_ordering_risk"] = event_terms
            summary_parts.append("检测到事件发布与持久化顺序存在风险")

        if self._detect_factory_bypass(diff_lower):
            signals.append("factory_bypass")
            factory_terms = ["create", "new"]
            matched_terms.extend(factory_terms)
            signal_terms["factory_bypass"] = factory_terms
            summary_parts.append("检测到工厂方法被直接构造绕过")

        cross_layer_terms = self._detect_cross_layer_dependency(
            file_path=file_path,
            diff_excerpt=diff_excerpt,
            combined_context=combined,
        )
        if cross_layer_terms:
            signals.append("cross_layer_dependency")
            matched_terms.extend(cross_layer_terms)
            signal_terms["cross_layer_dependency"] = cross_layer_terms
            summary_parts.append("检测到分层边界被直接穿透")

        transactional_side_effect_terms = self._detect_transactional_side_effect(
            diff_excerpt=diff_excerpt,
            combined_context=combined,
        )
        if transactional_side_effect_terms:
            signals.append("transactional_side_effect")
            matched_terms.extend(transactional_side_effect_terms)
            signal_terms["transactional_side_effect"] = transactional_side_effect_terms
            summary_parts.append("检测到事务边界内混入外部副作用")

        configuration_behavior_terms = self._detect_configuration_behavior_coupling(
            diff_excerpt=diff_excerpt,
            combined_context=combined,
        )
        if configuration_behavior_terms:
            signals.append("configuration_behavior_coupling")
            matched_terms.extend(configuration_behavior_terms)
            signal_terms["configuration_behavior_coupling"] = configuration_behavior_terms
            summary_parts.append("检测到配置开关直接控制业务副作用或核心路径")

        loop_amplification_terms = self._detect_loop_call_amplification(diff_excerpt, combined)
        if loop_amplification_terms:
            signals.append("loop_call_amplification")
            matched_terms.extend(loop_amplification_terms)
            signal_terms["loop_call_amplification"] = loop_amplification_terms
            summary_parts.append("检测到循环内仓储或远程调用，批量路径可能被逐条放大")

        bulk_processing_terms = self._detect_bulk_processing_risk(diff_excerpt, combined)
        if bulk_processing_terms:
            signals.append("bulk_processing_risk")
            matched_terms.extend(bulk_processing_terms)
            signal_terms["bulk_processing_risk"] = bulk_processing_terms
            summary_parts.append("检测到集合/批处理路径缺少批量边界，可能逐条持久化或逐条外调")

        comment_contract_terms = self._detect_comment_contract_unimplemented(diff_excerpt, combined)
        if comment_contract_terms:
            signals.append("comment_contract_unimplemented")
            matched_terms.extend(comment_contract_terms)
            signal_terms["comment_contract_unimplemented"] = comment_contract_terms
            summary_parts.append("检测到注释、TODO 或占位实现承诺的行为没有在当前实现中落地")

        observations = self._build_observations(
            file_path=file_path,
            target_hunk=target_hunk,
            repository_context=repository_context,
            signal_terms=signal_terms,
            combined_text=combined,
        )

        deduped_signals = self._dedupe(signals)
        return {
            "language": "java",
            "signals": deduped_signals,
            "deterministic_signals": [
                signal for signal in deduped_signals if signal in self.DETERMINISTIC_SIGNALS
            ],
            "contextual_signals": [
                signal for signal in deduped_signals if signal not in self.DETERMINISTIC_SIGNALS
            ],
            "signal_strengths": {
                signal: ("deterministic" if signal in self.DETERMINISTIC_SIGNALS else "contextual")
                for signal in deduped_signals
            },
            "analysis_stages": {
                "rule_stage": (
                    f"已命中 {len([signal for signal in deduped_signals if signal in self.DETERMINISTIC_SIGNALS])} 个确定性信号，"
                    "可优先形成 observation 或直接兜底 finding"
                ),
                "observation_stage": (
                    f"已生成 {len(observations)} 个结构化观察点，供专家逐条复核和引用"
                    if observations
                    else "当前未生成结构化观察点，后续主要依赖规则、上下文和 LLM 深审"
                ),
                "llm_stage": "LLM 负责结合规则、源码上下文和 observation 做最终专业判断，而不是重复发现确定性信号",
            },
            "summary": "；".join(summary_parts),
            "matched_terms": self._dedupe(matched_terms)[:12],
            "signal_terms": {key: self._dedupe(value)[:8] for key, value in signal_terms.items()},
            "observations": observations,
        }

    def _build_observations(
        self,
        *,
        file_path: str,
        target_hunk: dict[str, Any],
        repository_context: dict[str, Any],
        signal_terms: dict[str, list[str]],
        combined_text: str = "",
    ) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        for signal_name, terms in signal_terms.items():
            normalized_terms = [str(item).strip() for item in list(terms or []) if str(item).strip()]
            if not normalized_terms:
                continue
            profile = self._observation_profile(signal_name)
            if not profile:
                continue
            line_start = self._locate_observation_line_start(
                terms=normalized_terms,
                target_hunk=target_hunk,
                repository_context=repository_context,
            )
            if signal_name == "exception_swallowed":
                line_start = self._locate_exception_swallowed_line_start(
                    target_hunk=target_hunk,
                    combined_text=combined_text,
                    fallback=line_start,
                )
            summary = str(profile.get("summary") or "").format(
                terms=" / ".join(normalized_terms[:2]),
            )
            evidence = self._build_observation_evidence(
                terms=normalized_terms,
                target_hunk=target_hunk,
                repository_context=repository_context,
            )
            observation_id = self._build_observation_id(
                signal_name=signal_name,
                file_path=file_path,
                line_start=line_start,
                terms=normalized_terms,
            )
            observations.append(
                {
                    "observation_id": observation_id,
                    "kind": str(profile.get("kind") or signal_name),
                    "signal": signal_name,
                    "language": "java",
                    "file_path": str(file_path or "").strip(),
                    "line_start": line_start,
                    "line_end": line_start,
                    "summary": summary,
                    "evidence": evidence[:3],
                    "risk_hints": [str(item).strip() for item in list(profile.get("risk_hints") or []) if str(item).strip()][:4],
                    "related_symbols": normalized_terms[:3],
                    "tags": [str(item).strip() for item in list(profile.get("tags") or []) if str(item).strip()][:4],
                    "confidence": float(profile.get("confidence") or 0.7),
                }
            )
        return observations

    def _observation_profile(self, signal_name: str) -> dict[str, object]:
        profiles: dict[str, dict[str, object]] = {
            "loop_call_amplification": {
                "kind": "control_flow_with_external_call",
                "summary": "检测到循环体中的外部依赖调用现象：{terms}",
                "risk_hints": ["批量路径放大", "数据库/网络往返", "N+1 或串行调用风险"],
                "confidence": 0.86,
            },
            "comment_contract_unimplemented": {
                "kind": "declared_intent_without_implementation",
                "summary": "检测到注释、TODO、占位实现或方法意图与当前实现可能不一致：{terms}",
                "risk_hints": ["承诺未落地", "语义误导", "业务行为缺失"],
                "confidence": 0.84,
            },
            "naming_convention_violation": {
                "kind": "weak_identifier_signal",
                "summary": "检测到标识符命名质量退化现象：{terms}",
                "risk_hints": ["可读性下降", "语义不清", "维护成本升高"],
                "confidence": 0.72,
            },
            "magic_value_literal": {
                "kind": "literal_embedded_in_business_logic",
                "summary": "检测到业务逻辑中嵌入字面量现象：{terms}",
                "risk_hints": ["魔法值", "可维护性风险", "配置收敛不足"],
                "confidence": 0.74,
            },
            "unbounded_query_risk": {
                "kind": "query_without_bound",
                "summary": "检测到查询缺少边界保护现象：{terms}",
                "risk_hints": ["无分页", "全量扫描", "数据库压力"],
                "confidence": 0.8,
            },
            "security_guard_removed": {
                "kind": "security_guard_removed",
                "summary": "检测到入口校验、权限或身份一致性保护被删除：{terms}",
                "risk_hints": ["入口保护删除", "越权/非法输入风险", "安全边界弱化"],
                "confidence": 0.86,
                "tags": ["security", "guard"],
            },
            "idempotency_guard_removed": {
                "kind": "idempotency_guard_removed",
                "summary": "检测到幂等或重复处理保护被删除：{terms}",
                "risk_hints": ["重复处理", "幂等失效", "消息/请求重放风险"],
                "confidence": 0.84,
                "tags": ["idempotency", "reliability"],
            },
            "lock_guard_removed": {
                "kind": "lock_guard_removed",
                "summary": "检测到锁或并发保护被删除：{terms}",
                "risk_hints": ["并发保护删除", "竞态条件", "重复提交/状态错乱"],
                "confidence": 0.84,
                "tags": ["lock", "concurrency"],
            },
            "query_semantics_weakened": {
                "kind": "query_semantics_changed",
                "summary": "检测到查询语义变化现象：{terms}",
                "risk_hints": ["结果集扩大", "索引命中下降", "业务语义偏移"],
                "confidence": 0.76,
            },
            "query_plan_risk": {
                "kind": "query_plan_risk",
                "summary": "检测到查询计划可能退化的现象：{terms}",
                "risk_hints": ["索引失配", "排序放大", "扫描范围扩大"],
                "confidence": 0.79,
                "tags": ["query", "performance"],
            },
            "exception_swallowed": {
                "kind": "error_handling_weakened",
                "summary": "检测到异常处理被削弱或吞掉异常的现象：{terms}",
                "risk_hints": ["异常丢失", "排障困难", "补偿风险"],
                "confidence": 0.77,
            },
            "exception_semantics_weakened": {
                "kind": "error_semantics_changed",
                "summary": "检测到异常后返回语义被弱化的现象：{terms}",
                "risk_hints": ["错误被伪装成成功", "补偿链路失真", "状态不一致"],
                "confidence": 0.8,
                "tags": ["exception", "semantics"],
            },
            "event_ordering_risk": {
                "kind": "state_and_event_ordering_change",
                "summary": "检测到事件发布与持久化顺序变化现象：{terms}",
                "risk_hints": ["事件顺序风险", "一致性风险", "领域事件时序异常"],
                "confidence": 0.78,
            },
            "factory_bypass": {
                "kind": "construction_path_changed",
                "summary": "检测到对象构造路径变化现象：{terms}",
                "risk_hints": ["工厂约束绕过", "不变量丢失", "领域建模退化"],
                "confidence": 0.73,
                "tags": ["construction", "domain-model"],
            },
            "cross_layer_dependency": {
                "kind": "cross_layer_dependency",
                "summary": "检测到跨层直接依赖现象：{terms}",
                "risk_hints": ["分层耦合", "职责穿透", "架构边界弱化"],
                "confidence": 0.78,
                "tags": ["layering", "architecture"],
            },
            "transactional_side_effect": {
                "kind": "transactional_side_effect",
                "summary": "检测到事务边界与外部副作用耦合现象：{terms}",
                "risk_hints": ["事务内副作用", "一致性风险", "重试/回滚放大"],
                "confidence": 0.82,
                "tags": ["transaction", "side-effect"],
            },
            "configuration_behavior_coupling": {
                "kind": "configuration_behavior_coupling",
                "summary": "检测到配置或开关直接决定核心业务行为的现象：{terms}",
                "risk_hints": ["配置驱动业务语义", "环境差异扩大", "隐藏分支"],
                "confidence": 0.75,
                "tags": ["configuration", "behavior"],
            },
            "bulk_processing_risk": {
                "kind": "bulk_processing_boundary_missing",
                "summary": "检测到批处理或集合路径缺少批量边界现象：{terms}",
                "risk_hints": ["逐条持久化/外调", "批处理放大", "吞吐退化"],
                "confidence": 0.82,
                "tags": ["bulk", "performance"],
            },
        }
        return dict(profiles.get(signal_name) or {})

    def _empty_payload(self, language: str) -> dict[str, object]:
        return {
            "language": str(language or "text"),
            "signals": [],
            "summary": "",
            "matched_terms": [],
            "signal_terms": {},
            "observations": [],
        }

    def _build_observation_id(
        self,
        *,
        signal_name: str,
        file_path: str,
        line_start: int,
        terms: list[str],
    ) -> str:
        raw = f"{signal_name}|{file_path}|{line_start}|{'|'.join(terms[:3])}"
        return f"obs_{hashlib.md5(raw.encode('utf-8')).hexdigest()[:12]}"

    def _locate_observation_line_start(
        self,
        *,
        terms: list[str],
        target_hunk: dict[str, Any],
        repository_context: dict[str, Any],
    ) -> int:
        numbered_sources = [
            str((repository_context.get("current_class_context") or {}).get("snippet") or ""),
            str((repository_context.get("primary_context") or {}).get("snippet") or ""),
            str((repository_context.get("current_method_context") or {}).get("snippet") or ""),
        ]
        for source in numbered_sources:
            for line_start, text in self._iter_numbered_lines(source):
                if self._line_matches_terms(text, terms):
                    return line_start

        changed_lines = [
            int(value)
            for value in list(target_hunk.get("changed_lines") or [])
            if str(value).strip().isdigit()
        ]
        if not changed_lines:
            excerpt_header = ""
            excerpt_lines_raw = str(target_hunk.get("excerpt") or "").splitlines()
            if excerpt_lines_raw and str(excerpt_lines_raw[0] or "").strip().startswith("@@"):
                excerpt_header = str(excerpt_lines_raw[0] or "").strip()
            start_line, line_count = self._parse_hunk_new_file_range(
                str(target_hunk.get("hunk_header") or "").strip() or excerpt_header
            )
            if start_line is not None and line_count > 0:
                changed_lines = list(range(start_line, start_line + line_count))
        excerpt_lines = [
            line
            for line in str(target_hunk.get("excerpt") or "").splitlines()
            if line.strip() and not line.strip().startswith("@@")
        ]
        changed_index = 0
        for raw_line in excerpt_lines:
            stripped = raw_line.strip()
            if stripped.startswith("-"):
                continue
            candidate_line = changed_lines[changed_index] if changed_index < len(changed_lines) else None
            if stripped.startswith("+") or not stripped.startswith("-"):
                changed_index += 1 if candidate_line is not None else 0
            cleaned = re.sub(r"^\s*[+ ]\s*", "", stripped)
            if candidate_line is not None and self._line_matches_terms(cleaned, terms):
                return candidate_line

        fallback = target_hunk.get("start_line") or (changed_lines[0] if changed_lines else 1)
        try:
            return int(fallback or 1)
        except Exception:
            return 1

    def _locate_exception_swallowed_line_start(
        self,
        *,
        target_hunk: dict[str, Any],
        combined_text: str,
        fallback: int,
    ) -> int:
        diff_candidates = [
            str(target_hunk.get("excerpt") or ""),
            str(combined_text or ""),
        ]
        for source in diff_candidates:
            located = self._locate_exception_swallowed_from_diff(source)
            if located is not None:
                return located
        return fallback

    def _locate_exception_swallowed_from_diff(self, diff_text: str) -> int | None:
        current_new_line: int | None = None
        for raw_line in str(diff_text or "").splitlines():
            stripped = str(raw_line or "").lstrip()
            if stripped.startswith("@@"):
                start_line, _line_count = self._parse_hunk_new_file_range(stripped)
                current_new_line = start_line
                continue
            if stripped.startswith(("+++", "---", "diff --git ", "index ")):
                continue
            if current_new_line is None:
                continue

            is_removed = stripped.startswith("-")
            is_added = stripped.startswith("+")
            content = stripped[1:].strip() if is_removed or is_added else stripped.strip()
            lowered = content.lower()
            if "catch" in lowered and "printstacktrace" not in lowered:
                return current_new_line
            if is_removed and any(token in lowered for token in ("printstacktrace", "logger.error", "log.error", "throw new", "throw e")):
                return current_new_line

            if not is_removed:
                current_new_line += 1
        return None

    def _parse_hunk_new_file_range(self, hunk_header: str) -> tuple[int | None, int]:
        match = re.search(r"\+\s*(\d+)(?:,(\d+))?", str(hunk_header or ""))
        if not match:
            return None, 0
        start_line = int(match.group(1))
        line_count = int(match.group(2) or 1)
        return start_line, max(1, line_count)

    def _build_observation_evidence(
        self,
        *,
        terms: list[str],
        target_hunk: dict[str, Any],
        repository_context: dict[str, Any],
    ) -> list[str]:
        evidence: list[str] = []
        sources = [
            str(target_hunk.get("excerpt") or ""),
            str((repository_context.get("current_class_context") or {}).get("snippet") or ""),
            str((repository_context.get("primary_context") or {}).get("snippet") or ""),
        ]
        for source in sources:
            for raw_line in str(source or "").splitlines():
                cleaned = re.sub(r"^\s*\d+\s*\|\s*", "", re.sub(r"^\s*[+ ]\s*", "", raw_line)).strip()
                if not cleaned:
                    continue
                if self._line_matches_terms(cleaned, terms):
                    evidence.append(cleaned[:180])
                if len(evidence) >= 3:
                    return self._dedupe(evidence)
        return self._dedupe(evidence or terms[:2])

    def _iter_numbered_lines(self, content: str) -> list[tuple[int, str]]:
        numbered: list[tuple[int, str]] = []
        for raw_line in str(content or "").splitlines():
            match = re.match(r"^\s*(\d+)\s*\|\s*(.*)$", str(raw_line or ""))
            if not match:
                continue
            numbered.append((int(match.group(1)), str(match.group(2) or "").strip()))
        return numbered

    def _line_matches_terms(self, line: str, terms: list[str]) -> bool:
        lowered_line = str(line or "").lower()
        for term in terms:
            normalized = str(term or "").strip().lower()
            if not normalized:
                continue
            if normalized in lowered_line:
                return True
            token_candidates = [
                token
                for token in re.split(r"[^a-zA-Z0-9_:.#]+", normalized)
                if token and token not in {"for", "while", "todo", "public", "private", "protected"}
            ]
            if any(token in lowered_line for token in token_candidates[:3]):
                return True
        return False

    def _detect_query_semantics_weakened(self, diff_lower: str) -> bool:
        return bool(
            ("builder.equal(" in diff_lower and "builder.like(" in diff_lower)
            or ("findby" in diff_lower and "containing" in diff_lower and "+" in diff_lower)
        )

    def _detect_unbounded_query_risk(self, diff_lower: str, combined_lower: str) -> bool:
        removed_limit = any(
            token in diff_lower
            for token in [" limit :", "query.setparameter(\"chunk\"", "pageable", "pagerequest"]
        )
        added_unbounded_select = (
            "select * from" in diff_lower and " order by " in diff_lower and " limit " not in diff_lower
        )
        no_paging_visible = (
            "query.list()" in combined_lower
            and "limit" not in combined_lower
            and "pageable" not in combined_lower
            and "pagerequest" not in combined_lower
            and "setmaxresults" not in combined_lower
        )
        return removed_limit or added_unbounded_select or no_paging_visible

    def _detect_query_plan_risk(self, diff_excerpt: str, combined_context: str) -> list[str]:
        combined = "\n".join([diff_excerpt, combined_context])
        lowered = combined.lower()
        query_shape = any(token in lowered for token in ("select ", "jdbcTemplate.query", "@query", "criteriabuilder", "querywrapper", "lambdaquerywrapper"))
        if not query_shape:
            return []
        like_and_order = (" like " in lowered or "containing" in lowered or "startswith" in lowered or "endswith" in lowered) and " order by " in lowered
        leading_wildcard = bool(re.search(r"like\s+['\"]?%|like\s+\?", lowered))
        fuzzy_condition = any(token in lowered for token in ("findby") ) and any(token in lowered for token in ("containing", "like"))
        if like_and_order or leading_wildcard or fuzzy_condition:
            terms: list[str] = []
            if "like" in lowered or "containing" in lowered:
                terms.append("like")
            if "order by" in lowered:
                terms.append("order_by")
            if "select *" in lowered:
                terms.append("select_all")
            if "jdbcTemplate.query" in combined:
                terms.append("jdbcTemplate.query")
            return terms[:4]
        return []

    def _detect_security_guard_removed(self, diff_excerpt: str) -> list[str]:
        removed = "\n".join(self._removed_diff_lines(diff_excerpt)).lower()
        added = "\n".join(self._added_diff_lines(diff_excerpt)).lower()
        tokens = [
            "@valid",
            "bindingresult",
            "rejectvalue",
            "haspermission",
            "permission",
            "authorize",
            "authenticated",
            "ownerid",
            "tenant",
            "mismatch",
            "csrf",
            "sanitize",
        ]
        removed_terms = [token for token in tokens if token in removed and token not in added]
        if not removed_terms:
            return []
        return removed_terms[:4]

    def _detect_idempotency_guard_removed(self, diff_excerpt: str) -> list[str]:
        removed = "\n".join(self._removed_diff_lines(diff_excerpt)).lower()
        added = "\n".join(self._added_diff_lines(diff_excerpt)).lower()
        tokens = ["idempotent", "idempotency", "duplicate", "deduplicate", "requestid", "eventid", "幂等", "重复消费"]
        if any(token in removed for token in tokens) and not any(token in added for token in tokens):
            return [token for token in tokens if token in removed][:4]
        return []

    def _detect_lock_guard_removed(self, diff_excerpt: str) -> list[str]:
        removed = "\n".join(self._removed_diff_lines(diff_excerpt)).lower()
        added = "\n".join(self._added_diff_lines(diff_excerpt)).lower()
        tokens = ["synchronized", "lock(", "trylock", "unlock", "setnx", "redisson", "mutex"]
        if any(token in removed for token in tokens) and not any(token in added for token in tokens):
            return [token for token in tokens if token in removed][:4]
        return []

    def _removed_diff_lines(self, diff_excerpt: str) -> list[str]:
        lines: list[str] = []
        for line in str(diff_excerpt or "").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("---") or not stripped.startswith("-"):
                continue
            lines.append(stripped[1:].strip())
        return lines

    def _added_diff_lines(self, diff_excerpt: str) -> list[str]:
        lines: list[str] = []
        for line in str(diff_excerpt or "").splitlines():
            stripped = line.lstrip()
            if stripped.startswith("+++") or not stripped.startswith("+"):
                continue
            lines.append(stripped[1:].strip())
        return lines

    def _detect_naming_convention_violation(self, diff_excerpt: str) -> list[str]:
        added_identifiers: list[str] = []
        for line in diff_excerpt.splitlines():
            if not line.startswith("+") or line.startswith("+++"):
                continue
            for pattern in [
                r"\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*=",
                r"\b(?:public|private|protected)\s+[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            ]:
                added_identifiers.extend(re.findall(pattern, line))

        declaration_removed = re.findall(
            r"^-.*?\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([A-Z][A-Z0-9_]{2,})\s*=",
            diff_excerpt,
            flags=re.MULTILINE,
        )
        declaration_added = re.findall(
            r"^\+.*?\b(?:final\s+)?[A-Za-z_][A-Za-z0-9_<>]*\s+([a-z][A-Za-z0-9_]*)\s*=",
            diff_excerpt,
            flags=re.MULTILINE,
        )
        if declaration_removed and declaration_added:
            return [declaration_removed[0], declaration_added[0]]

        removed_constant = re.findall(r"^-.*?\b([A-Z][A-Z0-9_]{2,})\b\s*=", diff_excerpt, flags=re.MULTILINE)
        added_non_constant = re.findall(r"^\+.*?\b([a-z][A-Za-z0-9_]*)\b\s*=", diff_excerpt, flags=re.MULTILINE)
        if removed_constant and added_non_constant:
            return [removed_constant[0], added_non_constant[0]]
        for identifier in added_identifiers:
            if self._looks_like_bad_java_identifier(identifier):
                return [identifier]
        return []

    def _looks_like_bad_java_identifier(self, identifier: str) -> bool:
        normalized = str(identifier or "").strip()
        if len(normalized) < 3:
            return False
        lower = normalized.lower()
        if lower.endswith(("tmp", "temp", "data", "value", "obj", "info")):
            return True
        if "__" in normalized:
            return True
        if re.search(r"[a-z][A-Z]{2,}", normalized):
            return True
        if normalized[0].isupper() and not normalized.isupper():
            return True
        return False

    def _detect_magic_value_literals(self, diff_excerpt: str) -> list[str]:
        suspicious: list[str] = []
        whitelist = {"0", "1", "-1", "2", "10", "100", "1000", "true", "false"}
        for raw_line in diff_excerpt.splitlines():
            line = raw_line.strip()
            if not line.startswith("+") or line.startswith("+++"):
                continue
            lowered = line.lower()
            if any(token in lowered for token in ("static final", "private static final", "public static final", "enum ")):
                continue
            if any(token in lowered for token in ('"http', '"select ', "@value(", "@requestparam(", "@jsonproperty(")):
                continue
            for literal in re.findall(r"(?<![A-Za-z0-9_])(-?\d{2,})(?![A-Za-z0-9_])", line):
                if literal in whitelist:
                    continue
                if literal not in suspicious:
                    suspicious.append(literal)
            for literal in re.findall(r'"([^"\n]{3,})"', line):
                if any(ch.isspace() for ch in literal) and len(literal) <= 12:
                    continue
                if re.fullmatch(r"[A-Za-z0-9_/.-]{3,}", literal) and literal.upper() != literal:
                    continue
                if literal not in suspicious and len(literal) >= 3:
                    suspicious.append(literal)
        return suspicious[:4]

    def _detect_exception_swallowed(self, diff_lower: str, combined_lower: str) -> bool:
        diff_mentions_exception_path = any(
            token in diff_lower
            for token in [
                "catch",
                "exception",
                "throw ",
                "throws ",
                "printstacktrace",
                "logger.",
                "log.",
            ]
        )
        if not diff_mentions_exception_path:
            return False

        removed_handling = any(
            token in diff_lower for token in ["printstacktrace", "logger.error", "log.error", "throw new", "throw e"]
        )
        normalized_added = self._normalized_added_context(combined_lower)
        empty_catch = bool(
            re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", combined_lower, flags=re.DOTALL)
            or re.search(r"catch\s*\([^)]*\)\s*\{\s*\n\s*\}", combined_lower, flags=re.DOTALL)
            or re.search(r"catch\s*\([^)]*\)\s*\{\s*\}", normalized_added, flags=re.DOTALL)
        )
        return removed_handling or empty_catch

    def _normalized_added_context(self, text: str) -> str:
        lines: list[str] = []
        for raw_line in str(text or "").splitlines():
            stripped = raw_line.strip()
            if stripped.startswith(("@@", "+++", "---")):
                continue
            if stripped.startswith("-"):
                continue
            if stripped.startswith("+"):
                stripped = stripped[1:].strip()
            lines.append(stripped)
        return "\n".join(line for line in lines if line)

    def _detect_exception_semantics_weakened(self, diff_excerpt: str, combined_context: str) -> list[str]:
        combined = "\n".join([diff_excerpt, combined_context])
        lowered = combined.lower()
        if "catch" not in lowered:
            return []
        catch_blocks = re.findall(r"catch\s*\([^)]*\)\s*\{(.*?)\}", combined, flags=re.IGNORECASE | re.DOTALL)
        fallback_patterns = [
            r"return\s+true\s*;",
            r"return\s+false\s*;",
            r"return\s+null\s*;",
            r"return\s+[^;]*\.success\s*\(",
            r"return\s+success\s*\(",
            r"return\s+collections\.empty\w*\(",
            r"return\s+list\.of\(",
            r"return\s+optional\.empty\(",
            r"return\s+\d+\s*;",
        ]
        for block in catch_blocks:
            lowered_block = str(block or "").lower()
            if any(re.search(pattern, lowered_block, flags=re.IGNORECASE) for pattern in fallback_patterns):
                fallback = re.search(r"return\s+([^;]+);", str(block or ""), flags=re.IGNORECASE)
                return ["catch", str((fallback.group(1) if fallback else "fallback_return")).strip()]
        return []

    def _detect_event_ordering_risk(self, diff_lower: str) -> bool:
        removed_save = re.search(r"^-.*\brepository\.save\(", diff_lower, flags=re.MULTILINE)
        added_save = re.search(r"^\+.*\brepository\.save\(", diff_lower, flags=re.MULTILINE)
        publish_pos = diff_lower.find("eventbus.publish")
        added_save_pos = diff_lower.rfind("repository.save(")
        return bool(removed_save and added_save and publish_pos != -1 and added_save_pos != -1 and publish_pos < added_save_pos)

    def _detect_factory_bypass(self, diff_lower: str) -> bool:
        return bool(
            re.search(r"^-.*\.[a-z_]*create\s*\(", diff_lower, flags=re.MULTILINE)
            and re.search(r"^\+.*\bnew\s+[a-z_][a-z0-9_]*\s*\(", diff_lower, flags=re.MULTILINE)
        )

    def _detect_cross_layer_dependency(
        self,
        *,
        file_path: str,
        diff_excerpt: str,
        combined_context: str,
    ) -> list[str]:
        lowered_path = str(file_path or "").lower()
        lowered = "\n".join([diff_excerpt, combined_context]).lower()
        controller_like = any(token in lowered_path for token in ("/controller", "/interfaces/", "controller.java")) or any(
            token in lowered for token in ("@restcontroller", "@controller", "class ordercontroller", "class .*controller")
        )
        domain_like = any(token in lowered_path for token in ("/domain/", "/model/"))
        application_like = any(token in lowered_path for token in ("/application/", "/app/"))
        direct_repository = re.search(r"\b([A-Za-z_][A-Za-z0-9_]*(?:repository|repo|dao|mapper))\s*\.", diff_excerpt, flags=re.IGNORECASE)
        infra_import = re.search(r"\bimport\s+.*(?:infrastructure|infra|persistence)\.", combined_context, flags=re.IGNORECASE)
        if controller_like and direct_repository:
            return ["controller", str(direct_repository.group(1) or "").strip()]
        if domain_like and (direct_repository or infra_import):
            return ["domain", str((direct_repository.group(1) if direct_repository else "infrastructure"))]
        if application_like and infra_import and direct_repository:
            return ["application", str(direct_repository.group(1) or "").strip()]
        return []

    def _detect_transactional_side_effect(self, *, diff_excerpt: str, combined_context: str) -> list[str]:
        combined = "\n".join([diff_excerpt, combined_context])
        lowered = combined.lower()
        if "@transactional" not in lowered:
            return []
        persistence_call = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:repository|repo|dao|mapper)|entityManager|jdbcTemplate)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
            combined,
            flags=re.IGNORECASE,
        )
        side_effect_call = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:client|gateway|proxy|adapter|connector|eventBus|publisher|producer|mq|kafka|rocketMq|restTemplate|webClient))\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
            combined,
            flags=re.IGNORECASE,
        )
        if persistence_call and side_effect_call:
            return [
                f"{str(persistence_call.group(1) or '').strip()}.{str(persistence_call.group(2) or '').strip()}",
                f"{str(side_effect_call.group(1) or '').strip()}.{str(side_effect_call.group(2) or '').strip()}",
            ]
        if persistence_call and ("publish(" in lowered or "send(" in lowered or "reserve(" in lowered):
            return [f"{str(persistence_call.group(1) or '').strip()}.{str(persistence_call.group(2) or '').strip()}", "outbound_side_effect"]
        return []

    def _detect_configuration_behavior_coupling(self, *, diff_excerpt: str, combined_context: str) -> list[str]:
        combined = "\n".join([diff_excerpt, combined_context])
        lowered = combined.lower()
        config_name_match = re.search(
            r"(?:@value\(\s*\"\$\{([^}]+)\}\"|feature[A-Za-z0-9_]*|toggle[A-Za-z0-9_]*|flag[A-Za-z0-9_]*|config[A-Za-z0-9_]*)",
            combined,
            flags=re.IGNORECASE,
        )
        if not config_name_match:
            return []
        business_effect = re.search(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:repository|repo|client|gateway|publisher|eventBus|service))\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
            combined,
            flags=re.IGNORECASE,
        )
        conditional = " if " in f" {lowered} " or "switch" in lowered or "?" in combined
        if business_effect and conditional:
            config_name = str(config_name_match.group(1) or config_name_match.group(0) or "").strip()
            return [config_name or "feature_flag", f"{str(business_effect.group(1) or '').strip()}.{str(business_effect.group(2) or '').strip()}"]
        return []

    def _detect_bulk_processing_risk(self, diff_excerpt: str, combined_context: str) -> list[str]:
        excerpt = self._normalize_java_context_snippet("\n".join([diff_excerpt, combined_context]))
        lowered = excerpt.lower()
        batch_shape = any(token in lowered for token in ("list<", "set<", "collection<", "page<", "items", "orders", "batch", "stream()", "foreach"))
        if not batch_shape:
            return []
        loop_and_call_terms = self._detect_loop_call_amplification(diff_excerpt, combined_context)
        if loop_and_call_terms:
            return ["batch_items", *loop_and_call_terms[:2]]
        batched_absent = not any(token in lowered for token in ("saveall(", "batch", "chunk", "partition", "bulkinsert", "batchupdate"))
        per_item_write = re.search(
            r"(?:foreach\s*\(|for\s*\([^)]*\)\s*\{|\.forEach\s*\().{0,600}\b([A-Za-z_][A-Za-z0-9_]*(?:repository|repo|dao|mapper|client|gateway|publisher))\s*\.\s*(save|insert|update|send|publish)",
            excerpt,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if batched_absent and per_item_write:
            return [str(per_item_write.group(1) or "").strip(), str(per_item_write.group(2) or "").strip()]
        return []

    def _detect_loop_call_amplification(self, diff_excerpt: str, combined_context: str) -> list[str]:
        excerpt = self._normalize_java_context_snippet("\n".join([diff_excerpt, combined_context]))
        if not excerpt.strip():
            return []
        added_only_excerpt = self._normalize_java_context_snippet(
            "\n".join(
                line[1:].strip()
                for line in str(diff_excerpt or "").splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
        )
        loop_pattern = re.compile(
            r"(for\s*\([^)]*\)\s*\{|while\s*\([^)]*\)\s*\{|do\s*\{|\bforEach\s*\(|\.forEach\s*\()",
            flags=re.IGNORECASE,
        )
        search_excerpt = added_only_excerpt if added_only_excerpt and loop_pattern.search(added_only_excerpt) else excerpt
        dependency_pattern = re.compile(
            r"\b("
            r"repository|repo|dao|mapper|client|gateway|service|manager|provider|publisher"
            r"|"
            r"[A-Za-z_][A-Za-z0-9_]*(?:repository|repo|dao|mapper|query|jdbcTemplate|sqlSession|entityManager)"
            r"|[A-Za-z_][A-Za-z0-9_]*(?:client|api|gateway|facade|proxy|feign|adapter|connector|remote)"
            r"|[A-Za-z_][A-Za-z0-9_]*(?:service|manager|provider)"
            r"|jdbcTemplate|restTemplate|webClient|sqlSession|entityManager|redisTemplate"
            r")\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)",
            flags=re.IGNORECASE,
        )
        method_ref_pattern = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*(?:service|manager|client|gateway|facade|proxy|adapter|connector|repository|repo|dao|mapper|provider))\s*::\s*([A-Za-z_][A-Za-z0-9_]*)",
            flags=re.IGNORECASE,
        )
        risky_call_verbs = {
            "get", "find", "load", "fetch", "query", "list", "select", "scan",
            "call", "invoke", "request", "send", "execute", "process", "handle",
            "save", "insert", "update", "delete", "deduct", "reserve", "publish",
            "pull", "push", "batchquery", "batchfetch", "calculate", "compute",
            "convert", "transform", "sync",
        }
        for loop_match in loop_pattern.finditer(search_excerpt):
            loop_token = loop_match.group(1).strip()
            # 在循环起点后的窗口中检索外部调用，覆盖 for(:)、stream().forEach 与 lambda block 的常见写法。
            window = search_excerpt[loop_match.start() : loop_match.start() + 900]
            call_terms: list[tuple[int, str]] = []
            for call_match in dependency_pattern.finditer(window):
                dependency_name = call_match.group(1).strip()
                method_name = call_match.group(2).strip()
                if method_name.lower() in risky_call_verbs or dependency_name.lower().endswith(
                    ("repository", "repo", "dao", "mapper", "client", "gateway", "facade", "proxy", "feign")
                ):
                    dependency_lower = dependency_name.lower()
                    method_lower = method_name.lower()
                    priority = 1
                    if dependency_lower.endswith(("repository", "repo", "dao", "mapper")) or dependency_lower in {"repository", "repo", "dao", "mapper"}:
                        priority = 3
                    if method_lower in {"save", "insert", "update", "delete"}:
                        priority += 2
                    call_terms.append((priority, f"{dependency_name}.{method_name}"))
            if call_terms:
                ordered_terms = [term for _, term in sorted(call_terms, key=lambda item: (-item[0], item[1].lower()))]
                return [loop_token, *self._dedupe(ordered_terms)[:3]]
            method_ref_match = method_ref_pattern.search(window)
            if method_ref_match:
                dependency_name = method_ref_match.group(1).strip()
                method_name = method_ref_match.group(2).strip()
                if method_name.lower() in risky_call_verbs:
                    return [loop_token, f"{dependency_name}::{method_name}"]
        return []

    def _detect_comment_contract_unimplemented(self, diff_excerpt: str, combined_context: str) -> list[str]:
        normalized_context = self._normalize_java_context_snippet("\n".join([diff_excerpt, combined_context]))
        added_lines = [
            line[1:].strip()
            for line in diff_excerpt.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        context_lines = [line.strip() for line in normalized_context.splitlines() if line.strip()]
        candidate_lines = added_lines + [line for line in context_lines if line not in added_lines]
        if not candidate_lines:
            return []
        comment_lines = [
            line
            for line in candidate_lines
            if line.startswith("//") or line.startswith("/*") or "todo" in line.lower()
        ]
        if not comment_lines:
            return []
        code_blob = "\n".join(line for line in context_lines if line not in comment_lines).lower()
        implementation_blob = self._implementation_only_blob(context_lines, comment_lines)
        contract_pairs = [
            (["审计事件", "审计日志", "audit event", "audit log", "audit"], ["audit", "auditlogger", "recordaudit", "appendaudit"]),
            (["操作日志", "行为日志", "operation log"], ["operationlog", "audit", "logger", "logservice"]),
            (["扣减库存", "库存", "deduct inventory", "reserve"], ["库存", "inventory", "reserve", "deduct"]),
            (["发送事件", "事件", "publish event", "domain event"], ["publish", "eventbus", "domain event", "outbox"]),
            (["发送通知", "notify", "通知"], ["notify", "message", "publish"]),
            (["缓存", "cache"], ["cache"]),
            (["调用接口", "调用下游", "调用远程", "远程接口", "remote", "invoke"], ["client", "api", "gateway", "facade", "proxy", "feign", "resttemplate", "webclient", "invoke", "call", "request", "send"]),
            (["重试", "retry"], ["retry", "backoff", "attempt"]),
            (["校验", "validate"], ["validate", "check", "assert"]),
        ]
        for comment in comment_lines:
            lowered_comment = comment.lower()
            for source_tokens, impl_tokens in contract_pairs:
                if any(token in comment or token in lowered_comment for token in source_tokens):
                    if not any(token in implementation_blob for token in impl_tokens):
                        return [comment[:48].strip()]
            if "todo" in lowered_comment:
                if self._todo_has_matching_implementation(lowered_comment, implementation_blob):
                    continue
                return [comment[:48].strip()]
        stub_terms = self._detect_stubbed_implementation(normalized_context)
        if stub_terms:
            return stub_terms
        return []

    def _collect_repository_context_snippets(self, repository_context: dict[str, Any]) -> list[str]:
        snippets: list[str] = []

        def append_snippet(value: object) -> None:
            if len(snippets) >= 12:
                return
            if not isinstance(value, dict):
                return
            for key in ("snippet", "excerpt", "content", "current_code"):
                text = str(value.get(key) or "").strip()
                if text:
                    snippets.append(text[:1600])
                    return

        for key in (
            "related_contexts",
            "related_source_snippets",
            "parent_contract_contexts",
            "caller_contexts",
            "callee_contexts",
            "domain_model_contexts",
            "persistence_contexts",
            "code_graph_related_contexts",
        ):
            for item in list(repository_context.get(key) or []):
                append_snippet(item)

        for symbol_context in list(repository_context.get("symbol_contexts") or []):
            if not isinstance(symbol_context, dict):
                continue
            append_snippet(symbol_context)
            for nested_key in ("definitions", "references"):
                for item in list(symbol_context.get(nested_key) or [])[:4]:
                    append_snippet(item)

        minimal_context = repository_context.get("code_graph_minimal_context")
        if isinstance(minimal_context, dict):
            for key in ("changed_nodes", "review_priority", "impacted_flows", "static_observations"):
                for item in list(minimal_context.get(key) or [])[:4]:
                    append_snippet(item)

        return self._dedupe(snippets)

    def _implementation_only_blob(self, context_lines: list[str], comment_lines: list[str]) -> str:
        implementation_lines: list[str] = []
        comment_set = {line.strip() for line in comment_lines}
        for raw_line in context_lines:
            line = raw_line.strip()
            if not line or line in comment_set:
                continue
            lowered = line.lower()
            if lowered.startswith(("//", "/*", "*")):
                continue
            if re.search(r"\binterface\s+[A-Za-z_][A-Za-z0-9_]*\b", line):
                continue
            if re.match(
                r"^(?:@\w+(?:\([^)]*\))?\s*)?(?:public|protected|private)?\s*(?:abstract\s+)?"
                r"(?:[\w.$<>\[\], ?]+\s+)+[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*;?$",
                line,
            ):
                continue
            implementation_lines.append(line)
        return "\n".join(implementation_lines).lower()

    def _todo_has_matching_implementation(self, lowered_comment: str, implementation_blob: str) -> bool:
        if not implementation_blob.strip():
            return False
        intent_to_impl_patterns = [
            (
                ("审计事件", "审计日志", "audit event", "audit log", "audit"),
                (r"\baudit\b", r"\bauditlogger\b", r"\.\s*(recordaudit|appendaudit|writeaudit)\s*\("),
            ),
            (
                ("操作日志", "行为日志", "operation log"),
                (r"\boperationlog\b", r"\baudit\b", r"\.\s*(info|record|append|write)\s*\("),
            ),
            (
                ("发送事件", "事件", "publish event", "domain event"),
                (r"\.\s*publish\s*\(", r"\beventbus\s*\.", r"\boutbox\b", r"domain\s*event", r"\beventpublisher\b"),
            ),
            (
                ("发送通知", "notify", "通知"),
                (r"\.\s*notify\s*\(", r"\.\s*send\s*\(", r"\bmessage\b", r"\bnotification\b", r"\bpublish\b"),
            ),
            (
                ("扣减库存", "库存", "deduct inventory", "reserve"),
                (r"\.\s*(deduct|reserve|lock|decrease|reduce)\s*\(", r"\binventory\b", r"库存"),
            ),
            (
                ("调用接口", "调用下游", "调用远程", "远程接口", "remote", "invoke"),
                (r"\.\s*(call|invoke|request|send|exchange|execute)\s*\(", r"\b(client|gateway|facade|proxy|feign|resttemplate|webclient)\b"),
            ),
            (
                ("重试", "retry"),
                (r"\bretry\b", r"\bbackoff\b", r"\battempt\b"),
            ),
            (
                ("校验", "validate"),
                (r"\.\s*(validate|check|assert)\s*\(", r"\bvalidator\b", r"\brequire\b"),
            ),
            (
                ("缓存", "cache"),
                (r"\bcache\b", r"\bredis\b", r"\.\s*(set|put|expire)\s*\("),
            ),
        ]
        for intent_tokens, implementation_patterns in intent_to_impl_patterns:
            if not any(token in lowered_comment for token in intent_tokens):
                continue
            return any(re.search(pattern, implementation_blob, flags=re.IGNORECASE) for pattern in implementation_patterns)
        return False

    def _detect_stubbed_implementation(self, normalized_context: str) -> list[str]:
        context = str(normalized_context or "")
        if not context.strip():
            return []
        placeholder_patterns = [
            r"throw\s+new\s+UnsupportedOperationException\s*\(",
            r"throw\s+new\s+NotImplementedException\s*\(",
            r"throw\s+new\s+IllegalStateException\s*\(\s*\"TODO",
            r"return\s+null\s*;",
            r"return\s+Collections\.emptyList\s*\(\s*\)\s*;",
            r"return\s+List\.of\s*\(\s*\)\s*;",
            r"return\s+Map\.of\s*\(\s*\)\s*;",
            r"return\s+false\s*;",
            r"return\s+0\s*;",
        ]
        for pattern in placeholder_patterns:
            match = re.search(pattern, context, flags=re.IGNORECASE)
            if match:
                return [match.group(0).strip()]
        empty_method_pattern = re.compile(
            r"((?://[^\n]*\n|/\*.*?\*/\s*)*)"
            r"(public|private|protected)\s+[A-Za-z0-9_<>\[\], ?]+\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*\{\s*\}",
            flags=re.DOTALL,
        )
        for match in empty_method_pattern.finditer(context):
            comments = str(match.group(1) or "").strip()
            if comments:
                first_comment_line = comments.splitlines()[0].strip()
                return [first_comment_line[:48]]
        comment_only_block_pattern = re.compile(
            r"(public|private|protected)\s+[A-Za-z0-9_<>\[\], ?]+\s+[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)\s*\{\s*(//[^\n]*|/\*.*?\*/)\s*\}",
            flags=re.DOTALL,
        )
        comment_block_match = comment_only_block_pattern.search(context)
        if comment_block_match:
            comment_text = re.sub(r"\s+", " ", comment_block_match.group(2) or "").strip()
            return [comment_text[:48]]
        return []

    def _normalize_java_context_snippet(self, content: str) -> str:
        normalized_lines: list[str] = []
        for raw_line in str(content or "").splitlines():
            line = str(raw_line or "").rstrip()
            if not line:
                normalized_lines.append("")
                continue
            line = re.sub(r"^\s*[+\-]\s*", "", line)
            line = re.sub(r"^\s*\d+\s*\|\s*", "", line)
            normalized_lines.append(line)
        return "\n".join(normalized_lines)

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            ordered.append(normalized)
        return ordered
