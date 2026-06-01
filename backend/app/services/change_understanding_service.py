from __future__ import annotations

import re
from collections.abc import Iterable

from app.services.diff_excerpt_service import DiffExcerptService


RISK_DOMAIN_EXPERTS: dict[str, tuple[str, ...]] = {
    "security": ("security_compliance",),
    "business": ("correctness_business",),
    "database": ("database_analysis",),
    "transaction": ("correctness_business", "database_analysis", "performance_reliability"),
    "performance": ("performance_reliability", "database_analysis"),
    "concurrency": ("performance_reliability", "test_verification"),
    "mq": ("mq_analysis", "test_verification"),
    "cache": ("redis_analysis", "test_verification"),
    "test": ("test_verification",),
    "maintainability": ("architecture_design", "maintainability_code_health"),
}


class ChangeUnderstandingService:
    """Build deterministic, compact facts about a review change set.

    This layer intentionally avoids LLM calls. It gives routing and expert
    prompts a stable map of files, roles, methods, symbols, and risk domains.
    """

    TEST_MARKERS = ("/test/", "/tests/", "test.java", "tests.java", "spec.", "__tests__")
    BUSINESS_TERMS = (
        "order",
        "payment",
        "refund",
        "inventory",
        "stock",
        "amount",
        "price",
        "status",
        "state",
        "account",
        "balance",
        "settlement",
        "订单",
        "支付",
        "退款",
        "库存",
        "金额",
        "状态",
    )

    def __init__(self, diff_service: DiffExcerptService | None = None) -> None:
        self._diff_service = diff_service or DiffExcerptService()

    def understand(self, *, changed_files: Iterable[str], unified_diff: str) -> dict[str, object]:
        files: list[dict[str, object]] = []
        all_domains: list[str] = []
        all_symbols: list[str] = []
        all_terms: list[str] = []
        for raw_path in changed_files:
            path = str(raw_path or "").strip()
            if not path:
                continue
            file_facts = self._understand_file(path, unified_diff)
            files.append(file_facts)
            all_domains.extend(str(item) for item in list(file_facts.get("risk_domains") or []))
            all_symbols.extend(str(item) for item in list(file_facts.get("changed_symbols") or []))
            all_terms.extend(str(item) for item in list(file_facts.get("business_terms") or []))

        risk_domains = self._dedupe(all_domains)
        expert_hints = self._experts_for_domains(risk_domains)
        return {
            "files": files,
            "changed_symbols": self._dedupe(all_symbols)[:40],
            "business_terms": self._dedupe(all_terms)[:20],
            "risk_domains": risk_domains,
            "expert_hints": expert_hints,
            "summary": self._build_summary(files, risk_domains),
        }

    def _understand_file(self, file_path: str, unified_diff: str) -> dict[str, object]:
        hunks = self._diff_service.list_hunks(unified_diff, file_path)
        file_diff = self._diff_service.extract_file_diff(unified_diff, file_path)
        hunk_text = "\n".join(str(item.get("excerpt") or "") for item in hunks)
        added_lines = self._added_lines(hunk_text or file_diff)
        removed_lines = self._removed_lines(hunk_text or file_diff)
        signal_text = "\n".join([file_path, file_diff, hunk_text, "\n".join(added_lines), "\n".join(removed_lines)])
        language = self._language_for_path(file_path)
        file_role = self._file_role(file_path, signal_text)
        is_test = self._is_test_file(file_path)
        changed_methods = self._changed_methods(hunks, added_lines)
        annotations = self._annotations(signal_text)
        symbols = self._changed_symbols(added_lines)
        business_terms = self._business_terms(signal_text)
        risk_domains = self._risk_domains(
            file_path=file_path,
            file_role=file_role,
            is_test=is_test,
            signal_text=signal_text,
            added_lines=added_lines,
            removed_lines=removed_lines,
        )
        return {
            "path": file_path,
            "language": language,
            "file_role": file_role,
            "is_test": is_test,
            "changed_methods": changed_methods,
            "annotations": annotations,
            "changed_symbols": symbols,
            "business_terms": business_terms,
            "risk_domains": risk_domains,
            "expert_hints": self._experts_for_domains(risk_domains),
            "hunk_count": len(hunks),
            "changed_line_count": sum(len(list(item.get("changed_lines") or [])) for item in hunks),
        }

    def _file_role(self, file_path: str, text: str) -> str:
        path_lower = str(file_path or "").lower()
        lower = f"{file_path}\n{text}".lower()
        if self._is_test_file(file_path):
            return "test"
        path_role_checks = (
            ("controller", ("controller.java", "/controller/", "/web/")),
            ("cache", ("cache", "redis")),
            ("mq", ("consumer.java", "producer.java", "listener.java", "kafka", "rocketmq", "rabbit")),
            ("service", ("service.java", "/service/", "/application/")),
            ("repository", ("repository.java", "mapper.java", "dao.java", "/repository/", "/mapper/", "/dao/")),
            ("entity", ("entity.java", "aggregate.java", "/entity/", "/domain/")),
            ("dto", ("dto.java", "request.java", "response.java", "vo.java", "bo.java", "/dto/")),
            ("config", ("config.java", "configuration.java", "/config/", "application.yml", ".properties")),
            ("sql", (".sql", "migration", "schema")),
            ("frontend", (".tsx", ".jsx", ".vue")),
        )
        for role, tokens in path_role_checks:
            if any(token in path_lower for token in tokens):
                return role
        role_checks = (
            ("controller", ("controller", "@restcontroller", "@controller", "@requestmapping")),
            ("repository", ("repository", "mapper", "dao", "@mapper", "@repository")),
            ("entity", ("entity", "@entity", "aggregate", "po.java", "do.java")),
            ("dto", ("dto", "request.java", "response.java", "vo.java", "bo.java")),
            ("config", ("config", "configuration", "@configuration", "application.yml", ".properties")),
            ("mq", ("consumer", "producer", "listener", "kafka", "rocketmq", "rabbit")),
            ("cache", ("redis", "cache", "caffeine")),
            ("service", ("service", "application", "@service")),
            ("frontend", (".tsx", ".jsx", ".vue")),
            ("sql", (".sql", "migration", "schema")),
        )
        for role, tokens in role_checks:
            if any(token in lower for token in tokens):
                return role
        return "code"

    def _language_for_path(self, file_path: str) -> str:
        suffix = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        return {
            "java": "java",
            "kt": "kotlin",
            "xml": "xml",
            "sql": "sql",
            "yml": "yaml",
            "yaml": "yaml",
            "properties": "properties",
            "ts": "typescript",
            "tsx": "typescript-react",
            "js": "javascript",
            "jsx": "javascript-react",
            "vue": "vue",
        }.get(suffix, suffix or "unknown")

    def _is_test_file(self, file_path: str) -> bool:
        normalized = file_path.replace("\\", "/").lower()
        return any(marker in normalized for marker in self.TEST_MARKERS)

    def _risk_domains(
        self,
        *,
        file_path: str,
        file_role: str,
        is_test: bool,
        signal_text: str,
        added_lines: list[str],
        removed_lines: list[str],
    ) -> list[str]:
        lower = signal_text.lower()
        added = "\n".join(added_lines).lower()
        removed = "\n".join(removed_lines).lower()
        domains: list[str] = []
        if is_test or file_role == "test":
            domains.append("test")
        if file_role == "controller" or any(
            token in lower
            for token in (
                "requestbody",
                "requestparam",
                "pathvariable",
                "principal",
                "userid",
                "user_id",
                "tenantid",
                "tenant_id",
                "permission",
                "preauthorize",
                "authorize",
                "token",
                "jwt",
                "password",
                "secret",
                "${",
                "sql injection",
                "鉴权",
                "授权",
                "权限",
            )
        ):
            domains.append("security")
        if any(term in lower for term in self.BUSINESS_TERMS):
            domains.append("business")
        if file_role in {"repository", "sql"} or any(
            token in lower
            for token in (
                "repository",
                "mapper",
                "dao",
                "entitymanager",
                "criteria",
                "@query",
                "select ",
                "insert ",
                "update ",
                "delete ",
                " where ",
                " limit ",
                "pageable",
                "setmaxresults",
                "mybatis",
                "jpa",
            )
        ):
            domains.append("database")
        if "@transactional" in lower or (
            any(token in lower for token in ("repository.save", "mapper.insert", "mapper.update", ".save("))
            and any(token in lower for token in ("publish", "send(", "event", "mq", "kafka", "http"))
        ):
            domains.append("transaction")
        if self._has_loop_external_call(added) or any(token in lower for token in ("batch", "bulk", "parallelstream")):
            domains.append("performance")
        if any(token in lower for token in ("synchronized", "lock(", "trylock", "setnx", "idempot", "幂等", "并发", "锁")):
            domains.append("concurrency")
        if file_role == "mq" or any(
            token in lower for token in ("kafka", "rocketmq", "rabbit", "producer", "consumer", "listener", "ack", "deadletter", "publish")
        ):
            domains.append("mq")
        if file_role == "cache" or any(token in lower for token in ("redis", "cache", "cacheable", "cacheevict", "ttl", "expire")):
            domains.append("cache")
        if any(token in added for token in ("todo", "fixme", "magic", "hardcode", "临时", "后续")) or any(
            token in removed for token in ("validate", "check", "assert", "permission", "limit", "lock")
        ):
            domains.append("maintainability")
        return self._dedupe(domains)

    def _experts_for_domains(self, risk_domains: Iterable[str]) -> list[str]:
        experts: list[str] = []
        for domain in risk_domains:
            experts.extend(RISK_DOMAIN_EXPERTS.get(str(domain), ()))
        return self._dedupe(experts)

    def _changed_methods(self, hunks: list[dict[str, object]], added_lines: list[str]) -> list[str]:
        candidates: list[str] = []
        for hunk in hunks:
            header = str(hunk.get("hunk_header") or "")
            trailing = header.rsplit("@@", 1)[-1].strip() if "@@" in header else ""
            candidates.extend(self._method_names_from_text(trailing))
        candidates.extend(self._method_names_from_text("\n".join(added_lines)))
        return self._dedupe(candidates)[:12]

    def _method_names_from_text(self, text: str) -> list[str]:
        names: list[str] = []
        pattern = re.compile(
            r"(?:public|protected|private|static|final|synchronized|\s)+[\w<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(",
            re.M,
        )
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in {"if", "for", "while", "switch", "catch", "return", "new"}:
                names.append(name)
        return names

    def _annotations(self, text: str) -> list[str]:
        return self._dedupe(match.group(1) for match in re.finditer(r"@([A-Za-z_][A-Za-z0-9_]*)", text))[:16]

    def _changed_symbols(self, added_lines: list[str]) -> list[str]:
        text = "\n".join(added_lines)
        symbols: list[str] = []
        for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*(?:Id|ID|No|Code|Status|State|Amount|Price|Count|Type|Token))\b", text):
            symbols.append(match.group(1))
        for match in re.finditer(r"\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
            name = match.group(1)
            if len(name) > 2 and name not in {"get", "set", "add"}:
                symbols.append(name)
        for name in ("userId", "tenantId", "orderId", "paymentId", "refundId", "skuId", "amount", "price", "status", "state"):
            if re.search(rf"\b{re.escape(name)}\b", text, re.I):
                symbols.append(name)
        return self._dedupe(symbols)[:30]

    def _business_terms(self, text: str) -> list[str]:
        lower = text.lower()
        return [term for term in self.BUSINESS_TERMS if term.lower() in lower][:20]

    def _added_lines(self, text: str) -> list[str]:
        return self._diff_lines(text, "+")

    def _removed_lines(self, text: str) -> list[str]:
        return self._diff_lines(text, "-")

    def _diff_lines(self, text: str, marker: str) -> list[str]:
        results: list[str] = []
        formatted_marker = f"| {marker}"
        raw_marker = marker
        for raw_line in str(text or "").splitlines():
            stripped = raw_line.lstrip()
            if stripped.startswith(("+++", "---")):
                continue
            if formatted_marker in raw_line:
                results.append(raw_line.split(formatted_marker, 1)[1].strip())
                continue
            if stripped.startswith(raw_marker) and not stripped.startswith((f"{raw_marker}{raw_marker}", f"{raw_marker}++")):
                results.append(stripped[1:].strip())
        return results

    def _has_loop_external_call(self, text: str) -> bool:
        for match in re.finditer(r"\b(for|while|foreach|forEach|stream\(\)|map)\b", text, re.I):
            window = text[match.start() : match.start() + 900]
            if re.search(r"\b\w*(repository|mapper|dao|client|gateway|service)\w*\s*\.|\b(http|send|publish)\b", window, re.I):
                return True
        return False

    def _build_summary(self, files: list[dict[str, object]], risk_domains: list[str]) -> str:
        if not files:
            return "未识别到可分析的变更文件。"
        role_counts: dict[str, int] = {}
        for item in files:
            role = str(item.get("file_role") or "code")
            role_counts[role] = role_counts.get(role, 0) + 1
        role_text = "、".join(f"{role} {count} 个" for role, count in sorted(role_counts.items()))
        domain_text = "、".join(risk_domains) if risk_domains else "未命中明确风险域"
        return f"本次变更包含 {len(files)} 个文件（{role_text}），识别风险域：{domain_text}。"

    def _dedupe(self, values: Iterable[str]) -> list[str]:
        deduped: list[str] = []
        for value in values:
            normalized = str(value or "").strip()
            if normalized and normalized not in deduped:
                deduped.append(normalized)
        return deduped
