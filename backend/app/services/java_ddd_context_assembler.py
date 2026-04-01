from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.services.repository_context_service import RepositoryContextService


class JavaDddContextAssembler:
    """为 Java 项目补充固定结构的审查上下文，并在命中时增强 DDD 语义。"""

    CALLER_PATH_HINTS = ("controller", "application", "appservice", "listener", "job", "scheduler")
    CALLEE_PATH_HINTS = ("repository", "mapper", "domainservice", "service", "gateway")
    DOMAIN_PATH_HINTS = ("domain", "aggregate", "entity", "valueobject", "event")
    PERSISTENCE_SUFFIXES = (".xml", ".sql")
    DDD_PATH_HINTS = ("domain", "aggregate", "valueobject", "event", "application")
    DDD_TEXT_HINTS = ("Aggregate", "ValueObject", "DomainEvent", "ApplicationService", "DomainService")

    def build_context_pack(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        line_start: int,
        primary_context: dict[str, Any],
        related_files: list[str],
        symbol_contexts: list[dict[str, Any]],
        excerpt: str,
    ) -> dict[str, object]:
        current_class_context = self._build_current_class_context(
            service,
            file_path=file_path,
            line_start=line_start,
            primary_context=primary_context,
        )
        parent_contract_contexts = self._find_parent_contracts(
            service,
            file_path=file_path,
            current_class_context=current_class_context,
        )
        caller_contexts = self._find_callers(
            service,
            file_path=file_path,
            current_class_context=current_class_context,
            symbol_contexts=symbol_contexts,
        )
        callee_contexts = self._find_callees(
            service,
            file_path=file_path,
            current_class_context=current_class_context,
            primary_context=primary_context,
            excerpt=excerpt,
        )
        domain_model_contexts = self._find_domain_models(
            service,
            file_path=file_path,
            primary_context=primary_context,
            excerpt=excerpt,
        )
        transaction_context = self._find_transaction_context(
            file_path=file_path,
            current_class_context=current_class_context,
            caller_contexts=caller_contexts,
            callee_contexts=callee_contexts,
        )
        persistence_contexts = self._find_persistence_contexts(
            service,
            file_path=file_path,
            related_files=related_files,
            callee_contexts=callee_contexts,
            domain_model_contexts=domain_model_contexts,
        )
        java_context_signals = self._collect_java_context_signals(
            file_path=file_path,
            primary_context=primary_context,
            excerpt=excerpt,
            related_files=related_files,
            current_class_context=current_class_context,
            caller_contexts=caller_contexts,
            callee_contexts=callee_contexts,
            domain_model_contexts=domain_model_contexts,
            transaction_context=transaction_context,
            persistence_contexts=persistence_contexts,
        )
        java_review_mode = self._detect_java_review_mode(
            file_path=file_path,
            related_files=related_files,
            current_class_context=current_class_context,
            callee_contexts=callee_contexts,
            domain_model_contexts=domain_model_contexts,
            java_context_signals=java_context_signals,
        )
        return {
            "java_review_mode": java_review_mode,
            "java_context_signals": java_context_signals,
            "current_class_context": current_class_context,
            "parent_contract_contexts": parent_contract_contexts,
            "caller_contexts": caller_contexts,
            "callee_contexts": callee_contexts,
            "domain_model_contexts": domain_model_contexts,
            "transaction_context": transaction_context,
            "persistence_contexts": persistence_contexts,
        }

    def _build_current_class_context(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        line_start: int,
        primary_context: dict[str, Any],
    ) -> dict[str, Any]:
        text = self._read_file(service, file_path)
        if not text:
            snippet = str(primary_context.get("snippet") or "").strip()
            return {
                "path": file_path,
                "kind": "current_class",
                "class_name": self._extract_class_name(snippet),
                "method_name": self._extract_method_name(snippet),
                "line_start": line_start,
                "line_end": line_start,
                "snippet": snippet,
            }
        lines = text.splitlines()
        method_start, method_end = self._find_enclosing_block(lines, line_start)
        class_name = self._extract_class_name(text) or Path(file_path).stem
        method_snippet = service.load_file_range(file_path, method_start, method_end, padding=2)
        return {
            "path": file_path,
            "kind": "current_class",
            "class_name": class_name,
            "method_name": self._extract_method_name(method_snippet.get("snippet") or ""),
            "line_start": method_start,
            "line_end": method_end,
            "snippet": str(method_snippet.get("snippet") or "").strip(),
            "changed_methods": [self._extract_method_name(method_snippet.get("snippet") or "")] if self._extract_method_name(method_snippet.get("snippet") or "") else [],
            "changed_fields": self._extract_field_names(method_snippet.get("snippet") or ""),
        }

    def _find_parent_contracts(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        current_class_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        text = self._read_file(service, file_path)
        if not text:
            return []
        tokens = self._extract_parent_contract_tokens(text)
        results: list[dict[str, Any]] = []
        for token in tokens[:4]:
            context = service.search_symbol_context(token, globs=["*.java"], definition_limit=2, reference_limit=0)
            for definition in list(context.get("definitions") or [])[:2]:
                if not isinstance(definition, dict):
                    continue
                path = str(definition.get("path") or "").strip()
                if not path or path == file_path:
                    continue
                results.append(
                    {
                        "path": path,
                        "kind": "parent_contract",
                        "contract_type": "interface" if token.startswith("I") else "abstract_class",
                        "symbol": token,
                        "line_start": int(definition.get("line_number") or 1),
                        "line_end": int(definition.get("line_number") or 1),
                        "snippet": str(definition.get("snippet") or "").strip(),
                    }
                )
        return self._dedupe_contexts(results)

    def _find_callers(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        current_class_context: dict[str, Any],
        symbol_contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        method_name = str(current_class_context.get("method_name") or "").strip()
        symbols = [method_name] if method_name else []
        for item in symbol_contexts:
            symbol = str(item.get("symbol") or "").strip()
            if symbol and symbol not in symbols:
                symbols.append(symbol)
        results: list[dict[str, Any]] = []
        for symbol in symbols[:3]:
            refs = service.search_symbol_context(symbol, globs=["*.java"], definition_limit=0, reference_limit=8)
            for reference in list(refs.get("references") or [])[:8]:
                if not isinstance(reference, dict):
                    continue
                path = str(reference.get("path") or "").strip()
                if not path or path == file_path or not self._has_path_hint(path, self.CALLER_PATH_HINTS):
                    continue
                line_number = int(reference.get("line_number") or 1)
                snippet = service.load_file_range(path, line_number, line_number, padding=6)
                results.append(
                    {
                        "path": path,
                        "kind": "caller_context",
                        "caller_type": self._infer_caller_type(path),
                        "symbol": symbol,
                        "line_start": line_number,
                        "line_end": line_number,
                        "snippet": str(snippet.get("snippet") or reference.get("snippet") or "").strip(),
                    }
                )
        return self._dedupe_contexts(results)

    def _find_callees(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        current_class_context: dict[str, Any],
        primary_context: dict[str, Any],
        excerpt: str,
    ) -> list[dict[str, Any]]:
        file_text = self._read_file(service, file_path)
        snippet_text = "\n".join(
            [
                str(current_class_context.get("snippet") or "").strip(),
                str(primary_context.get("snippet") or "").strip(),
                str(excerpt or "").strip(),
            ]
        )
        candidates = self._extract_dependency_tokens(
            snippet_text,
            suffixes=("Repository", "Service", "Mapper", "Gateway"),
        )
        invocation_tokens = self._extract_callee_tokens_from_invocations(
            snippet_text,
            file_text=file_text,
        )
        for token in invocation_tokens:
            if token not in candidates:
                candidates.append(token)
        results: list[dict[str, Any]] = []
        for token in candidates[:6]:
            definitions = service.search_symbol_context(token, globs=["*.java", "*.xml"], definition_limit=3, reference_limit=0)
            for definition in list(definitions.get("definitions") or [])[:3]:
                if not isinstance(definition, dict):
                    continue
                path = str(definition.get("path") or "").strip()
                if not path or path == file_path:
                    continue
                line_number = int(definition.get("line_number") or 1)
                snippet = service.load_file_range(path, line_number, line_number, padding=8)
                results.append(
                    {
                        "path": path,
                        "kind": "callee_context",
                        "callee_type": self._infer_callee_type(path, token),
                        "symbol": token,
                        "line_start": line_number,
                        "line_end": int(snippet.get("line_end") or line_number),
                        "snippet": str(snippet.get("snippet") or definition.get("snippet") or "").strip(),
                    }
                )
        return self._dedupe_contexts(results)

    def _find_domain_models(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        primary_context: dict[str, Any],
        excerpt: str,
    ) -> list[dict[str, Any]]:
        candidates = self._extract_dependency_tokens(
            f"{primary_context.get('snippet') or ''}\n{excerpt}",
            suffixes=("Aggregate", "Entity", "ValueObject", "Event", "DomainEvent"),
        )
        results: list[dict[str, Any]] = []
        for token in candidates[:6]:
            definitions = service.search_symbol_context(token, globs=["*.java"], definition_limit=3, reference_limit=0)
            for definition in list(definitions.get("definitions") or [])[:3]:
                if not isinstance(definition, dict):
                    continue
                path = str(definition.get("path") or "").strip()
                if not path or path == file_path or not self._has_path_hint(path, self.DOMAIN_PATH_HINTS):
                    continue
                results.append(
                    {
                        "path": path,
                        "kind": "domain_model",
                        "domain_type": self._infer_domain_type(path, token),
                        "symbol": token,
                        "line_start": int(definition.get("line_number") or 1),
                        "line_end": int(definition.get("line_number") or 1),
                        "snippet": str(definition.get("snippet") or "").strip(),
                    }
                )
        return self._dedupe_contexts(results)

    def _find_transaction_context(
        self,
        *,
        file_path: str,
        current_class_context: dict[str, Any],
        caller_contexts: list[dict[str, Any]],
        callee_contexts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        snippet = str(current_class_context.get("snippet") or "").strip()
        chain_entries = [
            self._format_call_chain_entry(item)
            for item in [*caller_contexts[:2], current_class_context, *callee_contexts[:3]]
        ]
        cleaned_chain = [item for item in chain_entries if item]
        return {
            "kind": "transaction_context",
            "transactional_method": str(current_class_context.get("method_name") or "").strip(),
            "transactional_path": file_path,
            "transaction_boundary_snippet": snippet,
            "call_chain": cleaned_chain,
            "contains_remote_call": bool(re.search(r"(FeignClient|RestTemplate|WebClient|HttpClient|rpc|remote)", snippet)),
            "contains_message_publish": bool(re.search(r"(publish|send|Kafka|RocketMQ|Rabbit|eventBus)", snippet, re.IGNORECASE)),
            "contains_multi_repository_write": len({str(item.get("path") or "").strip() for item in callee_contexts if "repository" in str(item.get("callee_type") or "")}) > 1,
        }

    def _find_persistence_contexts(
        self,
        service: RepositoryContextService,
        *,
        file_path: str,
        related_files: list[str],
        callee_contexts: list[dict[str, Any]],
        domain_model_contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        candidate_paths: list[str] = []
        for path in [*related_files, *[str(item.get("path") or "") for item in callee_contexts], *[str(item.get("path") or "") for item in domain_model_contexts]]:
            normalized = str(path or "").strip()
            if normalized and normalized not in candidate_paths:
                candidate_paths.append(normalized)
        results: list[dict[str, Any]] = []
        for path in candidate_paths[:8]:
            lowered = path.lower()
            if path == file_path:
                continue
            if any(lowered.endswith(suffix) for suffix in self.PERSISTENCE_SUFFIXES):
                snippet = service.load_file_range(path, 1, 24, padding=0)
                results.append(
                    {
                        "path": path,
                        "kind": "persistence_context",
                        "persistence_type": "xml_sql" if lowered.endswith(".xml") else "query_sql",
                        "symbol": Path(path).stem,
                        "line_start": 1,
                        "line_end": 24,
                        "snippet": str(snippet.get("snippet") or "").strip(),
                    }
                )
                continue
            if any(token in lowered for token in ("entity", "repository", "mapper")):
                snippet = service.load_file_range(path, 1, 32, padding=0)
                results.append(
                    {
                        "path": path,
                        "kind": "persistence_context",
                        "persistence_type": self._infer_persistence_type(path),
                        "symbol": Path(path).stem,
                        "line_start": 1,
                        "line_end": 32,
                        "snippet": str(snippet.get("snippet") or "").strip(),
                    }
                )
        return self._dedupe_contexts(results)

    def _read_file(self, service: RepositoryContextService, file_path: str) -> str:
        if not service.is_ready():
            return ""
        target = (service.local_path / file_path).resolve()
        if not target.exists() or not target.is_file():
            return ""
        return target.read_text(encoding="utf-8", errors="ignore")

    def _find_enclosing_block(self, lines: list[str], line_start: int) -> tuple[int, int]:
        index = max(0, min(len(lines) - 1, line_start - 1))
        start = index
        method_pattern = re.compile(
            r"(public|protected|private|static|final|synchronized|default|abstract|\s)+[\w<>,\[\]? ]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{}]*\)\s*(throws [^{]+)?\{"
        )
        while start > 0 and not method_pattern.search(lines[start]):
            start -= 1
        brace_balance = 0
        end = start
        for pointer in range(start, len(lines)):
            brace_balance += lines[pointer].count("{")
            brace_balance -= lines[pointer].count("}")
            end = pointer
            if pointer > start and brace_balance <= 0:
                break
        return start + 1, end + 1

    def _extract_class_name(self, text: str) -> str:
        match = re.search(r"\b(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)", str(text or ""))
        return str(match.group(1) if match else "").strip()

    def _extract_method_name(self, text: str) -> str:
        match = re.search(
            r"\b(?:public|protected|private|static|final|synchronized|default|abstract|\s)+[\w<>,\[\]? ]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            str(text or ""),
        )
        return str(match.group(1) if match else "").strip()

    def _extract_field_names(self, text: str) -> list[str]:
        fields: list[str] = []
        for match in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Repository|Service|Mapper|Gateway))\b", str(text or "")):
            if match not in fields:
                fields.append(match)
        return fields[:6]

    def _extract_parent_contract_tokens(self, text: str) -> list[str]:
        tokens: list[str] = []
        extends_match = re.search(r"\bextends\s+([A-Za-z_][A-Za-z0-9_]*)", text)
        if extends_match:
            tokens.append(str(extends_match.group(1)).strip())
        implements_match = re.search(r"\bimplements\s+([A-Za-z0-9_, ]+)", text)
        if implements_match:
            for token in str(implements_match.group(1)).split(","):
                normalized = token.strip()
                if normalized and normalized not in tokens:
                    tokens.append(normalized)
        return tokens

    def _extract_dependency_tokens(self, text: str, *, suffixes: tuple[str, ...]) -> list[str]:
        tokens: list[str] = []
        for suffix in suffixes:
            for match in re.findall(rf"\b([A-Z][A-Za-z0-9_]*{suffix})\b", str(text or "")):
                normalized = str(match).strip()
                if normalized and normalized not in tokens:
                    tokens.append(normalized)
        return tokens

    def _extract_callee_tokens_from_invocations(self, text: str, *, file_text: str) -> list[str]:
        variable_types = self._extract_variable_type_map(file_text)
        tokens: list[str] = []
        for variable_name in re.findall(r"\b([a-z][A-Za-z0-9_]*)\s*\.", str(text or "")):
            normalized_name = str(variable_name).strip()
            if normalized_name in {"this", "super"}:
                continue
            inferred_type = variable_types.get(normalized_name)
            if not inferred_type:
                inferred_type = self._infer_type_from_variable_name(normalized_name)
            if inferred_type and inferred_type not in tokens:
                tokens.append(inferred_type)
        return tokens[:8]

    def _extract_variable_type_map(self, file_text: str) -> dict[str, str]:
        results: dict[str, str] = {}
        pattern = re.compile(
            r"\b(?:private|protected|public|final|static|\s)+([A-Z][A-Za-z0-9_<>]*)\s+([a-z][A-Za-z0-9_]*)\s*(?:=|;|,)"
        )
        for declared_type, variable_name in pattern.findall(str(file_text or "")):
            normalized_type = re.sub(r"<.*?>", "", str(declared_type).strip())
            normalized_name = str(variable_name).strip()
            if normalized_name and normalized_type and normalized_name not in results:
                results[normalized_name] = normalized_type
        return results

    def _infer_type_from_variable_name(self, variable_name: str) -> str:
        normalized = str(variable_name or "").strip()
        if not normalized:
            return ""
        for suffix in ("Repository", "Service", "Mapper", "Gateway"):
            lowered_suffix = suffix.lower()
            if normalized.lower().endswith(lowered_suffix):
                return normalized[0].upper() + normalized[1:]
        return ""

    def _has_path_hint(self, path: str, hints: tuple[str, ...]) -> bool:
        lowered = str(path or "").replace("\\", "/").lower()
        return any(hint in lowered for hint in hints)

    def _infer_caller_type(self, path: str) -> str:
        lowered = path.lower()
        if "controller" in lowered:
            return "controller"
        if "listener" in lowered:
            return "listener"
        if "job" in lowered or "scheduler" in lowered:
            return "scheduler"
        return "application_service"

    def _infer_callee_type(self, path: str, symbol: str) -> str:
        lowered = path.lower()
        if "repository" in lowered or symbol.endswith("Repository"):
            return "repository"
        if "mapper" in lowered or symbol.endswith("Mapper"):
            return "mapper"
        if "gateway" in lowered or symbol.endswith("Gateway"):
            return "gateway"
        return "domain_service"

    def _infer_domain_type(self, path: str, symbol: str) -> str:
        lowered = f"{path}::{symbol}".lower()
        if "valueobject" in lowered:
            return "value_object"
        if "event" in lowered:
            return "domain_event"
        if "aggregate" in lowered:
            return "aggregate"
        return "entity"

    def _infer_persistence_type(self, path: str) -> str:
        lowered = path.lower()
        if "entity" in lowered:
            return "jpa_entity"
        if "mapper" in lowered:
            return "mapper"
        return "repository"

    def _dedupe_contexts(self, contexts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in contexts:
            path = str(item.get("path") or "").strip()
            kind = str(item.get("kind") or "").strip()
            symbol = str(item.get("symbol") or "").strip()
            key = (path, kind, symbol)
            if not path or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:8]

    def _collect_java_context_signals(
        self,
        *,
        file_path: str,
        primary_context: dict[str, Any],
        excerpt: str,
        related_files: list[str],
        current_class_context: dict[str, Any],
        caller_contexts: list[dict[str, Any]],
        callee_contexts: list[dict[str, Any]],
        domain_model_contexts: list[dict[str, Any]],
        transaction_context: dict[str, Any],
        persistence_contexts: list[dict[str, Any]],
    ) -> list[str]:
        signals: list[str] = []
        blob_parts = [
            file_path,
            *(str(path).strip() for path in related_files if str(path).strip()),
            str(primary_context.get("snippet") or "").strip(),
            str(excerpt or "").strip(),
            str(current_class_context.get("snippet") or "").strip(),
        ]
        text_blob = "\n".join(part for part in blob_parts if part)
        lowered_blob = text_blob.lower()
        if "controller" in lowered_blob or any("controller" in str(item.get("caller_type") or "") for item in caller_contexts):
            signals.append("controller_entry")
        if "@transactional" in lowered_blob or str(transaction_context.get("transactional_method") or "").strip():
            signals.append("transaction_boundary")
        if any("repository" in str(item.get("callee_type") or "") for item in callee_contexts):
            signals.append("repository_dependency")
        if any("mapper" in str(item.get("callee_type") or "") for item in callee_contexts):
            signals.append("mapper_dependency")
        if persistence_contexts:
            signals.append("persistence_context")
        if any(".xml" in str(item.get("path") or "").lower() for item in persistence_contexts):
            signals.append("sql_or_mapper_context")
        if "applicationservice" in lowered_blob or "/application/" in lowered_blob:
            signals.append("application_service_layer")
        if any("domain_service" in str(item.get("callee_type") or "") for item in callee_contexts):
            signals.append("domain_service_dependency")
        if self._has_path_hint(file_path, self.DDD_PATH_HINTS) or any(self._has_path_hint(path, self.DDD_PATH_HINTS) for path in related_files):
            signals.append("ddd_package_layout")
        if domain_model_contexts:
            signals.append("domain_model_context")
        for item in domain_model_contexts:
            domain_type = str(item.get("domain_type") or "").strip()
            if domain_type:
                signal = f"domain_{domain_type}"
                if signal not in signals:
                    signals.append(signal)
        if any(token.lower() in lowered_blob for token in [hint.lower() for hint in self.DDD_TEXT_HINTS]):
            signals.append("ddd_symbol_hint")
        return signals

    def _detect_java_review_mode(
        self,
        *,
        file_path: str,
        related_files: list[str],
        current_class_context: dict[str, Any],
        callee_contexts: list[dict[str, Any]],
        domain_model_contexts: list[dict[str, Any]],
        java_context_signals: list[str],
    ) -> str:
        ddd_score = 0
        signal_set = {str(item).strip() for item in java_context_signals if str(item).strip()}
        if "ddd_package_layout" in signal_set:
            ddd_score += 2
        if "domain_model_context" in signal_set:
            ddd_score += 2
        if "ddd_symbol_hint" in signal_set:
            ddd_score += 1
        if "application_service_layer" in signal_set:
            ddd_score += 1
        if "domain_service_dependency" in signal_set:
            ddd_score += 1
        if any(signal.startswith("domain_") for signal in signal_set):
            ddd_score += 2
        snippet = str(current_class_context.get("snippet") or "").strip()
        if re.search(r"\b(order|aggregate|entity|event|valueobject)\b", snippet, re.IGNORECASE):
            ddd_score += 1
        if any("domain" in str(item.get("path") or "").lower() for item in domain_model_contexts):
            ddd_score += 1
        if any("domainservice" in str(item.get("path") or "").replace("\\", "/").lower() for item in callee_contexts):
            ddd_score += 1
        if self._has_path_hint(file_path, self.DDD_PATH_HINTS):
            ddd_score += 1
        if any(self._has_path_hint(path, self.DDD_PATH_HINTS) for path in related_files):
            ddd_score += 1
        return "ddd_enhanced" if ddd_score >= 3 else "general"

    def _format_call_chain_entry(self, item: dict[str, Any]) -> str:
        path = str(item.get("path") or "").strip()
        if not path:
            return ""
        role = (
            str(item.get("caller_type") or "").strip()
            or str(item.get("callee_type") or "").strip()
            or str(item.get("kind") or "").strip()
        )
        symbol = (
            str(item.get("method_name") or "").strip()
            or str(item.get("symbol") or "").strip()
            or str(item.get("class_name") or "").strip()
        )
        if role and symbol:
            return f"{role}:{path}::{symbol}"
        if role:
            return f"{role}:{path}"
        if symbol:
            return f"{path}::{symbol}"
        return path
