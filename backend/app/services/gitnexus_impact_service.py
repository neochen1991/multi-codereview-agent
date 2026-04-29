from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.domain.models.report import (
    ImpactFile,
    ImpactGraph,
    ImpactGraphEdge,
    ImpactGraphNode,
    ImpactPath,
    ImpactReport,
    ImpactSymbol,
    TestScopeRecommendation,
)
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json
from app.services.mcp_stdio_client import McpStdioClient

logger = logging.getLogger(__name__)

NON_SYMBOL_TOKENS = {
    "public",
    "private",
    "protected",
    "static",
    "final",
    "abstract",
    "synchronized",
    "native",
    "volatile",
    "transient",
    "strictfp",
    "default",
    "void",
    "class",
    "interface",
    "enum",
    "record",
    "return",
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "new",
    "throw",
    "throws",
    "super",
    "this",
}


class GitNexusImpactClient(Protocol):
    """GitNexus 图谱查询客户端。"""

    def analyze_mr(
        self,
        *,
        repo_name: str,
        repo_path: str,
        subject: ReviewSubject,
        changed_symbols: list[ImpactSymbol],
        runtime_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """基于已建好的 GitNexus 图谱分析本次 MR 影响。"""


class GitNexusMcpImpactClient:
    """通过 GitNexus MCP stdio server 查询已建图谱。

    GitNexus 官方推荐的本地 MCP 启动命令是预装后的：

    `gitnexus mcp`

    这里通过通用 McpStdioClient 调用 `list_repos`、`detect_changes` 和 `impact`。
    """

    def __init__(self, timeout_seconds: int | None = None) -> None:
        self.timeout_seconds = max(5, int(timeout_seconds or os.getenv("GITNEXUS_MCP_TIMEOUT_SECONDS", "45") or 45))

    def analyze_mr(
        self,
        *,
        repo_name: str,
        repo_path: str,
        subject: ReviewSubject,
        changed_symbols: list[ImpactSymbol],
        runtime_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        command = self._command()
        logger.info(
            "gitnexus impact start repo=%s repo_path=%s changed_file_count=%s changed_symbol_count=%s command=%s",
            repo_name,
            repo_path,
            len(list(subject.changed_files or [])),
            len(changed_symbols),
            " ".join(command),
        )
        list_repos_payload = self._call_tool(command, repo_path, "list_repos", {}, runtime_env)
        available_repos = self._extract_repo_names(list_repos_payload)
        effective_repo_name = self._resolve_available_repo_name(repo_name, repo_path, list_repos_payload) or repo_name
        detect_changes_payload: dict[str, Any] = {}
        detect_changes_error = ""
        skipped_invalid_targets: list[str] = []
        skipped_missing_context_targets: list[str] = []
        skipped_missing_impact_targets: list[str] = []
        successful_context_targets: list[str] = []
        successful_impact_targets: list[str] = []
        try:
            detect_changes_payload = self._call_tool(
                command,
                repo_path,
                "detect_changes",
                {
                    "repo": effective_repo_name,
                    "scope": "all",
                },
                runtime_env,
            )
        except RuntimeError as error:
            detect_changes_error = str(error)
            logger.warning(
                "gitnexus detect_changes unavailable repo=%s repo_path=%s error=%s; continue with changed symbols + context/impact",
                effective_repo_name,
                repo_path,
                detect_changes_error,
            )
        targets, skipped_invalid_targets = self._build_targets(changed_symbols, detect_changes_payload)
        context_payloads: list[dict[str, Any]] = []
        impact_payloads: list[dict[str, Any]] = []
        if targets:
            context_payloads, skipped_missing_context_targets, successful_context_targets = self._query_contexts(
                command, effective_repo_name, repo_path, targets, runtime_env
            )
            impact_payloads, skipped_missing_impact_targets, successful_impact_targets = self._query_impacts(
                command, effective_repo_name, repo_path, targets, runtime_env
            )
        logger.info(
            "gitnexus impact finish repo=%s effective_repo=%s available_repo_count=%s detect_changes_keys=%s context_result_count=%s impact_result_count=%s queried_targets=%s",
            repo_name,
            effective_repo_name,
            len(available_repos),
            sorted(detect_changes_payload.keys()),
            len(context_payloads),
            len(impact_payloads),
            ",".join(targets[:8]),
        )
        if available_repos and effective_repo_name not in available_repos:
            raise RuntimeError(
                f"GitNexus MCP 未发现仓库 {effective_repo_name}，当前可用仓库: {', '.join(available_repos[:8])}"
            )
        return {
            "repo": effective_repo_name,
            "available_repos": available_repos,
            "detect_changes": detect_changes_payload,
            "context_results": context_payloads,
            "impact_results": impact_payloads,
            "queried_targets": targets,
            "raw_response_count": 2 + len(context_payloads) + len(impact_payloads),
            "detect_changes_error": detect_changes_error,
            "skipped_invalid_targets": skipped_invalid_targets,
            "skipped_missing_context_targets": skipped_missing_context_targets,
            "skipped_missing_impact_targets": skipped_missing_impact_targets,
            "successful_context_targets": successful_context_targets,
            "successful_impact_targets": successful_impact_targets,
        }

    def _command(self) -> list[str]:
        raw = str(os.getenv("GITNEXUS_MCP_COMMAND") or "").strip()
        if raw:
            return raw.split()
        return ["gitnexus", "mcp"]

    def _call_mcp(
        self,
        command: list[str],
        repo_path: str,
        requests: list[dict[str, Any]],
        runtime_env: dict[str, str] | None = None,
    ) -> dict[int, dict[str, Any]]:
        client = McpStdioClient(command, cwd=repo_path, timeout_seconds=self.timeout_seconds, env=runtime_env)
        try:
            return client.call_many(requests)
        except RuntimeError as error:
            raise RuntimeError(f"GitNexus MCP 调用失败: {error}") from error

    def _call_tool(
        self,
        command: list[str],
        repo_path: str,
        tool_name: str,
        arguments: dict[str, Any],
        runtime_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        logger.info("gitnexus mcp tool call tool=%s arguments=%s", tool_name, json.dumps(arguments, ensure_ascii=False))
        try:
            responses = self._call_mcp(
                command,
                repo_path,
                [
                    self._initialize_request(),
                    self._initialized_notification(),
                    {
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": tool_name,
                            "arguments": arguments,
                        },
                    },
                ],
                runtime_env,
            )
        except RuntimeError as error:
            logger.exception(
                "gitnexus mcp tool failed tool=%s repo_path=%s command=%s arguments=%s",
                tool_name,
                repo_path,
                " ".join(command),
                json.dumps(arguments, ensure_ascii=False),
            )
            raise RuntimeError(f"GitNexus {tool_name} 调用失败: {error}") from error
        response_message = responses.get(2) or {}
        if isinstance(response_message, dict) and isinstance(response_message.get("error"), dict):
            error_payload = dict(response_message.get("error") or {})
            logger.error(
                "gitnexus mcp tool returned error tool=%s repo_path=%s command=%s arguments=%s error=%s",
                tool_name,
                repo_path,
                " ".join(command),
                json.dumps(arguments, ensure_ascii=False),
                json.dumps(error_payload, ensure_ascii=False),
            )
            message = str(error_payload.get("message") or "unknown error")
            code = error_payload.get("code")
            data = error_payload.get("data")
            detail = f"code={code} message={message}"
            if data not in (None, "", {}):
                detail = f"{detail} data={json.dumps(data, ensure_ascii=False)}"
            raise RuntimeError(f"GitNexus {tool_name} 调用失败: {detail}")
        payload = self._tool_payload(response_message)
        if "error" in payload:
            logger.error(
                "gitnexus mcp tool payload error tool=%s repo_path=%s command=%s arguments=%s payload=%s",
                tool_name,
                repo_path,
                " ".join(command),
                json.dumps(arguments, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
            )
            raise RuntimeError(f"GitNexus {tool_name} 调用失败: {payload.get('error')}")
        logger.info("gitnexus mcp tool result tool=%s keys=%s", tool_name, sorted(payload.keys()))
        return payload

    def _query_contexts(
        self,
        command: list[str],
        repo_name: str,
        repo_path: str,
        targets: list[str],
        runtime_env: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        results: list[dict[str, Any]] = []
        skipped_missing_targets: list[str] = []
        successful_targets: list[str] = []
        for target in targets[:8]:
            logger.info("gitnexus context queue request repo=%s target=%s", repo_name, target)
            try:
                results.append(
                    self._call_tool(
                        command,
                        repo_path,
                        "context",
                        {
                            "repo": repo_name,
                            "name": target,
                        },
                        runtime_env,
                    )
                )
                successful_targets.append(target)
            except RuntimeError as error:
                if self._is_missing_symbol_error(error):
                    logger.warning(
                        "gitnexus context skipped missing symbol repo=%s target=%s error=%s",
                        repo_name,
                        target,
                        error,
                    )
                    skipped_missing_targets.append(target)
                    continue
                raise
        return results, skipped_missing_targets, successful_targets

    def _query_impacts(
        self,
        command: list[str],
        repo_name: str,
        repo_path: str,
        targets: list[str],
        runtime_env: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        results: list[dict[str, Any]] = []
        skipped_missing_targets: list[str] = []
        successful_targets: list[str] = []
        for target in targets[:8]:
            logger.info("gitnexus impact queue request repo=%s target=%s", repo_name, target)
            try:
                results.append(
                    self._call_tool(
                        command,
                        repo_path,
                        "impact",
                        {
                            "repo": repo_name,
                            "target": target,
                        },
                        runtime_env,
                    )
                )
                successful_targets.append(target)
            except RuntimeError as error:
                if self._is_missing_symbol_error(error):
                    logger.warning(
                        "gitnexus impact skipped missing symbol repo=%s target=%s error=%s",
                        repo_name,
                        target,
                        error,
                    )
                    skipped_missing_targets.append(target)
                    continue
                raise
        return results, skipped_missing_targets, successful_targets

    def _initialize_request(self) -> dict[str, Any]:
        return {
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "multi-codereview-agent", "version": "0.1.0"},
            },
        }

    def _initialized_notification(self) -> dict[str, Any]:
        return {"method": "notifications/initialized", "params": {}}

    def _build_targets(
        self,
        changed_symbols: list[ImpactSymbol],
        detect_changes_payload: dict[str, Any],
    ) -> tuple[list[str], list[str]]:
        candidates: list[str] = []
        for item in changed_symbols:
            symbol = str(item.symbol or "").strip()
            container = str(item.container or "").strip()
            if container and symbol and container != symbol:
                candidates.append(f"{container}.{symbol}")
            if symbol:
                candidates.append(symbol)
            if container:
                candidates.append(container)
        for key in ("changed_symbols", "changedSymbols", "symbols"):
            for item in list(detect_changes_payload.get(key) or []):
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
                elif isinstance(item, dict):
                    symbol = str(item.get("name") or item.get("symbol") or item.get("target") or "").strip()
                    if symbol:
                        candidates.append(symbol)
        deduped: list[str] = []
        skipped_invalid: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            normalized = item.strip()
            if not normalized or normalized in seen:
                continue
            if not self._is_valid_symbol_target(normalized):
                skipped_invalid.append(normalized)
                continue
            seen.add(normalized)
            deduped.append(normalized)
        return deduped[:12], self._dedupe_strings(skipped_invalid)

    def _is_missing_symbol_error(self, error: RuntimeError) -> bool:
        message = str(error or "").lower()
        return ("symbol " in message and " not found" in message) or (
            "target " in message and " not found" in message
        )

    def _is_valid_symbol_target(self, target: str) -> bool:
        normalized = str(target or "").strip()
        if not normalized:
            return False
        leaf = normalized.rsplit(".", 1)[-1].strip().lower()
        if leaf in NON_SYMBOL_TOKENS:
            return False
        return True

    def _dedupe_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _tool_payload(self, response: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(response, dict):
            return {}
        result = dict(response.get("result") or {})
        content = list(result.get("content") or [])
        for item in content:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            parsed = self._parse_json_text(text)
            if parsed is None:
                return {"text": text}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        return result

    def _parse_json_text(self, text: str) -> Any | None:
        candidates = [text]
        fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        candidates.extend(item.strip() for item in fenced if item.strip())
        object_candidate = self._extract_balanced_json(text, "{", "}")
        if object_candidate:
            candidates.append(object_candidate)
        array_candidate = self._extract_balanced_json(text, "[", "]")
        if array_candidate:
            candidates.append(array_candidate)
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
        return None

    def _extract_balanced_json(self, text: str, opener: str, closer: str) -> str:
        start = text.find(opener)
        if start < 0:
            return ""
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start : index + 1].strip()
        return ""

    def _extract_repo_names(self, payload: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        direct_name = str(payload.get("name") or payload.get("repo") or payload.get("repo_name") or "").strip()
        if direct_name:
            candidates.append(direct_name)
        for key in ("repos", "repositories", "items", "results", "value"):
            raw = payload.get(key)
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, str) and item.strip():
                    candidates.append(item.strip())
                elif isinstance(item, dict):
                    repo_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
                    if repo_name:
                        candidates.append(repo_name)
        text = str(payload.get("text") or "").strip()
        if text and not candidates:
            for line in text.splitlines():
                line = line.strip().lstrip("-").strip()
                if line:
                    candidates.append(line)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _resolve_available_repo_name(self, repo_name: str, repo_path: str, payload: dict[str, Any]) -> str:
        repo_path_resolved = str(Path(repo_path).resolve()) if repo_path else ""
        direct_name = str(payload.get("name") or payload.get("repo") or payload.get("repo_name") or "").strip()
        direct_path = str(payload.get("path") or payload.get("repoPath") or payload.get("repo_path") or "").strip()
        if direct_name:
            if repo_path_resolved and direct_path:
                try:
                    if str(Path(direct_path).resolve()) == repo_path_resolved:
                        return direct_name
                except OSError:
                    pass
            if direct_name == repo_name:
                return direct_name
        for key in ("repos", "repositories", "items", "results", "value"):
            raw = payload.get(key)
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                continue
            for item in raw:
                if not isinstance(item, dict):
                    continue
                candidate_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
                candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
                if repo_path_resolved and candidate_path:
                    try:
                        if str(Path(candidate_path).resolve()) == repo_path_resolved and candidate_name:
                            return candidate_name
                    except OSError:
                        pass
                if candidate_name == repo_name:
                    return candidate_name
        return repo_name


class GitNexusImpactService:
    """生成每个 MR 的关联影响报告。

    如果 subject metadata 已经写入 GitNexus 分析结果，直接标准化为 ImpactReport；
    否则要求本地 GitNexus 图谱和官方 MCP 能力可用，再按官方流程查询影响范围。
    """

    _SYMBOL_PATTERNS = [
        re.compile(
            r"^\+\s*(?:(?:public|private|protected|static|final|abstract|synchronized|native|default|strictfp)\s+)*"
            r"[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*(?:throws\s+[\w\s,<>.?]+)?\s*\{?"
        ),
        re.compile(r"^\+\s*(?:class|interface|enum|record)\s+(\w+)"),
        re.compile(r"^\+\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\("),
        re.compile(r"^\+\s*(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("),
        re.compile(r"^\+\s*def\s+(\w+)\s*\("),
        re.compile(r"^\+\s*class\s+(\w+)"),
    ]

    def __init__(self, storage_root: Path | None = None, mcp_client: GitNexusImpactClient | None = None) -> None:
        self.storage_root = Path(storage_root) if storage_root is not None else None
        self._mcp_client = mcp_client or GitNexusMcpImpactClient()

    def analyze(self, subject: ReviewSubject, runtime: RuntimeSettings | None = None) -> ImpactReport:
        """返回标准化影响报告；GitNexus 不可用时直接抛错，不再生成降级报告。"""
        report, _ = self.analyze_with_trace(subject, runtime)
        return report

    def analyze_with_trace(
        self,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None = None,
    ) -> tuple[ImpactReport, dict[str, Any]]:
        cached = self._cached_gitnexus_report(subject)
        if cached is not None:
            logger.info("gitnexus impact use cached report changed_file_count=%s", len(cached.changed_files))
            return cached, {
                "source": "cached",
                "workflow": [
                    "本次关联影响报告来自已缓存的 GitNexus 结果。",
                    "未重新执行 detect_changes/context/impact。",
                ],
            }
        return self._gitnexus_graph_report(subject, runtime)

    def _cached_gitnexus_report(self, subject: ReviewSubject) -> ImpactReport | None:
        metadata = dict(subject.metadata or {})
        raw = metadata.get("gitnexus_impact_report") or metadata.get("impact_report")
        if not isinstance(raw, dict):
            return None
        payload = dict(raw)
        payload.setdefault("changed_files", list(subject.changed_files))
        payload.setdefault("graph_status", "ready")
        return ImpactReport.model_validate(payload)

    def _gitnexus_graph_report(
        self,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None,
    ) -> tuple[ImpactReport, dict[str, Any]]:
        repo_path = self._repo_path(subject, runtime)
        if not repo_path:
            raise RuntimeError("未配置本地代码仓路径，无法执行 GitNexus 关联影响分析。")
        registry = self._load_gitnexus_registry(subject, runtime)
        graph_status = self._load_graph_status(repo_path, registry)
        logger.info(
            "gitnexus graph status repo_path=%s state=%s repo_name=%s",
            repo_path,
            graph_status.get("state"),
            graph_status.get("repo_name"),
        )
        if str(graph_status.get("state") or "") != "ready":
            raise RuntimeError("GitNexus 图谱未就绪，请先完成 gitnexus analyze 建图。")
        if shutil.which("gitnexus") is None:
            raise RuntimeError("当前机器未预装 GitNexus，可执行命令 `gitnexus` 不存在。")
        resolved_repo_name = self._resolve_gitnexus_repo_name(repo_path, graph_status, registry)
        if not resolved_repo_name:
            raise RuntimeError("GitNexus 图谱已存在，但未在官方 registry 中识别到该仓库。")
        duplicate_paths = self._registry_paths_for_repo_name(repo_path, resolved_repo_name, registry)
        if len(duplicate_paths) > 1:
            raise RuntimeError(
                "GitNexus registry 中存在重复仓库名 "
                f"{resolved_repo_name}，对应路径有：{', '.join(duplicate_paths[:6])}。"
                "请清理旧 registry 条目，或使用唯一仓目录名重新执行 gitnexus analyze。"
            )
        changed_symbols = self._build_changed_symbols(subject, runtime)
        logger.info(
            "gitnexus graph analyze repo=%s repo_path=%s changed_files=%s changed_symbols=%s symbols=%s",
            resolved_repo_name,
            repo_path,
            len(self._changed_files(subject)),
            len(changed_symbols),
            ",".join(item.symbol for item in changed_symbols[:10]),
        )
        try:
            raw = self._mcp_client.analyze_mr(
                repo_name=resolved_repo_name,
                repo_path=repo_path,
                subject=subject,
                changed_symbols=changed_symbols,
                runtime_env=self._gitnexus_runtime_env(subject, runtime),
            )
        except Exception as error:
            logger.exception(
                "gitnexus graph analyze failed repo=%s repo_path=%s changed_symbols=%s changed_files=%s",
                resolved_repo_name,
                repo_path,
                [f"{item.container + '.' if item.container else ''}{item.symbol}" for item in changed_symbols[:12]],
                self._changed_files(subject)[:20],
            )
            raise RuntimeError(f"GitNexus 图谱已就绪，但按官方 MCP 流程调用失败：{error}") from error
        report = self._normalize_gitnexus_payload(subject, runtime, graph_status, raw, changed_symbols)
        trace = {
            "source": "gitnexus_mcp",
            "workflow": [
                "list_repos",
                "detect_changes(scope=all)",
                "context(key symbols)",
                "impact(key symbols)",
            ],
            **raw,
        }
        return report, trace

    def _load_graph_status(self, repo_path: str, registry: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        repo_root = Path(repo_path)
        candidates: list[tuple[Path, bool]] = [
            (repo_root / ".gitnexus" / "index_status.json", True),
        ]
        if self.storage_root is not None:
            candidates.append((self.storage_root / "gitnexus" / "index_status.json", False))
        for path, strict_repo_match in candidates:
            if not path.exists():
                continue
            try:
                status = read_json(path)
            except Exception:
                continue
            if isinstance(status, dict):
                if strict_repo_match:
                    return status
                if self._status_matches_repo_path(status, repo_path):
                    return status
                logger.info(
                    "gitnexus graph status ignored because repo_path mismatched status_path=%s expected_repo=%s actual_repo=%s",
                    path,
                    repo_path,
                    status.get("repo_path"),
                )
                continue
        meta_path = repo_root / ".gitnexus" / "meta.json"
        if meta_path.exists():
            try:
                meta = read_json(meta_path)
            except Exception:
                meta = {}
            if isinstance(meta, dict):
                resolved_repo_name = self._resolve_repo_name_from_registry(repo_path, registry)
                return {
                    "state": "ready",
                    "repo_path": str(meta.get("repoPath") or repo_path),
                    "repo_name": resolved_repo_name or repo_root.name,
                    "updated_at": str(meta.get("indexedAt") or ""),
                    "commit": str(meta.get("lastCommit") or ""),
                    "stats": dict(meta.get("stats") or {}),
                }
        if (repo_root / ".gitnexus").exists():
            resolved_repo_name = self._resolve_repo_name_from_registry(repo_path, registry)
            return {
                "state": "ready",
                "repo_path": repo_path,
                "repo_name": resolved_repo_name or repo_root.name,
                "updated_at": "",
            }
        return {}

    def _status_matches_repo_path(self, status: dict[str, Any], repo_path: str) -> bool:
        candidate_path = str(status.get("repo_path") or status.get("repoPath") or status.get("path") or "").strip()
        if not candidate_path:
            return False
        try:
            return str(Path(candidate_path).resolve()) == str(Path(repo_path).resolve())
        except OSError:
            return False

    def _resolve_repo_name_from_registry(self, repo_path: str, registry: list[dict[str, Any]] | None = None) -> str:
        registry = registry if registry is not None else self._load_gitnexus_registry()
        if not registry:
            return ""
        repo_path_resolved = str(Path(repo_path).resolve())
        for item in registry:
            if not isinstance(item, dict):
                continue
            candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            candidate_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
            if candidate_path and candidate_name:
                try:
                    if str(Path(candidate_path).resolve()) == repo_path_resolved:
                        return candidate_name
                except OSError:
                    continue
        return ""

    def _resolve_gitnexus_repo_name(
        self,
        repo_path: str,
        graph_status: dict[str, Any],
        registry: list[dict[str, Any]] | None = None,
    ) -> str:
        status_name = str(graph_status.get("repo_name") or "").strip()
        if status_name:
            return status_name
        return self._resolve_repo_name_from_registry(repo_path, registry)

    def _registry_paths_for_repo_name(
        self,
        repo_path: str,
        repo_name: str,
        registry: list[dict[str, Any]] | None = None,
    ) -> list[str]:
        if not repo_name:
            return []
        repo_path_resolved = str(Path(repo_path).resolve())
        matched: list[str] = []
        for item in registry if registry is not None else self._load_gitnexus_registry():
            if not isinstance(item, dict):
                continue
            candidate_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
            if candidate_name != repo_name:
                continue
            candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            if not candidate_path:
                continue
            try:
                normalized = str(Path(candidate_path).resolve())
            except OSError:
                normalized = candidate_path
            if normalized == repo_path_resolved:
                return [normalized]
            matched.append(normalized)
        deduped: list[str] = []
        seen: set[str] = set()
        for item in matched:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _load_gitnexus_registry(
        self,
        subject: ReviewSubject | None = None,
        runtime: RuntimeSettings | None = None,
    ) -> list[dict[str, Any]]:
        registry_path = self._gitnexus_registry_path(subject, runtime)
        if not registry_path.exists():
            return []
        try:
            payload = read_json(registry_path)
        except Exception:
            return []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("repos", "repositories", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def _gitnexus_runtime_env(
        self,
        subject: ReviewSubject | None,
        runtime: RuntimeSettings | None,
    ) -> dict[str, str] | None:
        home_override = self._gitnexus_home(subject, runtime)
        if not home_override:
            return None
        env = dict(os.environ)
        env["HOME"] = home_override
        env.setdefault("USERPROFILE", home_override)
        env.setdefault("GITNEXUS_HOME", home_override)
        return env

    def _gitnexus_registry_path(
        self,
        subject: ReviewSubject | None,
        runtime: RuntimeSettings | None,
    ) -> Path:
        metadata = dict(subject.metadata or {}) if subject is not None else {}
        explicit = str(metadata.get("gitnexus_registry_path") or os.getenv("GITNEXUS_REGISTRY_PATH") or "").strip()
        if explicit:
            return Path(explicit).expanduser()
        home_override = self._gitnexus_home(subject, runtime)
        if home_override:
            return Path(home_override).expanduser() / ".gitnexus" / "registry.json"
        return Path.home() / ".gitnexus" / "registry.json"

    def _gitnexus_home(
        self,
        subject: ReviewSubject | None,
        runtime: RuntimeSettings | None,
    ) -> str:
        del runtime
        metadata = dict(subject.metadata or {}) if subject is not None else {}
        return str(metadata.get("gitnexus_home") or os.getenv("GITNEXUS_HOME") or "").strip()

    def _normalize_gitnexus_payload(
        self,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None,
        graph_status: dict[str, Any],
        raw: dict[str, Any],
        changed_symbols: list[ImpactSymbol],
    ) -> ImpactReport:
        fallback = self._fallback_report(subject, runtime)
        detect_changes = dict(raw.get("detect_changes") or {})
        impact_results = [item for item in list(raw.get("impact_results") or []) if isinstance(item, dict)]
        context_results = [item for item in list(raw.get("context_results") or []) if isinstance(item, dict)]
        queried_targets = self._dedupe([str(item).strip() for item in list(raw.get("queried_targets") or [])])
        successful_context_targets = self._dedupe(
            [str(item).strip() for item in list(raw.get("successful_context_targets") or [])]
        )
        successful_impact_targets = self._dedupe(
            [str(item).strip() for item in list(raw.get("successful_impact_targets") or [])]
        )
        skipped_invalid_targets = self._dedupe([str(item).strip() for item in list(raw.get("skipped_invalid_targets") or [])])
        skipped_missing_context_targets = self._dedupe(
            [str(item).strip() for item in list(raw.get("skipped_missing_context_targets") or [])]
        )
        skipped_missing_impact_targets = self._dedupe(
            [str(item).strip() for item in list(raw.get("skipped_missing_impact_targets") or [])]
        )
        impacted_files = self._merge_impact_files(
            fallback.impacted_files,
            self._impact_files_from_gitnexus(detect_changes, impact_results),
        )
        test_scopes = self._merge_test_scopes(
            fallback.recommended_test_scope,
            self._test_scopes_from_gitnexus(detect_changes, impact_results),
        )
        impact_paths = self._impact_paths_from_gitnexus(impact_results, context_results)
        impacted_modules = self._dedupe(
            [
                *fallback.impacted_modules,
                *[str(item) for item in list(detect_changes.get("affected_processes") or [])],
                *[str(item) for item in list(detect_changes.get("affectedProcesses") or [])],
            ]
        )
        impact_graph = self._build_impact_graph(
            changed_symbols or fallback.changed_symbols,
            impact_paths,
            context_results,
            impacted_files,
            test_scopes,
            impacted_modules,
        )
        risk_level = str(detect_changes.get("risk_level") or detect_changes.get("riskLevel") or fallback.risk_level)
        detect_changes_error = str(raw.get("detect_changes_error") or "").strip()
        logger.info(
            "gitnexus normalize report graph_status=ready impacted_files=%s impacted_modules=%s impact_paths=%s impact_graph_nodes=%s impact_graph_edges=%s test_scopes=%s",
            len(impacted_files),
            len(impacted_modules),
            len(impact_paths),
            len(impact_graph.nodes),
            len(impact_graph.edges),
            len(test_scopes),
        )
        return ImpactReport(
            graph_status="ready",
            graph_indexed_at=str(graph_status.get("indexed_at") or graph_status.get("updated_at") or ""),
            graph_commit=str(graph_status.get("commit") or ""),
            fact_source="gitnexus_mcp",
            analysis_workflow=[
                "list_repos",
                "detect_changes(scope=all)",
                "context(key symbols)",
                "impact(key symbols)",
            ],
            changed_files=self._dedupe(
                [
                    *fallback.changed_files,
                    *[str(item) for item in list(detect_changes.get("changed_files") or [])],
                ]
            ),
            changed_symbols=changed_symbols or fallback.changed_symbols,
            impacted_files=impacted_files,
            impacted_modules=impacted_modules,
            impact_paths=impact_paths,
            impact_graph=impact_graph,
            external_entrypoints=fallback.external_entrypoints,
            risk_level=risk_level if risk_level in {"low", "medium", "high", "critical"} else fallback.risk_level,
            recommended_test_scope=test_scopes,
            must_run_tests=fallback.must_run_tests,
            queried_targets=queried_targets,
            successful_context_targets=successful_context_targets,
            successful_impact_targets=successful_impact_targets,
            skipped_invalid_targets=skipped_invalid_targets,
            skipped_missing_context_targets=skipped_missing_context_targets,
            skipped_missing_impact_targets=skipped_missing_impact_targets,
            manual_verification=[
                *(
                    [f"以下符号在 GitNexus 图谱中未查到 context，已跳过：{', '.join(skipped_missing_context_targets[:8])}"]
                    if skipped_missing_context_targets
                    else []
                ),
                *(
                    [f"以下符号在 GitNexus 图谱中未查到 impact，已跳过：{', '.join(skipped_missing_impact_targets[:8])}"]
                    if skipped_missing_impact_targets
                    else []
                ),
                "本次关联影响分析已使用 GitNexus 图谱，请优先核对受影响流程和测试范围。",
                *fallback.manual_verification,
            ],
            limitations=[
                "GitNexus 图谱已用于本次 MR 关联影响分析。",
                "当前实现按官方 MCP 流程先查询 list_repos，再调用 detect_changes(scope=all) 和 impact。",
                *(
                    [f"以下疑似关键字/修饰符未作为图谱查询目标：{', '.join(skipped_invalid_targets[:8])}"]
                    if skipped_invalid_targets
                    else []
                ),
                *(
                    [f"detect_changes 未成功返回，本次主要基于 MR 变更符号继续查询 context/impact：{detect_changes_error}"]
                    if detect_changes_error
                    else []
                ),
            ],
        )

    def _fallback_report(self, subject: ReviewSubject, runtime: RuntimeSettings | None) -> ImpactReport:
        changed_files = self._changed_files(subject)
        changed_symbols = self._extract_changed_symbols(subject.unified_diff)
        impacted_files = self._build_impacted_files(changed_files)
        impacted_modules = sorted({self._module_name(path) for path in changed_files if self._module_name(path)})
        test_scopes = self._build_test_scope(changed_files, changed_symbols)
        must_run_tests = self._build_must_run_tests(changed_files)
        risk_level = self._risk_level(changed_files, changed_symbols)
        manual_verification = self._manual_verification(changed_files, risk_level)
        limitations = [
            "GitNexus 代码图谱暂不可用，当前报告基于 diff、文件路径和符号规则生成。",
            "跨仓调用链、运行时反射、动态 SQL 和配置驱动路由需要在 GitNexus 建图后进一步补强。",
        ]
        repo_path = self._repo_path(subject, runtime)
        if repo_path:
            limitations.append(f"已识别本地代码仓路径：{repo_path}，可作为 GitNexus 后台建图输入。")
        return ImpactReport(
            graph_status="fallback",
            graph_indexed_at=datetime.now(UTC).isoformat(),
            changed_files=changed_files,
            changed_symbols=changed_symbols,
            impacted_files=impacted_files,
            impacted_modules=impacted_modules,
            external_entrypoints=self._external_entrypoints(changed_files),
            risk_level=risk_level,
            recommended_test_scope=test_scopes,
            must_run_tests=must_run_tests,
            manual_verification=manual_verification,
            limitations=limitations,
        )

    def _changed_files(self, subject: ReviewSubject) -> list[str]:
        files = [str(path).strip() for path in list(subject.changed_files or []) if str(path).strip()]
        if files:
            return self._dedupe(files)
        parsed: list[str] = []
        for line in str(subject.unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                if len(parts) >= 4:
                    parsed.append(parts[3].removeprefix("b/"))
        return self._dedupe(parsed)

    def _build_changed_symbols(self, subject: ReviewSubject, runtime: RuntimeSettings | None) -> list[ImpactSymbol]:
        symbols = self._extract_changed_symbols(subject.unified_diff)
        repo_path = self._repo_path(subject, runtime)
        if symbols:
            return symbols
        if str(subject.unified_diff or "").strip():
            mention_symbols = self._extract_symbol_mentions(subject.unified_diff)
            if mention_symbols:
                logger.info(
                    "gitnexus symbol extraction used platform diff mention fallback changed_symbols=%s",
                    len(mention_symbols),
                )
                return mention_symbols
            logger.info(
                "gitnexus symbol extraction used platform diff only because unified_diff is present but produced no symbol declarations"
            )
            return []
        local_diff = self._load_local_diff_from_git(repo_path, subject) if repo_path else ""
        if local_diff:
            symbols = self._extract_changed_symbols(local_diff)
            if symbols:
                logger.info(
                    "gitnexus symbol extraction used local git diff repo_path=%s changed_symbols=%s",
                    repo_path,
                    len(symbols),
                )
                return self._enrich_changed_symbols_from_source(repo_path, subject, symbols)
        if repo_path:
            symbols = self._scan_changed_file_symbols(repo_path, subject)
            if symbols:
                logger.info(
                    "gitnexus symbol extraction used source scan repo_path=%s changed_symbols=%s",
                    repo_path,
                    len(symbols),
                )
                return symbols
        return []

    def _enrich_changed_symbols_from_source(
        self,
        repo_path: str,
        subject: ReviewSubject,
        symbols: list[ImpactSymbol],
    ) -> list[ImpactSymbol]:
        source_candidates = self._scan_changed_file_symbols(repo_path, subject)
        if not source_candidates:
            return symbols
        indexed = {(item.file_path, item.symbol): item for item in source_candidates}
        enriched: list[ImpactSymbol] = []
        for item in symbols:
            source_item = indexed.get((item.file_path, item.symbol))
            if source_item is None:
                enriched.append(item)
                continue
            enriched.append(
                item.model_copy(
                    update={
                        "kind": item.kind or source_item.kind,
                        "container": item.container or source_item.container,
                        "line_start": item.line_start or source_item.line_start,
                    }
                )
            )
        return enriched

    def _load_local_diff_from_git(self, repo_path: str, subject: ReviewSubject) -> str:
        repo_dir = Path(repo_path)
        if not repo_path or not repo_dir.exists() or not repo_dir.is_dir():
            return ""
        source_ref = self._resolve_existing_git_ref(repo_path, self._source_ref_candidates(subject))
        target_ref = self._resolve_existing_git_ref(repo_path, self._target_ref_candidates(subject))
        if not source_ref or not target_ref:
            logger.warning(
                "gitnexus local git diff skipped because refs are unavailable repo_path=%s source_ref=%s target_ref=%s source_candidates=%s target_candidates=%s",
                repo_path,
                source_ref,
                target_ref,
                self._source_ref_candidates(subject),
                self._target_ref_candidates(subject),
            )
            return ""
        command = ["git", "diff", "--unified=3", target_ref, source_ref]
        changed_files = self._changed_files(subject)
        if changed_files:
            command.extend(["--", *changed_files[:40]])
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except Exception as error:
            logger.warning("gitnexus local git diff failed repo_path=%s error=%s", repo_path, error)
            return ""
        if completed.returncode != 0:
            logger.warning(
                "gitnexus local git diff returned non-zero repo_path=%s return_code=%s stderr=%s",
                repo_path,
                completed.returncode,
                completed.stderr[-400:],
            )
            return ""
        return str(completed.stdout or "")

    def _extract_changed_symbols(self, unified_diff: str) -> list[ImpactSymbol]:
        symbols: list[ImpactSymbol] = []
        current_file = ""
        current_new_line = 0
        current_class = ""
        for line in str(unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[3].removeprefix("b/") if len(parts) >= 4 else ""
                current_new_line = 0
                current_class = ""
                continue
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                current_new_line = int(hunk.group(1))
                continue
            normalized_line = line[1:] if line[:1] in {"+", "-", " "} else line
            class_match = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)\b", normalized_line)
            if class_match and not line.startswith("-"):
                current_class = class_match.group(1)
            if line.startswith("+") and not line.startswith("+++"):
                for pattern in self._SYMBOL_PATTERNS:
                    match = pattern.match(line)
                    if match:
                        symbol_name = match.group(1)
                        kind = self._symbol_kind(line)
                        if not self._is_symbol_declaration_line(line, symbol_name, kind):
                            continue
                        symbols.append(
                            ImpactSymbol(
                                file_path=current_file,
                                symbol=symbol_name,
                                kind=kind,
                                container=current_class if kind == "function" and current_class and current_class != symbol_name else "",
                                line_start=current_new_line,
                            )
                        )
                        break
            if not line.startswith("-"):
                current_new_line += 1
        return symbols[:80]

    def _extract_symbol_mentions(self, unified_diff: str) -> list[ImpactSymbol]:
        symbols: list[ImpactSymbol] = []
        current_file = ""
        current_new_line = 0
        current_class = ""
        seen: set[tuple[str, str, str, int]] = set()
        for line in str(unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[3].removeprefix("b/") if len(parts) >= 4 else ""
                current_new_line = 0
                current_class = ""
                continue
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                current_new_line = int(hunk.group(1))
                continue
            normalized_line = line[1:] if line[:1] in {"+", "-", " "} else line
            class_match = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)\b", normalized_line)
            if class_match and not line.startswith("-"):
                current_class = class_match.group(1)
            if line.startswith("+") and not line.startswith("+++"):
                for symbol_name in self._extract_inline_symbol_candidates(normalized_line):
                    key = (current_file, symbol_name, current_class, current_new_line)
                    if key in seen:
                        continue
                    seen.add(key)
                    symbols.append(
                        ImpactSymbol(
                            file_path=current_file,
                            symbol=symbol_name,
                            kind="function",
                            container=current_class,
                            line_start=current_new_line,
                        )
                    )
            if not line.startswith("-"):
                current_new_line += 1
        return symbols[:80]

    def _extract_inline_symbol_candidates(self, line: str) -> list[str]:
        candidates: list[str] = []
        text = str(line or "").strip()
        if not text or text.startswith(("@", "//", "*")):
            return []
        for match in re.finditer(r"(?:\.|\b)([A-Za-z_]\w*)\s*\(", text):
            symbol_name = str(match.group(1) or "").strip()
            if not symbol_name:
                continue
            lowered = symbol_name.lower()
            if lowered in NON_SYMBOL_TOKENS:
                continue
            candidates.append(symbol_name)
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\b", text):
            symbol_name = str(match.group(1) or "").strip()
            if symbol_name and symbol_name.lower() not in NON_SYMBOL_TOKENS:
                candidates.append(symbol_name)
        return self._dedupe(candidates)

    def _scan_changed_file_symbols(self, repo_path: str, subject: ReviewSubject) -> list[ImpactSymbol]:
        symbols: list[ImpactSymbol] = []
        source_ref = self._resolve_existing_git_ref(repo_path, self._source_ref_candidates(subject))
        target_ref = self._resolve_existing_git_ref(repo_path, self._target_ref_candidates(subject))
        for path in self._changed_files(subject)[:24]:
            content = self._load_file_content(repo_path, source_ref, path)
            if not content:
                content = self._load_file_content(repo_path, target_ref, path)
            if not content:
                continue
            symbols.extend(self._extract_symbols_from_source(path, content))
        return symbols[:80]

    def _load_file_content(self, repo_path: str, ref: str, file_path: str) -> str:
        if not ref or not file_path:
            return ""
        command = ["git", "show", f"{ref}:{file_path}"]
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "")

    def _source_ref_candidates(self, subject: ReviewSubject) -> list[str]:
        metadata = dict(subject.metadata or {})
        candidates: list[str] = []
        candidates.extend(str(item or "").strip() for item in list(subject.commits or []))
        candidates.append(str(metadata.get("auto_queue_head_sha") or "").strip())
        candidates.append(str(metadata.get("head_sha") or "").strip())
        candidates.extend(self._ref_aliases(str(subject.source_ref or "").strip()))
        candidates.append("HEAD")
        return self._dedupe(candidates)

    def _target_ref_candidates(self, subject: ReviewSubject) -> list[str]:
        target_ref = str(subject.target_ref or "").strip()
        candidates: list[str] = []
        candidates.extend(self._ref_aliases(target_ref))
        if target_ref not in {"main", "master"}:
            candidates.extend(self._ref_aliases("main"))
            candidates.extend(self._ref_aliases("master"))
        return self._dedupe(candidates)

    def _ref_aliases(self, ref: str) -> list[str]:
        normalized = str(ref or "").strip()
        if not normalized:
            return []
        if normalized.startswith("refs/"):
            return [normalized]
        return [
            normalized,
            f"refs/heads/{normalized}",
            f"origin/{normalized}",
            f"refs/remotes/origin/{normalized}",
            f"upstream/{normalized}",
            f"refs/remotes/upstream/{normalized}",
        ]

    def _resolve_existing_git_ref(self, repo_path: str, candidates: list[str]) -> str:
        for candidate in candidates:
            ref = str(candidate or "").strip()
            if not ref:
                continue
            command = ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"]
            try:
                completed = subprocess.run(
                    command,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except Exception as error:
                logger.warning(
                    "gitnexus git ref verification failed repo_path=%s ref=%s error=%s",
                    repo_path,
                    ref,
                    error,
                )
                continue
            if completed.returncode == 0:
                return ref
        return ""

    def _extract_symbols_from_source(self, file_path: str, content: str) -> list[ImpactSymbol]:
        symbols: list[ImpactSymbol] = []
        current_class = ""
        for index, line in enumerate(str(content or "").splitlines(), start=1):
            class_match = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)\b", line)
            if class_match:
                current_class = class_match.group(1)
                symbols.append(
                    ImpactSymbol(
                        file_path=file_path,
                        symbol=current_class,
                        kind="class",
                        container="",
                        line_start=index,
                    )
                )
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith(("@", "//", "*")):
                continue
            for pattern in self._SYMBOL_PATTERNS:
                candidate = f"+{stripped}"
                match = pattern.match(candidate)
                if not match:
                    continue
                symbol_name = match.group(1)
                kind = self._symbol_kind(candidate)
                if not self._is_symbol_declaration_line(candidate, symbol_name, kind):
                    continue
                if kind != "function":
                    continue
                symbols.append(
                    ImpactSymbol(
                        file_path=file_path,
                        symbol=symbol_name,
                        kind=kind,
                        container=current_class if current_class and current_class != symbol_name else "",
                        line_start=index,
                    )
                )
                break
        deduped: list[ImpactSymbol] = []
        seen: set[tuple[str, str, str]] = set()
        for item in symbols:
            key = (item.file_path, item.container, item.symbol)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:12]

    def _build_impacted_files(self, changed_files: list[str]) -> list[ImpactFile]:
        impacted: list[ImpactFile] = []
        for path in changed_files:
            impacted.append(
                ImpactFile(
                    file_path=path,
                    relationship="changed",
                    reason="本次 MR 直接修改该文件，需要纳入影响范围。",
                    risk_level=self._file_risk(path),
                )
            )
            for test_path in self._candidate_test_paths(path):
                impacted.append(
                    ImpactFile(
                        file_path=test_path,
                        relationship="test_candidate",
                        reason="按源码路径推导出的候选测试文件，建议检查是否需要新增或更新。",
                        risk_level="medium",
                    )
                )
        return self._dedupe_impact_files(impacted)[:120]

    def _impact_files_from_gitnexus(
        self,
        detect_changes: dict[str, Any],
        impact_results: list[dict[str, Any]],
    ) -> list[ImpactFile]:
        files: list[ImpactFile] = []
        candidate_values: list[Any] = []
        for key in ("affected_files", "affectedFiles", "impacted_files", "impactedFiles"):
            candidate_values.extend(list(detect_changes.get(key) or []))
        for result in impact_results:
            for key in ("affected_files", "affectedFiles", "impacted_files", "impactedFiles", "files"):
                candidate_values.extend(list(result.get(key) or []))
            for path_item in list(result.get("impact_paths") or result.get("paths") or []):
                if isinstance(path_item, dict):
                    candidate_values.extend(list(path_item.get("files") or []))
        for item in candidate_values:
            if isinstance(item, str):
                file_path = item
                reason = "GitNexus 图谱识别出的受影响文件。"
                relationship = "gitnexus_impacted"
                risk_level = "medium"
            elif isinstance(item, dict):
                file_path = str(item.get("file_path") or item.get("path") or item.get("file") or "").strip()
                reason = str(item.get("reason") or item.get("description") or "GitNexus 图谱识别出的受影响文件。")
                relationship = str(item.get("relationship") or item.get("type") or "gitnexus_impacted")
                risk_level = str(item.get("risk_level") or item.get("riskLevel") or "medium")
            else:
                continue
            if not file_path:
                continue
            files.append(
                ImpactFile(
                    file_path=file_path,
                    relationship=relationship,
                    reason=reason,
                    risk_level=risk_level if risk_level in {"low", "medium", "high", "critical"} else "medium",
                )
            )
        return files

    def _merge_impact_files(self, base: list[ImpactFile], extra: list[ImpactFile]) -> list[ImpactFile]:
        return self._dedupe_impact_files([*extra, *base])[:160]

    def _test_scopes_from_gitnexus(
        self,
        detect_changes: dict[str, Any],
        impact_results: list[dict[str, Any]],
    ) -> list[TestScopeRecommendation]:
        scopes: list[TestScopeRecommendation] = []
        candidates: list[Any] = []
        for key in ("test_recommendations", "testRecommendations", "recommended_tests", "recommendedTests"):
            candidates.extend(list(detect_changes.get(key) or []))
        for result in impact_results:
            for key in ("test_recommendations", "testRecommendations", "recommended_tests", "recommendedTests", "tests"):
                candidates.extend(list(result.get(key) or []))
        for item in candidates:
            if isinstance(item, str):
                scopes.append(
                    TestScopeRecommendation(
                        scope=item,
                        reason="GitNexus 图谱根据受影响调用链推荐。",
                        paths=[],
                        priority="high",
                    )
                )
            elif isinstance(item, dict):
                scope = str(item.get("scope") or item.get("name") or item.get("title") or "").strip()
                if not scope:
                    continue
                scopes.append(
                    TestScopeRecommendation(
                        scope=scope,
                        reason=str(item.get("reason") or item.get("description") or "GitNexus 图谱根据受影响调用链推荐。"),
                        paths=[str(path) for path in list(item.get("paths") or item.get("files") or []) if str(path).strip()],
                        priority=str(item.get("priority") or "high"),
                    )
                )
        return scopes

    def _merge_test_scopes(
        self,
        base: list[TestScopeRecommendation],
        extra: list[TestScopeRecommendation],
    ) -> list[TestScopeRecommendation]:
        seen: set[str] = set()
        merged: list[TestScopeRecommendation] = []
        for item in [*extra, *base]:
            key = str(item.scope or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged[:80]

    def _impact_paths_from_gitnexus(
        self,
        impact_results: list[dict[str, Any]],
        context_results: list[dict[str, Any]] | None = None,
    ) -> list[ImpactPath]:
        paths: list[ImpactPath] = []
        for result in impact_results:
            target, target_file_path = self._normalize_graph_label(result.get("target"))
            for item in list(result.get("impact_paths") or result.get("impactPaths") or result.get("paths") or []):
                if isinstance(item, list):
                    values = [self._normalize_graph_label(value)[0] for value in item if self._normalize_graph_label(value)[0]]
                    paths.append(
                        ImpactPath(
                            source=values[0] if values else "",
                            target=values[-1] if values else "",
                            path=values,
                            depth=max(len(values) - 1, 0),
                            risk="medium",
                        )
                    )
                elif isinstance(item, dict):
                    values = [
                        self._normalize_graph_label(value)[0]
                        for value in list(item.get("path") or item.get("nodes") or [])
                        if self._normalize_graph_label(value)[0]
                    ]
                    source_value, _ = self._normalize_graph_label(item.get("source") or (values[0] if values else ""))
                    target_value, _ = self._normalize_graph_label(item.get("target") or (values[-1] if values else ""))
                    paths.append(
                        ImpactPath(
                            source=source_value,
                            target=target_value,
                            path=values,
                            depth=int(item.get("depth") or max(len(values) - 1, 0)),
                            risk=str(item.get("risk") or item.get("risk_level") or "medium"),
                        )
                    )
            risk = str(result.get("risk") or result.get("risk_level") or "medium")
            if target:
                for process_name in [str(value).strip() for value in list(result.get("affected_processes") or []) if str(value).strip()]:
                    paths.append(
                        ImpactPath(
                            source=target,
                            target=process_name,
                            path=[target, process_name],
                            depth=1,
                            risk=risk,
                        )
                    )
                for module_name in [str(value).strip() for value in list(result.get("affected_modules") or []) if str(value).strip()]:
                    paths.append(
                        ImpactPath(
                            source=target,
                            target=module_name,
                            path=[target, module_name],
                            depth=1,
                            risk=risk,
                        )
                    )
                by_depth = result.get("byDepth")
                if isinstance(by_depth, dict):
                    for depth_key, raw_values in by_depth.items():
                        depth = int(depth_key) if str(depth_key).isdigit() else 1
                        if isinstance(raw_values, list):
                            for raw_item in raw_values:
                                label = ""
                                if isinstance(raw_item, str):
                                    label, _ = self._normalize_graph_label(raw_item)
                                elif isinstance(raw_item, dict):
                                    label, _ = self._normalize_graph_label(
                                        raw_item.get("name")
                                        or raw_item.get("symbol")
                                        or raw_item.get("target")
                                        or raw_item.get("module")
                                        or raw_item.get("process")
                                        or raw_item
                                    )
                                if not label:
                                    continue
                                paths.append(
                                    ImpactPath(
                                        source=target,
                                        target=label,
                                        path=[target, label],
                                        depth=depth,
                                        risk=risk,
                                    )
                                )
        for context in list(context_results or []):
            symbol_name = self._context_symbol_name(context)
            if not symbol_name:
                continue
            for outgoing in self._context_links(context, "outgoing"):
                paths.append(
                    ImpactPath(
                        source=symbol_name,
                        target=outgoing["label"],
                        path=[symbol_name, outgoing["label"]],
                        depth=1,
                        risk="medium",
                    )
                )
            for incoming in self._context_links(context, "incoming"):
                paths.append(
                    ImpactPath(
                        source=incoming["label"],
                        target=symbol_name,
                        path=[incoming["label"], symbol_name],
                        depth=1,
                        risk="medium",
                    )
                )
        deduped: list[ImpactPath] = []
        seen: set[tuple[str, ...]] = set()
        for item in paths:
            values = tuple(item.path or [item.source, item.target])
            if not any(values) or values in seen:
                continue
            seen.add(values)
            deduped.append(item)
        return deduped[:80]

    def _build_impact_graph(
        self,
        changed_symbols: list[ImpactSymbol],
        impact_paths: list[ImpactPath],
        context_results: list[dict[str, Any]],
        impacted_files: list[ImpactFile],
        test_scopes: list[TestScopeRecommendation],
        impacted_modules: list[str],
    ) -> ImpactGraph:
        nodes: dict[str, ImpactGraphNode] = {}
        edges: dict[tuple[str, str, str], ImpactGraphEdge] = {}

        def ensure_node(
            node_id: str,
            *,
            label: str | None = None,
            kind: str = "",
            file_path: str = "",
            role: str = "",
            risk: str = "",
        ) -> None:
            normalized = str(node_id or "").strip()
            if not normalized:
                return
            existing = nodes.get(normalized)
            if existing is None:
                nodes[normalized] = ImpactGraphNode(
                    node_id=normalized,
                    label=str(label or normalized),
                    kind=kind or "symbol",
                    file_path=file_path,
                    role=role,
                    risk=risk,
                )
                return
            if role and not existing.role:
                existing.role = role
            if kind and not existing.kind:
                existing.kind = kind
            if file_path and not existing.file_path:
                existing.file_path = file_path
            if risk and not existing.risk:
                existing.risk = risk

        def ensure_edge(source: str, target: str, relationship: str, confidence: float = 1.0) -> None:
            source_id = str(source or "").strip()
            target_id = str(target or "").strip()
            if not source_id or not target_id or source_id == target_id:
                return
            key = (source_id, target_id, relationship)
            if key not in edges:
                edges[key] = ImpactGraphEdge(
                    source=source_id,
                    target=target_id,
                    relationship=relationship,
                    confidence=confidence,
                )

        for symbol in changed_symbols:
            node_id = self._graph_symbol_id(symbol)
            ensure_node(
                node_id,
                label=node_id,
                kind=symbol.kind or "symbol",
                file_path=symbol.file_path,
                role="changed",
                risk="high",
            )
        for path in impact_paths:
            values = path.path or [path.source, path.target]
            last_value = ""
            for index, value in enumerate(values):
                ensure_node(
                    value,
                    label=value,
                    kind="symbol",
                    role="path" if index not in {0, len(values) - 1} else ("start" if index == 0 else "impacted"),
                    risk=path.risk,
                )
                if last_value:
                    ensure_edge(last_value, value, "calls", 1.0)
                last_value = value
        for context in context_results:
            symbol_name = self._context_symbol_name(context)
            if not symbol_name:
                continue
            ensure_node(symbol_name, label=symbol_name, kind="symbol", role="context")
            for link in self._context_links(context, "incoming"):
                ensure_node(
                    link["label"],
                    label=link["label"],
                    kind=link["kind"],
                    file_path=link.get("file_path", ""),
                    role="incoming",
                )
                ensure_edge(link["label"], symbol_name, link["relationship"], 0.8)
            for link in self._context_links(context, "outgoing"):
                ensure_node(
                    link["label"],
                    label=link["label"],
                    kind=link["kind"],
                    file_path=link.get("file_path", ""),
                    role="outgoing",
                )
                ensure_edge(symbol_name, link["label"], link["relationship"], 0.8)
            for process_name in self._context_processes(context):
                ensure_node(process_name, label=process_name, kind="process", role="process")
                ensure_edge(process_name, symbol_name, "process_step", 0.6)
        anchor_targets = [item.path[-1] for item in impact_paths if item.path] or [item.target for item in impact_paths if item.target]
        changed_anchor = self._graph_symbol_id(changed_symbols[0]) if changed_symbols else ""
        changed_anchor_by_file = {
            item.file_path: self._graph_symbol_id(item)
            for item in changed_symbols
            if item.file_path and self._graph_symbol_id(item)
        }
        terminal_anchor = anchor_targets[0] if anchor_targets else changed_anchor
        for file_item in impacted_files[:18]:
            node_id = f"file::{file_item.file_path}"
            ensure_node(
                node_id,
                label=Path(file_item.file_path).name or file_item.file_path,
                kind="file",
                file_path=file_item.file_path,
                role="file",
                risk=file_item.risk_level,
            )
            anchor = terminal_anchor
            if file_item.relationship == "changed":
                anchor = changed_anchor_by_file.get(file_item.file_path, changed_anchor)
            elif file_item.relationship == "test_candidate":
                anchor = self._anchor_for_test_candidate(file_item.file_path, changed_anchor_by_file) or terminal_anchor
            if anchor:
                ensure_edge(anchor, node_id, file_item.relationship or "impacts_file", 0.85)
        for module_name in impacted_modules[:8]:
            node_id = f"module::{module_name}"
            ensure_node(node_id, label=module_name, kind="module", role="module", risk="medium")
            if terminal_anchor:
                ensure_edge(terminal_anchor, node_id, "impacts_module", 0.7)
        for scope in test_scopes[:8]:
            node_id = f"test::{scope.scope}"
            ensure_node(node_id, label=scope.scope, kind="test_scope", role="test", risk=scope.priority)
            if terminal_anchor:
                ensure_edge(terminal_anchor, node_id, "recommended_test", 0.9)
            for path_item in scope.paths[:6]:
                file_node_id = f"file::{path_item}"
                ensure_node(
                    file_node_id,
                    label=Path(path_item).name or path_item,
                    kind="file",
                    file_path=path_item,
                    role="file",
                    risk="medium",
                )
                ensure_edge(node_id, file_node_id, "covers_file", 0.7)
        return ImpactGraph(nodes=list(nodes.values())[:120], edges=list(edges.values())[:180])

    def _anchor_for_test_candidate(self, file_path: str, changed_anchor_by_file: dict[str, str]) -> str:
        candidate_name = Path(file_path).name.lower()
        for changed_file_path, anchor in changed_anchor_by_file.items():
            changed_name = Path(changed_file_path).stem.lower()
            if changed_name and changed_name in candidate_name:
                return anchor
        return ""

    def _build_test_scope(
        self,
        changed_files: list[str],
        changed_symbols: list[ImpactSymbol],
    ) -> list[TestScopeRecommendation]:
        scopes: list[TestScopeRecommendation] = []
        source_files = [path for path in changed_files if not self._is_test_file(path)]
        test_files = [path for path in changed_files if self._is_test_file(path)]
        if source_files:
            scopes.append(
                TestScopeRecommendation(
                    scope="变更文件对应的单元测试",
                    reason="源码文件发生变化，应优先覆盖新增/修改方法以及异常分支。",
                    paths=self._dedupe([candidate for path in source_files for candidate in self._candidate_test_paths(path)])[:20],
                    priority="high",
                )
            )
        if any(self._is_controller_or_api(path) for path in changed_files):
            scopes.append(
                TestScopeRecommendation(
                    scope="接口级回归测试",
                    reason="入口层或 API 路由发生变化，需要验证请求参数、鉴权、错误码和兼容性。",
                    paths=[path for path in changed_files if self._is_controller_or_api(path)][:20],
                    priority="high",
                )
            )
        if any(self._is_repository_or_sql(path) for path in changed_files):
            scopes.append(
                TestScopeRecommendation(
                    scope="数据访问与事务回归",
                    reason="数据访问层或 SQL 相关文件变化，需要验证查询语义、事务边界和数据量边界。",
                    paths=[path for path in changed_files if self._is_repository_or_sql(path)][:20],
                    priority="high",
                )
            )
        if test_files:
            scopes.append(
                TestScopeRecommendation(
                    scope="已有测试用例回归",
                    reason="测试文件本身发生变化，需要确认测试仍能稳定复现业务风险。",
                    paths=test_files[:20],
                    priority="medium",
                )
            )
        if changed_symbols and not scopes:
            scopes.append(
                TestScopeRecommendation(
                    scope="变更符号的最小回归",
                    reason="检测到新增/修改函数或类，建议围绕这些符号补充最小可复现测试。",
                    paths=[item.file_path for item in changed_symbols if item.file_path][:20],
                    priority="medium",
                )
            )
        return scopes

    def _build_must_run_tests(self, changed_files: list[str]) -> list[str]:
        commands: list[str] = []
        if any(path.endswith(".java") for path in changed_files):
            commands.extend(["mvn test 或 ./gradlew test", "相关模块的集成测试"])
        if any(path.endswith((".ts", ".tsx", ".js", ".jsx")) for path in changed_files):
            commands.extend(["npm test", "npm run lint"])
        if any(path.endswith(".py") for path in changed_files):
            commands.extend(["pytest"])
        if any(self._is_controller_or_api(path) for path in changed_files):
            commands.append("接口契约/API 回归用例")
        if any(self._is_repository_or_sql(path) for path in changed_files):
            commands.append("数据库查询/事务相关回归用例")
        return self._dedupe(commands)

    def _manual_verification(self, changed_files: list[str], risk_level: str) -> list[str]:
        items: list[str] = []
        if risk_level in {"high", "critical"}:
            items.append("请人工确认本次变更是否影响核心链路、灰度开关和回滚方案。")
        if any(self._is_controller_or_api(path) for path in changed_files):
            items.append("请人工抽查入口参数、鉴权、异常响应和兼容性。")
        if any(self._is_repository_or_sql(path) for path in changed_files):
            items.append("请人工确认 SQL 条件、索引命中、分页/批量上限和事务边界。")
        if not items:
            items.append("请结合业务场景确认候选测试范围是否覆盖本次 MR 的真实使用路径。")
        return items

    def _risk_level(self, changed_files: list[str], changed_symbols: list[ImpactSymbol]) -> str:
        score = 0
        score += min(len(changed_files), 10)
        score += min(len(changed_symbols) // 3, 6)
        if any(self._is_controller_or_api(path) for path in changed_files):
            score += 3
        if any(self._is_repository_or_sql(path) for path in changed_files):
            score += 3
        if any("migration" in path.lower() or path.endswith(".sql") for path in changed_files):
            score += 4
        if score >= 12:
            return "high"
        if score >= 4:
            return "medium"
        return "low"

    def _external_entrypoints(self, changed_files: list[str]) -> list[str]:
        return [
            path
            for path in changed_files
            if self._is_controller_or_api(path)
            or any(token in path.lower() for token in ("consumer", "listener", "job", "scheduler", "handler"))
        ][:40]

    def _candidate_test_paths(self, path: str) -> list[str]:
        normalized = path.replace("\\", "/")
        if self._is_test_file(normalized):
            return [normalized]
        candidates: list[str] = []
        if "/src/main/java/" in normalized:
            base = normalized.replace("/src/main/java/", "/src/test/java/")
            candidates.append(re.sub(r"\.java$", "Test.java", base))
        elif normalized.startswith("src/main/java/"):
            base = normalized.replace("src/main/java/", "src/test/java/", 1)
            candidates.append(re.sub(r"\.java$", "Test.java", base))
        elif normalized.endswith(".java"):
            candidates.append(re.sub(r"\.java$", "Test.java", normalized.replace("/main/", "/test/")))
        elif normalized.endswith((".ts", ".tsx", ".js", ".jsx")):
            stem = re.sub(r"\.(ts|tsx|js|jsx)$", "", normalized)
            candidates.extend([f"{stem}.test.ts", f"{stem}.spec.ts"])
        elif normalized.endswith(".py"):
            name = Path(normalized).name
            candidates.append(str(Path(normalized).with_name(f"test_{name}")).replace("\\", "/"))
        return self._dedupe([item for item in candidates if item and item != path])

    def _module_name(self, path: str) -> str:
        normalized = path.replace("\\", "/").strip("/")
        parts = [part for part in normalized.split("/") if part]
        if not parts:
            return ""
        if "src" in parts:
            index = parts.index("src")
            return "/".join(parts[:index]) or parts[0]
        return parts[0]

    def _symbol_kind(self, line: str) -> str:
        lowered = line.lower()
        if " class " in lowered or lowered.lstrip("+ ").startswith("class "):
            return "class"
        if " interface " in lowered:
            return "interface"
        if " enum " in lowered:
            return "enum"
        return "function"

    def _is_symbol_declaration_line(self, line: str, symbol_name: str, kind: str) -> bool:
        normalized = str(line or "").strip()
        if kind in {"class", "interface", "enum"}:
            return True
        if kind != "function":
            return True
        if str(symbol_name or "").strip().lower() in NON_SYMBOL_TOKENS:
            return False
        if "=" in normalized and normalized.find("=") < normalized.find("("):
            return False
        if normalized.endswith(";") and "->" not in normalized:
            return False
        head = normalized.lstrip("+").strip()
        prefix = head.split("(", 1)[0].strip()
        tokens = [token for token in prefix.split() if token]
        if any(token in {"public", "private", "protected", "static", "final", "synchronized", "abstract"} for token in tokens):
            return True
        return len(tokens) >= 2 and tokens[-1] == symbol_name

    def _normalize_graph_label(self, raw: Any) -> tuple[str, str]:
        if isinstance(raw, dict):
            label = str(raw.get("name") or raw.get("symbol") or raw.get("target") or raw.get("id") or "").strip()
            file_path = str(raw.get("filePath") or raw.get("file_path") or raw.get("path") or "").strip()
            return label, file_path
        text = str(raw or "").strip()
        if not text:
            return "", ""
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = ast.literal_eval(text)
            except (SyntaxError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                return self._normalize_graph_label(parsed)
        return text, ""

    def _graph_symbol_id(self, symbol: ImpactSymbol) -> str:
        if symbol.container and symbol.symbol and symbol.container != symbol.symbol:
            return f"{symbol.container}.{symbol.symbol}"
        return str(symbol.symbol or "").strip()

    def _context_symbol_name(self, context: dict[str, Any]) -> str:
        symbol = context.get("symbol")
        if isinstance(symbol, dict):
            for key in ("name", "uid", "symbol", "target"):
                value = str(symbol.get(key) or "").strip()
                if value:
                    return value
        for key in ("name", "target", "symbol"):
            value = str(context.get(key) or "").strip()
            if value:
                return value
        return ""

    def _context_links(self, context: dict[str, Any], direction: str) -> list[dict[str, str]]:
        payload = context.get(direction)
        if not isinstance(payload, dict):
            return []
        items: list[dict[str, str]] = []
        for relationship_key in ("calls", "imports", "references", "uses", "has_method", "has_property", "extends", "implements"):
            for item in list(payload.get(relationship_key) or []):
                if isinstance(item, str) and item.strip():
                    label, _ = self._normalize_graph_label(item)
                    if not label:
                        continue
                    items.append({"label": label, "kind": "symbol", "relationship": relationship_key})
                    continue
                if not isinstance(item, dict):
                    continue
                label, file_path = self._normalize_graph_label(item)
                if not label:
                    continue
                items.append(
                    {
                        "label": label,
                        "kind": str(item.get("kind") or item.get("type") or "symbol"),
                        "relationship": relationship_key,
                        "file_path": file_path,
                    }
                )
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for item in items:
            key = (item["label"], item["relationship"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped[:12]

    def _context_processes(self, context: dict[str, Any]) -> list[str]:
        values: list[str] = []
        for item in list(context.get("processes") or []):
            if isinstance(item, str) and item.strip():
                values.append(item.strip())
            elif isinstance(item, dict):
                name = str(item.get("name") or item.get("process") or item.get("title") or "").strip()
                if name:
                    values.append(name)
        return self._dedupe(values)[:8]

    def _file_risk(self, path: str) -> str:
        if self._is_controller_or_api(path) or self._is_repository_or_sql(path):
            return "high"
        if self._is_test_file(path):
            return "medium"
        return "medium" if "/service" in path.lower() or "service" in Path(path).name.lower() else "low"

    def _is_test_file(self, path: str) -> bool:
        lowered = path.lower()
        return any(token in lowered for token in ("/test/", "/tests/", ".test.", ".spec.", "test_", "tests/"))

    def _is_controller_or_api(self, path: str) -> bool:
        lowered = path.lower()
        return any(token in lowered for token in ("controller", "/api/", "endpoint", "router", "resource"))

    def _is_repository_or_sql(self, path: str) -> bool:
        lowered = path.lower()
        return any(token in lowered for token in ("repository", "mapper", "dao", ".sql", "migration", "mybatis"))

    def _repo_path(self, subject: ReviewSubject, runtime: RuntimeSettings | None) -> str:
        metadata = dict(subject.metadata or {})
        for key in ("workspace_repo_path", "repo_context_workspace_path", "workspace_repo", "local_workspace_fallback"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        if runtime is not None:
            return str(getattr(runtime, "code_repo_local_path", "") or "").strip()
        return ""

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _dedupe_impact_files(self, values: list[ImpactFile]) -> list[ImpactFile]:
        seen: set[tuple[str, str]] = set()
        result: list[ImpactFile] = []
        for item in values:
            key = (item.file_path, item.relationship)
            if key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result
