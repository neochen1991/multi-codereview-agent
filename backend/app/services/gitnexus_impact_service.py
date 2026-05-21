from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import shlex
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
from app.repositories.fs import read_json, write_json
from app.services.command_resolver import resolve_executable
from app.services.cross_file_impact import _detect_signature_change
from app.services.feedback_learner_service import FeedbackLearnerService
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
    "string",
    "integer",
    "long",
    "double",
    "float",
    "boolean",
    "byte",
    "short",
    "character",
    "list",
    "map",
    "set",
    "optional",
    "select",
    "from",
    "where",
    "order",
    "by",
    "asc",
    "desc",
    "limit",
    "offset",
    "insert",
    "update",
    "delete",
    "join",
    "left",
    "right",
    "inner",
    "outer",
    "values",
    "group",
    "having",
}

MAX_GITNEXUS_TARGETS = 12
MAX_GITNEXUS_CONTEXT_QUERIES = 8
MAX_GITNEXUS_IMPACT_QUERIES = 8
MAX_GITNEXUS_DYNAMIC_TARGETS = 6


def _strip_wrapping_quotes(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _looks_like_windows_path(value: object) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^[A-Za-z]:[\\/]", text) or text.startswith("\\\\"))


def _looks_like_windows_command(value: object) -> bool:
    text = str(value or "").strip()
    return bool(re.search(r"[A-Za-z]:\\", text) or re.search(r'"[A-Za-z]:\\', text))


def _normalize_path_for_compare(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _looks_like_windows_path(text):
        normalized = text.replace("\\", "/")
        normalized = re.sub(r"/+", "/", normalized)
        return normalized.rstrip("/").lower()
    try:
        return str(Path(text).expanduser().resolve())
    except OSError:
        return os.path.normcase(os.path.normpath(os.path.expanduser(text))).rstrip(os.sep)


def _git_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/")


def _parse_command_text(raw: str) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        parsed_json = json.loads(text)
    except json.JSONDecodeError:
        parsed_json = None
    if isinstance(parsed_json, list):
        return [str(item).strip() for item in parsed_json if str(item).strip()]
    posix = os.name != "nt" and not _looks_like_windows_command(text)
    try:
        parts = shlex.split(text, posix=posix)
    except ValueError:
        parts = text.split()
    return [_strip_wrapping_quotes(part) for part in parts if _strip_wrapping_quotes(part)]


def _compact_json(value: object, *, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}... [truncated {len(text) - max_chars} chars]"


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
        max_targets: int = MAX_GITNEXUS_TARGETS,
        max_context_queries: int = MAX_GITNEXUS_CONTEXT_QUERIES,
        max_impact_queries: int = MAX_GITNEXUS_IMPACT_QUERIES,
        max_dynamic_targets: int = MAX_GITNEXUS_DYNAMIC_TARGETS,
        cli_available_repos: list[str] | None = None,
        impact_feedback_profiles: dict[str, Any] | None = None,
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
        max_targets: int = MAX_GITNEXUS_TARGETS,
        max_context_queries: int = MAX_GITNEXUS_CONTEXT_QUERIES,
        max_impact_queries: int = MAX_GITNEXUS_IMPACT_QUERIES,
        max_dynamic_targets: int = MAX_GITNEXUS_DYNAMIC_TARGETS,
        cli_available_repos: list[str] | None = None,
        impact_feedback_profiles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        command = self._command()
        max_targets = self._coerce_limit(max_targets, MAX_GITNEXUS_TARGETS)
        max_context_queries = self._coerce_limit(max_context_queries, MAX_GITNEXUS_CONTEXT_QUERIES)
        max_impact_queries = self._coerce_limit(max_impact_queries, MAX_GITNEXUS_IMPACT_QUERIES)
        max_dynamic_targets = self._coerce_limit(max_dynamic_targets, MAX_GITNEXUS_DYNAMIC_TARGETS, allow_zero=True)
        mcp_session = self._open_persistent_mcp_session(command, repo_path, runtime_env)
        logger.info(
            "gitnexus impact start repo=%s repo_path=%s changed_file_count=%s changed_symbol_count=%s command=%s max_targets=%s max_context=%s max_impact=%s max_dynamic=%s",
            repo_name,
            repo_path,
            len(list(subject.changed_files or [])),
            len(changed_symbols),
            " ".join(command),
            max_targets,
            max_context_queries,
            max_impact_queries,
            max_dynamic_targets,
        )
        try:
            list_repos_payload = self._call_tools_batch(
                command,
                repo_path,
                [
                    ("list_repos", {}),
                ],
                runtime_env,
                raise_on_error=False,
                mcp_session=mcp_session,
                include_initialize=True,
            )[0]
        except Exception:
            if mcp_session is not None:
                mcp_session.close()
            raise
        if "__error" in list_repos_payload:
            raise RuntimeError(f"GitNexus list_repos 调用失败: {list_repos_payload.get('__error')}")
        available_repos = self._extract_repo_names(list_repos_payload)
        effective_repo_name = self._resolve_available_repo_name(repo_name, repo_path, list_repos_payload) or repo_name
        skip_detect_changes_reason = self._duplicate_repo_name_sibling_reason(
            effective_repo_name,
            repo_path,
            list_repos_payload,
        )
        list_repos_missing_repo = False
        cli_repos = self._dedupe_strings(cli_available_repos or [])
        cli_list_has_repo = bool(cli_repos and effective_repo_name in cli_repos)
        if available_repos and effective_repo_name not in available_repos:
            list_repos_missing_repo = True
            logger.warning(
                "gitnexus list_repos did not include target repo repo=%s repo_path=%s mcp_available_repos=%s cli_available_repos=%s cli_has_repo=%s",
                effective_repo_name,
                repo_path,
                available_repos[:8],
                cli_repos[:8],
                cli_list_has_repo,
            )
            if not cli_list_has_repo:
                if mcp_session is not None:
                    mcp_session.close()
                raise RuntimeError(
                    "GitNexus MCP 未加载当前仓库，已停止后续 MCP 调用，避免检视流程卡住。"
                    f" target_repo={effective_repo_name} mcp_available_repos={available_repos[:8]} cli_available_repos={cli_repos[:8]}"
                )
            logger.warning(
                "gitnexus list_repos missed target repo but cli list confirmed it; continue to detect_changes repo=%s repo_path=%s",
                effective_repo_name,
                repo_path,
            )
        detect_changes_result: dict[str, Any] = {}
        if skip_detect_changes_reason:
            detect_changes_result = {"__error": skip_detect_changes_reason}
            logger.warning(
                "gitnexus detect_changes skipped repo=%s repo_path=%s reason=%s",
                effective_repo_name,
                repo_path,
                skip_detect_changes_reason,
            )
        else:
            detect_changes_result = self._call_tools_batch(
                command,
                repo_path,
                [
                    (
                        "detect_changes",
                        {
                            "repo": effective_repo_name,
                            "scope": "all",
                        },
                    ),
                ],
                runtime_env,
                raise_on_error=False,
                mcp_session=mcp_session,
                include_initialize=mcp_session is None,
            )[0]
        detect_changes_payload: dict[str, Any] = {}
        detect_changes_error = ""
        skipped_invalid_targets: list[str] = []
        skipped_missing_context_targets: list[str] = []
        skipped_missing_impact_targets: list[str] = []
        successful_context_targets: list[str] = []
        successful_impact_targets: list[str] = []
        dynamic_targets: list[str] = []
        if "__error" in detect_changes_result:
            detect_changes_error = str(detect_changes_result.get("__error") or "")
            if self._is_repo_unavailable_error(detect_changes_error):
                if mcp_session is not None:
                    mcp_session.close()
                raise RuntimeError(
                    "GitNexus MCP 对当前仓库不可用，已停止后续 context/impact 调用，避免检视流程卡住。"
                    f" repo={effective_repo_name} error={detect_changes_error}"
                )
            logger.warning(
                "gitnexus detect_changes unavailable repo=%s repo_path=%s error=%s; continue with changed symbols + context/impact",
                effective_repo_name,
                repo_path,
                detect_changes_error,
            )
        else:
            detect_changes_payload = detect_changes_result
        targets, skipped_invalid_targets = self._build_targets(
            changed_symbols,
            detect_changes_payload,
            max_targets=max_targets,
            impact_feedback_profiles=impact_feedback_profiles or {},
        )
        context_payloads: list[dict[str, Any]] = []
        impact_payloads: list[dict[str, Any]] = []
        if targets:
            (
                context_payloads,
                impact_payloads,
                skipped_missing_context_targets,
                skipped_missing_impact_targets,
                successful_context_targets,
                successful_impact_targets,
            ) = self._query_contexts_and_impacts(
                command,
                effective_repo_name,
                repo_path,
                targets,
                runtime_env,
                max_context_queries=max_context_queries,
                max_impact_queries=max_impact_queries,
                mcp_session=mcp_session,
            )
            dynamic_targets = self._dynamic_targets_from_contexts(
                context_payloads,
                existing_targets=targets,
                max_targets=max_dynamic_targets,
                changed_symbols=changed_symbols,
            )
            if dynamic_targets:
                (
                    dynamic_context_payloads,
                    dynamic_impact_payloads,
                    dynamic_skipped_context_targets,
                    dynamic_skipped_impact_targets,
                    dynamic_successful_context_targets,
                    dynamic_successful_impact_targets,
                ) = self._query_contexts_and_impacts(
                    command,
                    effective_repo_name,
                    repo_path,
                    dynamic_targets,
                    runtime_env,
                    max_context_queries=max_dynamic_targets,
                    max_impact_queries=max_dynamic_targets,
                    mcp_session=mcp_session,
                )
                context_payloads.extend(dynamic_context_payloads)
                impact_payloads.extend(dynamic_impact_payloads)
                targets = self._dedupe_strings([*targets, *dynamic_targets])
                skipped_missing_context_targets.extend(dynamic_skipped_context_targets)
                skipped_missing_impact_targets.extend(dynamic_skipped_impact_targets)
                successful_context_targets.extend(dynamic_successful_context_targets)
                successful_impact_targets.extend(dynamic_successful_impact_targets)
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
        try:
            return {
                "repo": effective_repo_name,
                "available_repos": available_repos,
                "cli_available_repos": cli_repos,
                "cli_list_has_repo": cli_list_has_repo,
                "list_repos_missing_repo": list_repos_missing_repo,
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
                "dynamic_targets": dynamic_targets,
            }
        finally:
            if mcp_session is not None:
                mcp_session.close()

    def _command(self) -> list[str]:
        raw = str(os.getenv("GITNEXUS_MCP_COMMAND") or "").strip()
        if raw:
            parsed = self._parse_command(raw)
            if parsed:
                return parsed
        binary = str(os.getenv("GITNEXUS_BIN") or "").strip()
        if binary:
            return [binary, "mcp"]
        return [resolve_executable("gitnexus") or "gitnexus", "mcp"]

    def _coerce_limit(self, value: int, default: int, *, allow_zero: bool = False) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = int(default)
        lower_bound = 0 if allow_zero else 1
        return max(lower_bound, min(50, parsed))

    def _parse_command(self, raw: str) -> list[str]:
        return _parse_command_text(raw)

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

    def _open_persistent_mcp_session(
        self,
        command: list[str],
        repo_path: str,
        runtime_env: dict[str, str] | None = None,
    ):
        if str(os.getenv("GITNEXUS_MCP_PERSISTENT", "")).strip().lower() not in {"1", "true", "yes", "on"}:
            return None
        if "_call_mcp" in self.__dict__:
            return None
        try:
            session = McpStdioClient(command, cwd=repo_path, timeout_seconds=self.timeout_seconds, env=runtime_env).open_session()
            session.start()
            return session
        except Exception as error:
            logger.warning("gitnexus persistent mcp session unavailable; fallback to per-batch process error=%s", error)
            return None

    def _call_tool(
        self,
        command: list[str],
        repo_path: str,
        tool_name: str,
        arguments: dict[str, Any],
        runtime_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._call_tools_batch(
            command,
            repo_path,
            [(tool_name, arguments)],
            runtime_env,
            raise_on_error=True,
        )[0]

    def _tool_response_payload(
        self,
        *,
        command: list[str],
        repo_path: str,
        tool_name: str,
        arguments: dict[str, Any],
        response_message: dict[str, Any],
        raise_on_error: bool,
    ) -> dict[str, Any]:
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
            if raise_on_error:
                raise RuntimeError(f"GitNexus {tool_name} 调用失败: {detail}")
            return {"__error": f"GitNexus {tool_name} 调用失败: {detail}"}
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
            if raise_on_error:
                raise RuntimeError(f"GitNexus {tool_name} 调用失败: {payload.get('error')}")
            return {"__error": f"GitNexus {tool_name} 调用失败: {payload.get('error')}"}
        logger.info(
            "gitnexus mcp tool response tool=%s repo_path=%s arguments=%s payload=%s",
            tool_name,
            repo_path,
            _compact_json(arguments),
            _compact_json(payload),
        )
        return payload

    def _call_tools_batch(
        self,
        command: list[str],
        repo_path: str,
        tool_calls: list[tuple[str, dict[str, Any]]],
        runtime_env: dict[str, str] | None = None,
        *,
        raise_on_error: bool,
        mcp_session=None,
        include_initialize: bool = True,
    ) -> list[dict[str, Any]]:
        requests = [self._initialize_request(), self._initialized_notification()] if include_initialize else []
        start_offset = 2 if include_initialize else 1
        for offset, (tool_name, arguments) in enumerate(tool_calls, start=start_offset):
            logger.info(
                "gitnexus mcp tool request tool=%s repo_path=%s command=%s arguments=%s",
                tool_name,
                repo_path,
                " ".join(command),
                _compact_json(arguments),
            )
            requests.append(
                {
                    "id": offset,
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments,
                    },
                }
            )
        try:
            responses = mcp_session.call_many(requests) if mcp_session is not None else self._call_mcp(command, repo_path, requests, runtime_env)
        except RuntimeError as error:
            tool_names = ",".join(name for name, _ in tool_calls)
            logger.exception(
                "gitnexus mcp batch failed tools=%s repo_path=%s command=%s",
                tool_names,
                repo_path,
                " ".join(command),
            )
            raise RuntimeError(f"GitNexus MCP 批量调用失败: {error}") from error
        payloads: list[dict[str, Any]] = []
        for offset, (tool_name, arguments) in enumerate(tool_calls, start=start_offset):
            payloads.append(
                self._tool_response_payload(
                    command=command,
                    repo_path=repo_path,
                    tool_name=tool_name,
                    arguments=arguments,
                    response_message=responses.get(offset) or {},
                    raise_on_error=raise_on_error,
                )
            )
        return payloads

    def _query_contexts(
        self,
        command: list[str],
        repo_name: str,
        repo_path: str,
        targets: list[str],
        runtime_env: dict[str, str] | None = None,
        query_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        results: list[dict[str, Any]] = []
        skipped_missing_targets: list[str] = []
        successful_targets: list[str] = []
        cache = query_cache if query_cache is not None else {}
        for target in targets[:MAX_GITNEXUS_CONTEXT_QUERIES]:
            logger.info("gitnexus context queue request repo=%s target=%s", repo_name, target)
            try:
                results.append(
                    self._call_cached_tool(
                        command=command,
                        repo_path=repo_path,
                        tool_name="context",
                        target=target,
                        arguments={
                            "repo": repo_name,
                            "name": target,
                        },
                        runtime_env=runtime_env,
                        query_cache=cache,
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

    def _query_contexts_and_impacts(
        self,
        command: list[str],
        repo_name: str,
        repo_path: str,
        targets: list[str],
        runtime_env: dict[str, str] | None = None,
        *,
        max_context_queries: int = MAX_GITNEXUS_CONTEXT_QUERIES,
        max_impact_queries: int = MAX_GITNEXUS_IMPACT_QUERIES,
        mcp_session=None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], list[str], list[str], list[str]]:
        context_targets = targets[: max(0, int(max_context_queries or 0))]
        impact_targets = targets[: max(0, int(max_impact_queries or 0))]
        tool_calls: list[tuple[str, dict[str, Any]]] = []
        response_slots: list[tuple[str, str]] = []
        for target in context_targets:
            logger.info("gitnexus context queue request repo=%s target=%s", repo_name, target)
            tool_calls.append(
                (
                    "context",
                    {
                        "repo": repo_name,
                        "name": target,
                    },
                )
            )
            response_slots.append(("context", target))
        for target in impact_targets:
            logger.info("gitnexus impact queue request repo=%s target=%s", repo_name, target)
            tool_calls.append(
                (
                    "impact",
                    {
                        "repo": repo_name,
                        "target": target,
                    },
                )
            )
            response_slots.append(("impact", target))
        if not tool_calls:
            return [], [], [], [], [], []

        payloads = self._call_tools_batch(
            command,
            repo_path,
            tool_calls,
            runtime_env,
            raise_on_error=False,
            mcp_session=mcp_session,
            include_initialize=mcp_session is None,
        )
        context_results: list[dict[str, Any]] = []
        impact_results: list[dict[str, Any]] = []
        skipped_missing_context_targets: list[str] = []
        skipped_missing_impact_targets: list[str] = []
        successful_context_targets: list[str] = []
        successful_impact_targets: list[str] = []
        for (tool_name, target), payload in zip(response_slots, payloads, strict=False):
            error_text = str(payload.get("__error") or "").strip()
            if error_text:
                error = RuntimeError(error_text)
                if self._is_missing_symbol_error(error):
                    logger.warning(
                        "gitnexus %s skipped missing symbol repo=%s target=%s error=%s",
                        tool_name,
                        repo_name,
                        target,
                        error,
                    )
                    if tool_name == "context":
                        skipped_missing_context_targets.append(target)
                    else:
                        skipped_missing_impact_targets.append(target)
                    continue
                raise error
            if tool_name == "context":
                context_results.append(payload)
                successful_context_targets.append(target)
            else:
                impact_results.append(payload)
                successful_impact_targets.append(target)
        return (
            context_results,
            impact_results,
            skipped_missing_context_targets,
            skipped_missing_impact_targets,
            successful_context_targets,
            successful_impact_targets,
        )

    def _query_impacts(
        self,
        command: list[str],
        repo_name: str,
        repo_path: str,
        targets: list[str],
        runtime_env: dict[str, str] | None = None,
        query_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        results: list[dict[str, Any]] = []
        skipped_missing_targets: list[str] = []
        successful_targets: list[str] = []
        cache = query_cache if query_cache is not None else {}
        for target in targets[:MAX_GITNEXUS_IMPACT_QUERIES]:
            logger.info("gitnexus impact queue request repo=%s target=%s", repo_name, target)
            try:
                results.append(
                    self._call_cached_tool(
                        command=command,
                        repo_path=repo_path,
                        tool_name="impact",
                        target=target,
                        arguments={
                            "repo": repo_name,
                            "target": target,
                        },
                        runtime_env=runtime_env,
                        query_cache=cache,
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

    def _call_cached_tool(
        self,
        *,
        command: list[str],
        repo_path: str,
        tool_name: str,
        target: str,
        arguments: dict[str, Any],
        runtime_env: dict[str, str] | None,
        query_cache: dict[tuple[str, str], dict[str, Any]],
    ) -> dict[str, Any]:
        key = (tool_name, target)
        if key not in query_cache:
            query_cache[key] = self._call_tool(command, repo_path, tool_name, arguments, runtime_env)
        return dict(query_cache[key])

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
        *,
        max_targets: int = MAX_GITNEXUS_TARGETS,
        impact_feedback_profiles: dict[str, Any] | None = None,
    ) -> tuple[list[str], list[str]]:
        candidates: list[dict[str, Any]] = []
        for item in changed_symbols:
            symbol = str(item.symbol or "").strip()
            container = str(item.container or "").strip()
            if container:
                candidates.append({"target": container, "source": "changed_container", "symbol": item})
            if container and symbol and container != symbol:
                candidates.append({"target": f"{container}.{symbol}", "source": "changed_container_symbol", "symbol": item})
            if symbol:
                candidates.append({"target": symbol, "source": "changed_symbol", "symbol": item})
        for key in ("changed_symbols", "changedSymbols", "symbols"):
            for item in list(detect_changes_payload.get(key) or []):
                if isinstance(item, str) and item.strip():
                    candidates.append({"target": item.strip(), "source": "detect_changes"})
                elif isinstance(item, dict):
                    symbol = str(item.get("name") or item.get("symbol") or item.get("target") or "").strip()
                    if symbol:
                        candidates.append({"target": symbol, "source": "detect_changes", "raw": item})
        ranked: dict[str, dict[str, Any]] = {}
        skipped_invalid: list[str] = []
        for item in candidates:
            target = str(item.get("target") or "").strip()
            if not target:
                continue
            if not self._is_valid_symbol_target(target):
                skipped_invalid.append(target)
                continue
            score = self._target_priority_score(target, item, impact_feedback_profiles or {})
            previous = ranked.get(target)
            if previous is None or score > float(previous.get("score") or 0.0):
                ranked[target] = {**item, "target": target, "score": score}
        ordered = sorted(ranked.values(), key=lambda item: (-float(item.get("score") or 0.0), str(item.get("target") or "")))
        return [str(item.get("target") or "") for item in ordered[: self._coerce_limit(max_targets, MAX_GITNEXUS_TARGETS)]], self._dedupe_strings(skipped_invalid)

    def _dynamic_targets_from_contexts(
        self,
        context_payloads: list[dict[str, Any]],
        *,
        existing_targets: list[str],
        max_targets: int = MAX_GITNEXUS_DYNAMIC_TARGETS,
        changed_symbols: list[ImpactSymbol] | None = None,
    ) -> list[str]:
        existing = {str(item or "").strip() for item in existing_targets if str(item or "").strip()}
        changed_symbols = list(changed_symbols or [])
        candidates: dict[str, float] = {}
        for context in context_payloads:
            for relation_key, base_score in (
                ("incoming", 32.0),
                ("callers", 32.0),
                ("outgoing", 26.0),
                ("callees", 26.0),
                ("byDepth", 20.0),
                ("by_depth", 20.0),
            ):
                for name in self._extract_context_symbol_names(context.get(relation_key)):
                    target = str(name or "").strip()
                    if not target or target in existing or not self._is_valid_symbol_target(target):
                        continue
                    score = base_score + self._dynamic_target_priority_bonus(target)
                    score += self._dynamic_changed_symbol_bonus(target, changed_symbols)
                    candidates[target] = max(candidates.get(target, 0.0), score)
        ordered = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
        return [target for target, _score in ordered[: self._coerce_limit(max_targets, MAX_GITNEXUS_DYNAMIC_TARGETS, allow_zero=True)]]

    def _extract_context_symbol_names(self, value: Any) -> list[str]:
        names: list[str] = []
        if isinstance(value, str):
            if value.strip():
                names.append(value.strip())
            return names
        if isinstance(value, dict):
            for key in ("uid", "id", "name", "symbol", "target", "method", "qualifiedName", "qualified_name", "signature"):
                candidate = str(value.get(key) or "").strip()
                if candidate:
                    names.append(candidate)
            for key in (
                "calls",
                "has_method",
                "hasMethod",
                "methods",
                "symbols",
                "items",
                "nodes",
                "children",
                "results",
                "entries",
            ):
                names.extend(self._extract_context_symbol_names(value.get(key)))
            return names
        if isinstance(value, list):
            for item in value:
                names.extend(self._extract_context_symbol_names(item))
        return names

    def _dynamic_target_priority_bonus(self, target: str) -> float:
        lowered = str(target or "").lower()
        score = 0.0
        if lowered.startswith("method:"):
            score += 24
        if lowered.startswith("constructor:"):
            score -= 10
        if "." in target:
            score += 4
        if any(token in lowered for token in ("controller", "endpoint", "router", "resource", "api")):
            score += 12
        if any(token in lowered for token in ("service", "manager", "handler", "processor")):
            score += 9
        if any(token in lowered for token in ("repository", "mapper", "dao", "client")):
            score += 6
        if any(token in lowered for token in ("test", "dto", "request", "response", "config", "constant")):
            score -= 8
        if "/test/" in lowered or "\\test\\" in lowered:
            score -= 16
        return score

    def _dynamic_changed_symbol_bonus(self, target: str, changed_symbols: list[ImpactSymbol]) -> float:
        target_text = str(target or "").strip()
        if not target_text:
            return 0.0
        target_lower = target_text.lower().replace("\\", "/")
        score = 0.0
        for symbol in changed_symbols:
            name = str(getattr(symbol, "symbol", "") or "").strip().lower()
            file_path = str(getattr(symbol, "file_path", "") or "").strip().lower().replace("\\", "/")
            if not name:
                continue
            if name in target_lower:
                score += 16
                if file_path and file_path in target_lower:
                    score += 28
        return min(score, 52.0)

    def _target_priority_score(
        self,
        target: str,
        candidate: dict[str, Any],
        impact_feedback_profiles: dict[str, Any] | None = None,
    ) -> float:
        score = 0.0
        source = str(candidate.get("source") or "")
        if source == "changed_container_symbol":
            score += 45
        elif source == "changed_symbol":
            score += 28
        elif source == "detect_changes":
            score += 30
        elif source == "changed_container":
            score += 56
        symbol = candidate.get("symbol")
        file_path = str(getattr(symbol, "file_path", "") or "").lower()
        kind = str(getattr(symbol, "kind", "") or "").lower()
        line_start = int(getattr(symbol, "line_start", 0) or 0)
        target_lower = str(target or "").lower()
        if kind == "function":
            score += 8
        if kind == "class":
            score += 4
        if any(token in file_path or token in target_lower for token in ("controller", "endpoint", "router", "resource", "/api/")):
            score += 18
        if any(token in file_path or token in target_lower for token in ("service", "applicationservice", "manager")):
            score += 14
        if any(token in file_path or token in target_lower for token in ("repository", "mapper", "dao")):
            score += 10
        if "." in target:
            score += 6
        if line_start > 0:
            score += 2
        if any(token in target_lower for token in ("test", "dto", "request", "response", "config", "constant")):
            score -= 8
        score += self._target_feedback_score_adjustment(target, impact_feedback_profiles or {})
        return score

    def _target_feedback_score_adjustment(self, target: str, impact_feedback_profiles: dict[str, Any]) -> float:
        target_text = str(target or "").strip().lower()
        if not target_text:
            return 0.0
        adjustment = 0.0
        for profile in dict(impact_feedback_profiles.get("targets") or {}).values():
            if not isinstance(profile, dict):
                continue
            target_type = str(profile.get("target_type") or "").strip()
            if target_type and target_type not in {"impact_path", "impact_file", "test_scope"}:
                continue
            key_text = str(profile.get("target_key") or "").strip().lower()
            if not key_text or target_text not in key_text:
                continue
            action = str(profile.get("recommended_action") or "").strip()
            false_positive_rate = float(
                profile.get("false_positive_rate")
                or profile.get("smoothed_false_positive_rate")
                or 0.0
            )
            accept_rate = float(profile.get("accept_rate") or profile.get("smoothed_accept_rate") or 0.0)
            sample_count = int(profile.get("sample_count") or 0)
            if action == "require_manual_verification" or false_positive_rate >= 0.6:
                adjustment -= 18 + min(10, sample_count)
            elif action == "watch_for_false_positive" or false_positive_rate >= 0.45:
                adjustment -= 8
            elif action == "boost_confidence" or accept_rate >= 0.8:
                adjustment += 10 + min(6, sample_count)
            elif action == "soft_boost_confidence" or accept_rate >= 0.55:
                adjustment += 4
        return adjustment

    def _is_missing_symbol_error(self, error: RuntimeError) -> bool:
        message = str(error or "").lower()
        return ("symbol " in message and " not found" in message) or (
            "target " in message and " not found" in message
        )

    def _is_repo_unavailable_error(self, error: str) -> bool:
        message = str(error or "").lower()
        repo_terms = ("repo", "repository", "仓库", "图谱", "graph")
        unavailable_terms = (
            "not found",
            "not indexed",
            "not loaded",
            "not available",
            "missing",
            "unknown",
            "未发现",
            "未找到",
            "未索引",
            "未加载",
            "不可用",
            "不存在",
        )
        return any(term in message for term in repo_terms) and any(term in message for term in unavailable_terms)

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

    def _duplicate_repo_name_sibling_reason(self, repo_name: str, repo_path: str, payload: dict[str, Any]) -> str:
        """Detect GitNexus list_repos ambiguity where current repo is only listed as a sibling.

        Some GitNexus versions return the first registry entry as the direct repo and put the
        current working tree under `siblings` with the same repo name. Calling detect_changes by
        name can then hit the stale direct repo. Context/impact still work from cwd, so we skip
        detect_changes and continue from explicit changed symbols.
        """

        repo_path_resolved = _normalize_path_for_compare(repo_path)
        if not repo_name or not repo_path_resolved:
            return ""
        direct_name = str(payload.get("name") or payload.get("repo") or payload.get("repo_name") or "").strip()
        direct_path = str(payload.get("path") or payload.get("repoPath") or payload.get("repo_path") or "").strip()
        if direct_name != repo_name or not direct_path:
            return ""
        if _normalize_path_for_compare(direct_path) == repo_path_resolved:
            return ""

        def sibling_matches(items: object) -> bool:
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                return False
            for item in items:
                if not isinstance(item, dict):
                    continue
                candidate_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
                candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
                if candidate_name == repo_name and candidate_path and _normalize_path_for_compare(candidate_path) == repo_path_resolved:
                    return True
            return False

        if sibling_matches(payload.get("siblings")):
            return (
                "GitNexus list_repos 将当前 worktree 作为同名 sibling 返回；"
                "为避免 detect_changes 命中旧 registry 主仓，已跳过 detect_changes，改用显式 changed symbols 查询 context/impact。"
            )
        return (
            "GitNexus list_repos 返回的同名仓库路径与当前 worktree 不一致；"
            "为避免 detect_changes 命中旧 registry 主仓，已跳过 detect_changes，改用显式 changed symbols 查询 context/impact。"
        )


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
        """返回标准化影响报告；GitNexus 不可用时生成可展示的降级报告。"""
        report, _ = self.analyze_with_trace(subject, runtime)
        return report

    def build_fallback_report(self, subject: ReviewSubject, runtime: RuntimeSettings | None = None) -> ImpactReport:
        """为报告页生成非执行态的降级影响报告。"""

        return self._fallback_report(subject, runtime)

    def preflight(self, subject: ReviewSubject, runtime: RuntimeSettings | None = None) -> dict[str, Any]:
        """检查 GitNexus 影响分析的本机可用性，便于 Windows/本地部署排障。"""

        checks: list[dict[str, str]] = []
        actions: list[str] = []

        def add_check(name: str, status: str, message: str, action: str = "") -> None:
            checks.append({"name": name, "status": status, "message": message})
            if action:
                actions.append(action)

        git_binary = shutil.which("git")
        add_check(
            "git_binary",
            "passed" if git_binary else "failed",
            f"已发现 git: {git_binary}" if git_binary else "未发现 git 可执行文件。",
            "安装 Git for Windows，并确认 git.exe 已加入 PATH。" if not git_binary else "",
        )

        command = self._gitnexus_command_for_diagnostics()
        command_available = self._gitnexus_command_available(command)
        add_check(
            "gitnexus_command",
            "passed" if command_available else "failed",
            f"GitNexus MCP 命令可用: {' '.join(command)}"
            if command_available
            else f"GitNexus MCP 命令不可用: {' '.join(command)}",
            "配置 GITNEXUS_BIN 或 GITNEXUS_MCP_COMMAND；Windows 空格路径建议使用 JSON array 或引号包裹。"
            if not command_available
            else "",
        )

        repo_path = self._repo_path(subject, runtime)
        if not repo_path:
            add_check(
                "repo_path",
                "failed",
                "未配置本地代码仓路径。",
                "在仓库配置或 review metadata 中设置 workspace_repo_path/code_repo_local_path。",
            )
            return self._preflight_result(checks, actions)
        repo_root = Path(repo_path)
        repo_exists = repo_root.exists() and repo_root.is_dir()
        add_check(
            "repo_path",
            "passed" if repo_exists else "failed",
            f"本地代码仓路径存在: {repo_path}" if repo_exists else f"本地代码仓路径不存在: {repo_path}",
            "确认 Windows 路径盘符、共享目录权限和工作区挂载位置。" if not repo_exists else "",
        )
        if not repo_exists:
            return self._preflight_result(checks, actions)

        git_dir_exists = (repo_root / ".git").exists()
        add_check(
            "git_repository",
            "passed" if git_dir_exists else "warning",
            "本地路径包含 .git。"
            if git_dir_exists
            else "本地路径未发现 .git，GitNexus 仍可能可用，但本地 diff/符号补充会受限。",
            "确认 repo_path 指向真实 Git 工作区根目录。" if not git_dir_exists else "",
        )

        registry_path = self._gitnexus_registry_path(subject, runtime)
        registry_exists = registry_path.exists()
        add_check(
            "gitnexus_registry",
            "passed" if registry_exists else "warning",
            f"GitNexus registry 存在: {registry_path}"
            if registry_exists
            else f"未发现 GitNexus registry: {registry_path}",
            "运行 gitnexus analyze 或确认 GITNEXUS_HOME/GITNEXUS_REGISTRY_PATH 是否指向正确目录。"
            if not registry_exists
            else "",
        )
        registry = self._load_gitnexus_registry(subject, runtime)
        repo_name = self._resolve_repo_name_from_registry(repo_path, registry)
        add_check(
            "registry_repo_match",
            "passed" if repo_name else "warning",
            f"registry 已匹配仓库: {repo_name}" if repo_name else "registry 未按当前 repo_path 匹配到仓库。",
            "在 Windows 上请确认 registry 里的路径盘符、大小写和斜杠与当前 repo_path 等价。"
            if not repo_name
            else "",
        )

        graph_status = self._load_graph_status(repo_path, registry, subject=subject, runtime=runtime)
        state = str(graph_status.get("state") or "").strip().lower()
        add_check(
            "graph_status",
            "passed" if state == "ready" else "failed",
            f"GitNexus 图谱状态 ready，commit={graph_status.get('commit') or ''}"
            if state == "ready"
            else f"GitNexus 图谱未就绪，当前状态: {state or 'missing'}",
            "在目标仓库运行 gitnexus analyze，生成 .gitnexus/meta.json 或 index_status.json。"
            if state != "ready"
            else "",
        )
        return self._preflight_result(checks, actions)

    def _preflight_result(self, checks: list[dict[str, str]], actions: list[str]) -> dict[str, Any]:
        statuses = {str(item.get("status") or "") for item in checks}
        status = "failed" if "failed" in statuses else "warning" if "warning" in statuses else "ready"
        deduped_actions: list[str] = []
        seen: set[str] = set()
        for action in actions:
            if action in seen:
                continue
            seen.add(action)
            deduped_actions.append(action)
        return {"status": status, "checks": checks, "recommended_actions": deduped_actions}

    def _gitnexus_command_for_diagnostics(self) -> list[str]:
        raw = str(os.getenv("GITNEXUS_MCP_COMMAND") or "").strip()
        if raw:
            parsed = _parse_command_text(raw)
            if parsed:
                return parsed
        binary = str(os.getenv("GITNEXUS_BIN") or "").strip()
        if binary:
            return [binary, "mcp"]
        return [resolve_executable("gitnexus") or "gitnexus", "mcp"]

    def _gitnexus_command_available(self, command: list[str]) -> bool:
        executable = str(command[0] if command else "").strip()
        if not executable:
            return False
        return bool(resolve_executable(executable))

    def _gitnexus_list_command(self, mcp_command: list[str]) -> list[str]:
        raw = str(os.getenv("GITNEXUS_LIST_COMMAND") or "").strip()
        if raw:
            parsed = _parse_command_text(raw)
            if parsed:
                return parsed
        if mcp_command:
            command = list(mcp_command)
            if str(command[-1]).strip().lower() == "mcp":
                return [*command[:-1], "list"]
        binary = str(os.getenv("GITNEXUS_BIN") or "").strip()
        if binary:
            return [binary, "list"]
        return [resolve_executable("gitnexus") or "gitnexus", "list"]

    def _gitnexus_cli_available_repos(
        self,
        mcp_command: list[str],
        repo_path: str,
        runtime_env: dict[str, str] | None,
    ) -> list[str]:
        command = self._gitnexus_list_command(mcp_command)
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
                env=runtime_env,
                check=False,
            )
        except Exception as error:
            logger.warning("gitnexus list failed command=%s repo_path=%s error=%s", " ".join(command), repo_path, error)
            return []
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        repos = self._parse_gitnexus_list_output(output)
        logger.info(
            "gitnexus cli list command=%s repo_path=%s returncode=%s repos=%s output=%s",
            " ".join(command),
            repo_path,
            completed.returncode,
            repos[:8],
            _compact_json(output, max_chars=1200),
        )
        return repos if completed.returncode == 0 else []

    def _parse_gitnexus_list_output(self, output: str) -> list[str]:
        text = str(output or "").strip()
        if not text:
            return []
        parsed = GitNexusMcpImpactClient()._parse_json_text(text)
        if parsed is not None:
            return self._extract_repo_names_from_any(parsed)
        candidates: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lowered = line.lower()
            if (
                lowered in {"name", "repo", "repository", "path"}
                or lowered.startswith(
                    (
                        "indexed repositories",
                        "name ",
                        "name|",
                        "repo ",
                        "repo|",
                        "repository ",
                        "repository|",
                        "path ",
                        "path|",
                        "----",
                        "====",
                    )
                )
            ):
                continue
            if line.startswith(("-", "*")):
                line = line[1:].strip()
            if "|" in line:
                parts = [part.strip() for part in line.strip("|").split("|") if part.strip()]
                if parts:
                    line = parts[0]
            for prefix in ("name:", "repo:", "repository:"):
                if line.lower().startswith(prefix):
                    line = line[len(prefix) :].strip()
                    break
            if ":" in line:
                continue
            if not line:
                continue
            token = line.split()[0].strip().strip(",;")
            if token and token.lower() not in {"name", "repo", "repository"}:
                candidates.append(token)
        return self._dedupe([item for item in candidates if item])

    def _extract_repo_names_from_any(self, value: Any) -> list[str]:
        names: list[str] = []
        if isinstance(value, str):
            if value.strip():
                names.append(value.strip())
            return self._dedupe(names)
        if isinstance(value, list):
            for item in value:
                names.extend(self._extract_repo_names_from_any(item))
            return self._dedupe(names)
        if isinstance(value, dict):
            direct = str(value.get("name") or value.get("repo") or value.get("repo_name") or value.get("repository") or "").strip()
            if direct:
                names.append(direct)
            for key in ("repos", "repositories", "items", "results", "value", "data"):
                names.extend(self._extract_repo_names_from_any(value.get(key)))
        return self._dedupe(names)

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
        try:
            return self._gitnexus_graph_report(subject, runtime)
        except RuntimeError as error:
            logger.warning("gitnexus impact downgraded to fallback report error=%s", error)
            base_fallback = self._fallback_report(subject, runtime)
            fallback = base_fallback.model_copy(
                update={
                    "graph_status": "degraded",
                    "limitations": self._dedupe(
                        [
                            f"GitNexus 图谱不可用，本次影响范围已降级为候选分析：{error}",
                            *base_fallback.limitations,
                        ]
                    ),
                    "manual_verification": self._dedupe(
                        [
                            "GitNexus 图谱未确认调用链，请人工核对真实上下游影响。",
                            *base_fallback.manual_verification,
                        ]
                    ),
                }
            )
            return fallback, {
                "source": "fallback",
                "workflow": [
                    "GitNexus 图谱预检未通过。",
                    "系统已基于 diff/changed_files 生成候选影响范围。",
                    "请完成 gitnexus analyze 后重新运行以获得真实调用链。",
                ],
                "error": str(error),
            }

    def _cached_gitnexus_report(self, subject: ReviewSubject) -> ImpactReport | None:
        metadata = dict(subject.metadata or {})
        raw = metadata.get("gitnexus_impact_report") or metadata.get("impact_report")
        if not isinstance(raw, dict):
            return None
        payload = dict(raw)
        payload.setdefault("changed_files", list(subject.changed_files))
        payload.setdefault("graph_status", "ready")
        return self._apply_feedback_profiles_to_report(ImpactReport.model_validate(payload))

    def _apply_feedback_profiles_to_report(self, report: ImpactReport) -> ImpactReport:
        impact_paths, manual_verification = self._apply_impact_feedback_profiles(
            report.impact_paths,
            self._impact_feedback_profiles(),
        )
        test_scopes = self._apply_impact_feedback_to_test_scopes(report.recommended_test_scope, impact_paths)
        if impact_paths is report.impact_paths and not manual_verification:
            return report
        return report.model_copy(
            update={
                "impact_paths": impact_paths,
                "recommended_test_scope": test_scopes,
                "manual_verification": self._dedupe([*manual_verification, *report.manual_verification]),
            }
        )

    def _gitnexus_graph_report(
        self,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None,
    ) -> tuple[ImpactReport, dict[str, Any]]:
        repo_path = self._repo_path(subject, runtime)
        if not repo_path:
            raise RuntimeError("未配置本地代码仓路径，无法执行 GitNexus 关联影响分析。")
        command = self._gitnexus_command_for_diagnostics()
        if not self._gitnexus_command_available(command):
            raise RuntimeError(
                "当前机器未预装 GitNexus，或配置的 GitNexus MCP 命令不可用。"
                f"请检查 GITNEXUS_BIN/GITNEXUS_MCP_COMMAND/PATH，当前命令: {' '.join(command)}"
            )
        workspace_unready_reason = self._mr_review_workspace_unready_reason(subject, runtime)
        if workspace_unready_reason:
            raise RuntimeError(workspace_unready_reason)
        self._ensure_review_workspace_gitnexus_index(repo_path, subject, runtime, command)
        registry = self._load_gitnexus_registry(subject, runtime)
        graph_status = self._load_graph_status(repo_path, registry, subject=subject, runtime=runtime)
        logger.info(
            "gitnexus graph status repo_path=%s state=%s repo_name=%s",
            repo_path,
            graph_status.get("state"),
            graph_status.get("repo_name"),
        )
        if str(graph_status.get("state") or "") != "ready":
            raise RuntimeError("GitNexus 图谱未就绪，请先完成 gitnexus analyze 建图。")
        source_alignment_error = self._validate_graph_matches_mr_source(repo_path, subject, graph_status, runtime)
        if source_alignment_error:
            raise RuntimeError(source_alignment_error)
        if not self._resolve_repo_name_from_registry(repo_path, registry):
            registered, registry_path, repo_name, registry_error = self._ensure_gitnexus_registry_entry(repo_path, subject, runtime)
            if registered:
                logger.info(
                    "gitnexus registry self-healed before mcp analyze repo=%s repo_path=%s registry_path=%s",
                    repo_name,
                    repo_path,
                    registry_path,
                )
                registry = self._load_gitnexus_registry(subject, runtime)
                graph_status = self._load_graph_status(repo_path, registry, subject=subject, runtime=runtime)
            elif registry_error:
                logger.warning(
                    "gitnexus registry self-heal failed before mcp analyze repo_path=%s registry_path=%s error=%s",
                    repo_path,
                    registry_path,
                    registry_error,
                )
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
        registry_path = self._gitnexus_registry_path(subject, runtime)
        runtime_env = self._gitnexus_runtime_env(subject, runtime)
        cli_available_repos = self._gitnexus_cli_available_repos(command, repo_path, runtime_env)
        logger.info(
            "gitnexus graph analyze repo=%s repo_path=%s registry_path=%s registry_exists=%s cli_available_repos=%s env_HOME=%s env_USERPROFILE=%s env_GITNEXUS_HOME=%s env_GITNEXUS_REGISTRY_PATH=%s changed_files=%s changed_symbols=%s symbols=%s",
            resolved_repo_name,
            repo_path,
            registry_path,
            registry_path.exists(),
            cli_available_repos[:8],
            (runtime_env or {}).get("HOME", ""),
            (runtime_env or {}).get("USERPROFILE", ""),
            (runtime_env or {}).get("GITNEXUS_HOME", ""),
            (runtime_env or {}).get("GITNEXUS_REGISTRY_PATH", ""),
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
                runtime_env=runtime_env,
                max_targets=runtime.gitnexus_max_targets,
                max_context_queries=runtime.gitnexus_max_context_queries,
                max_impact_queries=runtime.gitnexus_max_impact_queries,
                max_dynamic_targets=runtime.gitnexus_max_dynamic_targets,
                cli_available_repos=cli_available_repos,
                impact_feedback_profiles=self._impact_feedback_profiles(),
            )
        except Exception as error:
            logger.exception(
                "gitnexus graph analyze failed repo=%s repo_path=%s changed_symbols=%s changed_files=%s",
                resolved_repo_name,
                repo_path,
                [f"{item.container + '.' if item.container else ''}{item.symbol}" for item in changed_symbols[:12]],
                self._changed_files(subject)[:20],
            )
            raise RuntimeError(
                "GitNexus 图谱已就绪，但按官方 MCP 流程调用失败："
                f"{error}；当前 registry={registry_path}；"
                f"MCP 环境 HOME={(runtime_env or {}).get('HOME', '')}，"
                f"GITNEXUS_HOME={(runtime_env or {}).get('GITNEXUS_HOME', '')}，"
                f"GITNEXUS_REGISTRY_PATH={(runtime_env or {}).get('GITNEXUS_REGISTRY_PATH', '')}"
            ) from error
        report = self._normalize_gitnexus_payload(subject, runtime, graph_status, raw, changed_symbols)
        trace = {
            "source": "gitnexus_mcp",
            "workflow": [
                "list_repos",
                "detect_changes(scope=all)",
                "context(key symbols)",
                "impact(key symbols)",
            ],
            "review_workspace": self._review_workspace_trace(subject),
            **raw,
        }
        return report, trace

    def _mr_review_workspace_unready_reason(self, subject: ReviewSubject, runtime: RuntimeSettings | None = None) -> str:
        if not self._review_workspace_realtime_graph_enabled(runtime):
            return ""
        if str(subject.subject_type or "").lower() != "mr" or not str(subject.unified_diff or "").strip():
            return ""
        metadata = dict(subject.metadata or {})
        workspace_record = metadata.get("review_workspace")
        status = str(metadata.get("review_workspace_status") or "").strip().lower()
        if isinstance(workspace_record, dict):
            status = status or str(workspace_record.get("status") or "").strip().lower()
        if not status or status == "ready":
            return ""
        message = str(metadata.get("review_workspace_message") or "").strip()
        if isinstance(workspace_record, dict) and not message:
            message = str(workspace_record.get("message") or "").strip()
        base_repo_path = str(
            metadata.get("review_workspace_base_repo_path")
            or metadata.get("base_workspace_repo_path")
            or metadata.get("configured_workspace_repo_path")
            or metadata.get("workspace_repo_path")
            or ""
        ).strip()
        return (
            "GitNexus 图谱不可用于本次 MR：MR 快照工作区未就绪，"
            "为避免分析设置页基础仓库的旧代码，本次 GitNexus 关联影响分析已降级。"
            f" review_workspace_status={status}"
            f" base_repo_path={base_repo_path}"
            f" message={message or '未记录原因'}"
        )

    def _review_workspace_trace(self, subject: ReviewSubject) -> dict[str, Any]:
        metadata = dict(subject.metadata or {})
        if str(metadata.get("review_workspace_status") or "") != "ready":
            return {}
        return {
            "status": "ready",
            "workspace_path": str(metadata.get("review_workspace_path") or metadata.get("workspace_repo_path") or ""),
            "base_repo_path": str(metadata.get("review_workspace_base_repo_path") or metadata.get("base_workspace_repo_path") or ""),
            "snapshot_mode": str(metadata.get("review_workspace_snapshot_mode") or ""),
            "snapshot_commit": str(metadata.get("review_workspace_commit") or ""),
            "diff_hash": str(metadata.get("review_workspace_diff_hash") or ""),
            "message": str(metadata.get("review_workspace_message") or ""),
        }

    def ensure_review_workspace_index(
        self,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None = None,
    ) -> dict[str, Any]:
        """确保本次 MR worktree 下有 GitNexus 图谱，供 intake 阶段主动预热。"""

        repo_path = self._repo_path(subject, runtime)
        if not repo_path:
            return {"status": "skipped", "message": "未解析到代码仓路径，跳过 GitNexus worktree 建图。"}
        command = self._gitnexus_command_for_diagnostics()
        if not self._gitnexus_command_available(command):
            return {
                "status": "skipped",
                "message": f"GitNexus 命令不可用，跳过 worktree 建图：{' '.join(command)}",
                "repo_path": repo_path,
            }
        return self._ensure_review_workspace_gitnexus_index(repo_path, subject, runtime, command)

    def _ensure_review_workspace_gitnexus_index(
        self,
        repo_path: str,
        subject: ReviewSubject,
        runtime: RuntimeSettings | None,
        mcp_command: list[str],
    ) -> dict[str, Any]:
        metadata = dict(subject.metadata or {})
        if str(metadata.get("review_workspace_status") or "") != "ready":
            return {
                "status": "skipped",
                "message": "当前任务不是 ready 状态的 MR worktree，跳过 GitNexus worktree 建图。",
                "repo_path": repo_path,
            }
        if _normalize_path_for_compare(str(metadata.get("review_workspace_path") or "")) != _normalize_path_for_compare(repo_path):
            return {
                "status": "skipped",
                "message": "当前 repo_path 与 review_workspace_path 不一致，跳过 GitNexus worktree 建图。",
                "repo_path": repo_path,
                "review_workspace_path": str(metadata.get("review_workspace_path") or ""),
            }
        repo_root = Path(repo_path).expanduser()
        if (repo_root / ".gitnexus" / "meta.json").exists() or (repo_root / ".gitnexus" / "index_status.json").exists():
            return {
                "status": "ready",
                "message": "GitNexus worktree 图谱已存在。",
                "repo_path": repo_path,
                "graph_dir": str(repo_root / ".gitnexus"),
            }
        analyze_command = self._gitnexus_analyze_command_from_mcp(mcp_command)
        timeout = self._review_workspace_index_timeout()
        logger.info("gitnexus review workspace auto index start repo_path=%s command=%s", repo_path, " ".join(analyze_command))
        try:
            completed = subprocess.run(
                analyze_command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=self._gitnexus_runtime_env(subject, runtime),
            )
        except Exception as error:
            logger.warning("gitnexus review workspace auto index failed repo_path=%s error=%s", repo_path, error)
            return {
                "status": "failed",
                "message": f"GitNexus worktree 建图异常：{error}",
                "error_type": error.__class__.__name__,
                "repo_path": repo_path,
                "timeout_seconds": timeout,
            }
        logger.info(
            "gitnexus review workspace auto index finish repo_path=%s return_code=%s stdout=%s stderr=%s",
            repo_path,
            completed.returncode,
            (completed.stdout or "")[-1200:],
            (completed.stderr or "")[-1200:],
        )
        graph_ready = (repo_root / ".gitnexus" / "meta.json").exists() or (repo_root / ".gitnexus" / "index_status.json").exists()
        return {
            "status": "ready" if completed.returncode == 0 and graph_ready else "failed",
            "message": "GitNexus worktree 图谱已创建。" if completed.returncode == 0 and graph_ready else "GitNexus worktree 建图未生成图谱文件。",
            "repo_path": repo_path,
            "graph_dir": str(repo_root / ".gitnexus"),
            "return_code": completed.returncode,
            "timeout_seconds": timeout,
            "stdout_tail": (completed.stdout or "")[-1200:],
            "stderr_tail": (completed.stderr or "")[-1200:],
        }

    def _review_workspace_index_timeout(self) -> int:
        try:
            return max(60, int(os.getenv("GITNEXUS_REVIEW_WORKSPACE_INDEX_TIMEOUT_SECONDS", "1800") or 1800))
        except (TypeError, ValueError):
            return 1800

    def _gitnexus_analyze_command_from_mcp(self, mcp_command: list[str]) -> list[str]:
        raw = str(os.getenv("GITNEXUS_ANALYZE_COMMAND") or "").strip()
        if raw:
            parsed = _parse_command_text(raw)
            if parsed:
                return parsed
        if mcp_command:
            command = list(mcp_command)
            if str(command[-1]).strip().lower() == "mcp":
                return [*command[:-1], "analyze"]
            return [command[0], "analyze"]
        return [resolve_executable("gitnexus") or "gitnexus", "analyze"]

    def _load_graph_status(
        self,
        repo_path: str,
        registry: list[dict[str, Any]] | None = None,
        *,
        subject: ReviewSubject | None = None,
        runtime: RuntimeSettings | None = None,
    ) -> dict[str, Any]:
        repo_root = Path(repo_path)
        candidates: list[tuple[Path, bool]] = [
            (repo_root / ".gitnexus" / "index_status.json", True),
        ]
        if self.storage_root is not None:
            repository_id = self._repository_id(subject, runtime)
            if repository_id:
                candidates.append((self.storage_root / "gitnexus" / self._safe_repository_id(repository_id) / "index_status.json", False))
            candidates.append((self.storage_root / "gitnexus" / "index_status.json", False))
        non_ready_status: dict[str, Any] | None = None
        for path, strict_repo_match in candidates:
            if not path.exists():
                continue
            try:
                status = read_json(path)
            except Exception:
                continue
            if isinstance(status, dict):
                if not self._graph_status_matches_repo_path(status, repo_path, strict_repo_match):
                    logger.info(
                        "gitnexus graph status ignored because repo_path mismatched status_path=%s expected_repo=%s actual_repo=%s",
                        path,
                        repo_path,
                        status.get("repo_path") or status.get("repoPath") or status.get("path"),
                    )
                    continue
                if str(status.get("state") or "").strip().lower() == "ready":
                    return status
                if non_ready_status is None:
                    non_ready_status = status
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
            if non_ready_status is not None:
                return non_ready_status
            resolved_repo_name = self._resolve_repo_name_from_registry(repo_path, registry)
            return {
                "state": "ready",
                "repo_path": repo_path,
                "repo_name": resolved_repo_name or repo_root.name,
                "updated_at": "",
            }
        if non_ready_status is not None:
            return non_ready_status
        return {}

    def _graph_status_matches_repo_path(self, status: dict[str, Any], repo_path: str, strict_repo_match: bool) -> bool:
        candidate_path = str(status.get("repo_path") or status.get("repoPath") or status.get("path") or "").strip()
        if candidate_path:
            return _normalize_path_for_compare(candidate_path) == _normalize_path_for_compare(repo_path)
        return strict_repo_match

    def _status_matches_repo_path(self, status: dict[str, Any], repo_path: str) -> bool:
        candidate_path = str(status.get("repo_path") or status.get("repoPath") or status.get("path") or "").strip()
        if not candidate_path:
            return False
        return _normalize_path_for_compare(candidate_path) == _normalize_path_for_compare(repo_path)

    def _validate_graph_matches_mr_source(
        self,
        repo_path: str,
        subject: ReviewSubject,
        graph_status: dict[str, Any],
        runtime: RuntimeSettings | None = None,
    ) -> str:
        if not self._review_workspace_realtime_graph_enabled(runtime):
            return ""
        if str(subject.subject_type or "").lower() != "mr" or not str(subject.unified_diff or "").strip():
            return ""
        if not (Path(repo_path).expanduser() / ".git").exists():
            return ""
        metadata = dict(subject.metadata or {})
        if str(metadata.get("review_workspace_status") or "") == "ready" and _normalize_path_for_compare(
            str(metadata.get("review_workspace_path") or "")
        ) == _normalize_path_for_compare(repo_path):
            snapshot_commit = str(metadata.get("review_workspace_commit") or "").strip()
            graph_commit = str(
                graph_status.get("commit")
                or graph_status.get("lastCommit")
                or graph_status.get("last_commit")
                or ""
            ).strip()
            if snapshot_commit and graph_commit and not self._same_git_commit(graph_commit, snapshot_commit):
                return (
                    "GitNexus 图谱不可用于本次 MR：图谱 commit 与 MR 快照 commit 不一致。"
                    f" graph_commit={graph_commit[:12]} snapshot_commit={snapshot_commit[:12]}"
                )
            return ""
        source_ref = self._resolve_existing_git_ref(repo_path, self._mr_source_ref_candidates(subject))
        if not source_ref:
            return (
                "GitNexus 图谱不可用于本次 MR：MR 的 source/head 代码未在本地代码仓中找到。"
                "当前本地仓库很可能停留在目标/dev 分支，继续调用 MCP 会分析合入前代码。"
                f" source_ref={subject.source_ref or ''}"
            )
        source_commit = self._git_commit(repo_path, source_ref)
        graph_commit = str(
            graph_status.get("commit")
            or graph_status.get("lastCommit")
            or graph_status.get("last_commit")
            or ""
        ).strip()
        if not source_commit:
            return (
                "GitNexus 图谱不可用于本次 MR：无法解析 MR source/head commit，"
                "无法确认图谱是否对应待合入代码。"
                f" source_ref={source_ref}"
            )
        if not graph_commit:
            return (
                "GitNexus 图谱不可用于本次 MR：图谱状态缺少 commit，"
                "无法确认图谱是否对应待合入代码。请在 MR source 分支上重新执行 gitnexus analyze。"
            )
        if not self._same_git_commit(graph_commit, source_commit):
            return (
                "GitNexus 图谱不可用于本次 MR：图谱 commit 与 MR source/head commit 不一致。"
                "请先在本地检出 MR source 分支并重新执行 gitnexus analyze，或让系统降级为 diff 候选影响分析。"
                f" graph_commit={graph_commit[:12]} source_commit={source_commit[:12]} source_ref={source_ref}"
            )
        return ""

    def _review_workspace_realtime_graph_enabled(self, runtime: RuntimeSettings | None) -> bool:
        return bool(getattr(runtime, "enable_review_workspace_realtime_graph", False)) if runtime is not None else False

    def _mr_source_ref_candidates(self, subject: ReviewSubject) -> list[str]:
        metadata = dict(subject.metadata or {})
        candidates: list[str] = []
        candidates.extend(str(item or "").strip() for item in list(subject.commits or []))
        candidates.append(str(metadata.get("auto_queue_head_sha") or "").strip())
        candidates.append(str(metadata.get("head_sha") or "").strip())
        candidates.extend(self._ref_aliases(str(subject.source_ref or "").strip()))
        return self._dedupe(candidates)

    def _git_commit(self, repo_path: str, ref: str) -> str:
        if not repo_path or not ref:
            return ""
        try:
            completed = subprocess.run(
                ["git", "rev-parse", ref],
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "").strip()

    def _same_git_commit(self, left: str, right: str) -> bool:
        normalized_left = str(left or "").strip().lower()
        normalized_right = str(right or "").strip().lower()
        if not normalized_left or not normalized_right:
            return False
        return normalized_left == normalized_right or normalized_left.startswith(normalized_right) or normalized_right.startswith(normalized_left)

    def _repository_id(self, subject: ReviewSubject | None, runtime: RuntimeSettings | None) -> str:
        metadata = dict(subject.metadata or {}) if subject is not None else {}
        raw = str(metadata.get("repository_id") or getattr(subject, "repo_id", "") or "").strip()
        if raw:
            return raw
        return str(getattr(runtime, "default_repository_id", "") or "").strip() if runtime is not None else ""

    def _safe_repository_id(self, repository_id: str) -> str:
        return "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(repository_id or "").strip())

    def _resolve_repo_name_from_registry(self, repo_path: str, registry: list[dict[str, Any]] | None = None) -> str:
        registry = registry if registry is not None else self._load_gitnexus_registry()
        if not registry:
            return ""
        repo_path_resolved = _normalize_path_for_compare(repo_path)
        for item in registry:
            if not isinstance(item, dict):
                continue
            candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            candidate_name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
            if candidate_path and candidate_name:
                if _normalize_path_for_compare(candidate_path) == repo_path_resolved:
                    return candidate_name
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
        repo_path_resolved = _normalize_path_for_compare(repo_path)
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
            normalized = _normalize_path_for_compare(candidate_path)
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

    def _ensure_gitnexus_registry_entry(
        self,
        repo_path: str,
        subject: ReviewSubject | None,
        runtime: RuntimeSettings | None,
    ) -> tuple[bool, str, str, str]:
        registry_path = self._gitnexus_registry_path(subject, runtime)
        try:
            payload = read_json(registry_path) if registry_path.exists() else []
            entries, container, key = self._registry_entries_for_update(payload)
            repo_dir = Path(repo_path).expanduser()
            repo_path_resolved = _normalize_path_for_compare(str(repo_dir))
            repo_name = self._registry_repo_name(repo_dir, self._repository_id(subject, runtime), entries)
            entry = self._build_registry_entry(repo_dir, repo_name)
            replaced = False
            for index, item in enumerate(entries):
                candidate_path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
                if candidate_path and _normalize_path_for_compare(candidate_path) == repo_path_resolved:
                    entries[index] = {**item, **entry}
                    replaced = True
                    break
            if not replaced:
                entries.append(entry)
            if isinstance(container, dict):
                container[key] = entries
                next_payload: object = container
            else:
                next_payload = entries
            write_json(registry_path, next_payload)
        except Exception as error:
            logger.exception("gitnexus registry self-heal failed repo_path=%s registry_path=%s", repo_path, registry_path)
            return False, str(registry_path), "", f"{error.__class__.__name__}: {error}"
        registry = self._load_gitnexus_registry(subject, runtime)
        registered = bool(self._resolve_repo_name_from_registry(repo_path, registry))
        return registered, str(registry_path), repo_name, "" if registered else "registry 写入后仍未匹配当前仓库"

    def _registry_entries_for_update(self, payload: object) -> tuple[list[dict[str, Any]], object, str]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)], payload, ""
        if isinstance(payload, dict):
            for key in ("repositories", "repos", "items"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)], dict(payload), key
            container = dict(payload)
            return [], container, "repositories"
        return [], [], ""

    def _registry_repo_name(
        self,
        repo_dir: Path,
        repository_id: str,
        entries: list[dict[str, Any]],
    ) -> str:
        base_candidates = [
            repo_dir.name,
            str(repository_id or "").strip(),
            f"{repo_dir.name}-{str(repository_id or '').strip()}".strip("-"),
        ]
        candidate_paths_by_name: dict[str, set[str]] = {}
        for item in entries:
            name = str(item.get("name") or item.get("repo") or item.get("repo_name") or "").strip()
            path = str(item.get("path") or item.get("repoPath") or item.get("repo_path") or "").strip()
            if name and path:
                candidate_paths_by_name.setdefault(name, set()).add(_normalize_path_for_compare(path))
        repo_path_resolved = _normalize_path_for_compare(str(repo_dir))
        for candidate in [item for item in base_candidates if item]:
            paths = candidate_paths_by_name.get(candidate, set())
            if not paths or paths == {repo_path_resolved}:
                return candidate
        seed = repo_dir.name or str(repository_id or "").strip() or "repository"
        for index in range(2, 100):
            candidate = f"{seed}-{index}"
            if candidate not in candidate_paths_by_name:
                return candidate
        return f"{seed}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"

    def _build_registry_entry(self, repo_dir: Path, repo_name: str) -> dict[str, object]:
        meta = self._load_local_gitnexus_meta(repo_dir)
        indexed_at = str(meta.get("indexedAt") or meta.get("indexed_at") or datetime.now(UTC).isoformat())
        last_commit = str(meta.get("lastCommit") or meta.get("last_commit") or "")
        entry: dict[str, object] = {
            "name": repo_name,
            "path": str(repo_dir),
            "storagePath": str(repo_dir / ".gitnexus"),
            "indexedAt": indexed_at,
            "lastCommit": last_commit,
            "stats": dict(meta.get("stats") or {}),
        }
        remote_url = str(meta.get("remoteUrl") or meta.get("remote_url") or "").strip()
        if remote_url:
            entry["remoteUrl"] = remote_url
        return entry

    def _load_local_gitnexus_meta(self, repo_dir: Path) -> dict[str, object]:
        meta_path = repo_dir / ".gitnexus" / "meta.json"
        if not meta_path.exists():
            return {}
        try:
            payload = read_json(meta_path)
        except Exception:
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def _gitnexus_runtime_env(
        self,
        subject: ReviewSubject | None,
        runtime: RuntimeSettings | None,
    ) -> dict[str, str] | None:
        home_override = self._gitnexus_home(subject, runtime)
        if not home_override:
            env = dict(os.environ)
        else:
            env = dict(os.environ)
            normalized_home = self._normalize_gitnexus_home(home_override)
            env["HOME"] = normalized_home
            env.setdefault("USERPROFILE", normalized_home)
            env.setdefault("GITNEXUS_HOME", normalized_home)
        metadata = dict(subject.metadata or {}) if subject is not None else {}
        explicit_registry = str(metadata.get("gitnexus_registry_path") or os.getenv("GITNEXUS_REGISTRY_PATH") or "").strip()
        if explicit_registry:
            env["GITNEXUS_REGISTRY_PATH"] = str(Path(explicit_registry).expanduser())
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
            return self._gitnexus_registry_path_from_home(home_override)
        return Path.home() / ".gitnexus" / "registry.json"

    def _gitnexus_registry_path_from_home(self, home: str) -> Path:
        home_path = Path(home).expanduser()
        if home_path.name.lower() == ".gitnexus":
            return home_path / "registry.json"
        return home_path / ".gitnexus" / "registry.json"

    def _normalize_gitnexus_home(self, home: str) -> str:
        home_path = Path(home).expanduser()
        if home_path.name.lower() == ".gitnexus":
            return str(home_path.parent)
        return str(home_path)

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
        dynamic_targets = self._dedupe([str(item).strip() for item in list(raw.get("dynamic_targets") or [])])
        impacted_files = self._merge_impact_files(
            fallback.impacted_files,
            self._impact_files_from_gitnexus(detect_changes, impact_results),
        )
        test_scopes = self._merge_test_scopes(
            fallback.recommended_test_scope,
            self._test_scopes_from_gitnexus(detect_changes, impact_results),
        )
        impact_paths = self._impact_paths_from_gitnexus(impact_results, context_results)
        feedback_profiles = self._impact_feedback_profiles()
        impact_paths, impact_feedback_manual_verification = self._apply_impact_feedback_profiles(
            impact_paths,
            feedback_profiles,
        )
        test_scopes = self._apply_impact_feedback_to_test_scopes(test_scopes, impact_paths)
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
        signature_manual_verification = self._signature_change_manual_verification(subject, context_results)
        available_repos = self._dedupe([str(item).strip() for item in list(raw.get("available_repos") or [])])
        graph_degraded = self._is_gitnexus_fact_degraded(
            available_repos=available_repos,
            available_repos_reported="available_repos" in raw,
            queried_targets=queried_targets,
            detect_changes_error=detect_changes_error,
            successful_context_targets=successful_context_targets,
            successful_impact_targets=successful_impact_targets,
            impact_paths=impact_paths,
        )
        graph_status_value = "degraded" if graph_degraded else "ready"
        logger.info(
            "gitnexus normalize report graph_status=%s impacted_files=%s impacted_modules=%s impact_paths=%s impact_graph_nodes=%s impact_graph_edges=%s test_scopes=%s",
            graph_status_value,
            len(impacted_files),
            len(impacted_modules),
            len(impact_paths),
            len(impact_graph.nodes),
            len(impact_graph.edges),
            len(test_scopes),
        )
        return ImpactReport(
            graph_status=graph_status_value,
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
            dynamic_targets=dynamic_targets,
            skipped_invalid_targets=skipped_invalid_targets,
            skipped_missing_context_targets=skipped_missing_context_targets,
            skipped_missing_impact_targets=skipped_missing_impact_targets,
            manual_verification=[
                *impact_feedback_manual_verification,
                *signature_manual_verification,
                *(
                    ["GitNexus MCP 未确认可用仓库或未返回有效调用链，本次影响范围按候选结果处理。"]
                    if graph_degraded
                    else []
                ),
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
                (
                    "GitNexus 图谱查询结果不完整，本次报告已降级为候选影响分析。"
                    if graph_degraded
                    else "GitNexus 图谱已用于本次 MR 关联影响分析。"
                ),
                "当前实现按官方 MCP 流程先查询 list_repos，再调用 detect_changes(scope=all) 和 impact。",
                *(
                    ["GitNexus list_repos 未返回当前可用仓库，请检查 registry/HOME/GITNEXUS_HOME 与 MCP 进程使用的仓库是否一致。"]
                    if graph_degraded and not available_repos
                    else []
                ),
                *(
                    [f"已从 context 结果动态追加影响查询目标：{', '.join(dynamic_targets[:8])}"]
                    if dynamic_targets
                    else []
                ),
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

    def _is_gitnexus_fact_degraded(
        self,
        *,
        available_repos: list[str],
        available_repos_reported: bool,
        queried_targets: list[str],
        detect_changes_error: str,
        successful_context_targets: list[str],
        successful_impact_targets: list[str],
        impact_paths: list[ImpactPath],
    ) -> bool:
        if str(detect_changes_error or "").strip():
            return True
        if available_repos_reported and not available_repos:
            return True
        if queried_targets and not successful_context_targets and not successful_impact_targets:
            return True
        if queried_targets and not impact_paths and not successful_impact_targets:
            return True
        return False

    def _signature_change_manual_verification(
        self,
        subject: ReviewSubject,
        context_results: list[dict[str, Any]],
    ) -> list[str]:
        signature_change = _detect_signature_change(
            target_hunk_excerpt=str(subject.unified_diff or ""),
            context_payload={},
        )
        if not signature_change.get("detected"):
            return []
        summary = str(signature_change.get("summary") or "").strip()
        if not summary:
            return []
        caller_paths = self._context_result_paths(
            context_results,
            keys=("caller_contexts", "callers", "incoming_callers", "incoming"),
        )
        checks = [f"检测到签名级变更：{summary}，请核对调用方入参、返回值和异常契约是否仍兼容。"]
        if caller_paths:
            preview = " / ".join(caller_paths[:4])
            suffix = " 等" if len(caller_paths) > 4 else ""
            checks.append(f"签名变更关联调用方：{preview}{suffix}，请优先确认这些调用点是否已随本次变更同步调整。")
        return checks

    def _context_result_paths(self, context_results: list[dict[str, Any]], *, keys: tuple[str, ...]) -> list[str]:
        paths: list[str] = []
        for payload in context_results:
            for key in keys:
                value = payload.get(key)
                self._append_context_paths(paths, value)
        return self._dedupe(paths)

    def _append_context_paths(self, paths: list[str], value: object) -> None:
        if isinstance(value, dict):
            path = str(value.get("path") or value.get("file_path") or value.get("filePath") or "").strip()
            if path:
                paths.append(path)
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    self._append_context_paths(paths, nested)
            return
        if isinstance(value, list):
            for item in value:
                self._append_context_paths(paths, item)

    def _fallback_report(self, subject: ReviewSubject, runtime: RuntimeSettings | None) -> ImpactReport:
        changed_files = self._changed_files(subject)
        changed_symbols = self._build_changed_symbols(subject, runtime)
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
            return self._enrich_changed_symbols_from_source(repo_path, subject, symbols) if repo_path else symbols
        if str(subject.unified_diff or "").strip():
            mention_symbols = self._extract_symbol_mentions(subject.unified_diff)
            if repo_path:
                source_symbols = self._scan_changed_file_symbols(repo_path, subject)
                if source_symbols:
                    logger.info(
                        "gitnexus symbol extraction used source scan before platform diff mention fallback repo_path=%s changed_symbols=%s",
                        repo_path,
                        len(source_symbols),
                    )
                    return self._dedupe_symbols(source_symbols)
            if mention_symbols:
                logger.info(
                    "gitnexus symbol extraction used platform diff mention fallback changed_symbols=%s",
                    len(mention_symbols),
                )
                return self._dedupe_symbols(mention_symbols)
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

    def _dedupe_symbols(self, symbols: list[ImpactSymbol]) -> list[ImpactSymbol]:
        result: list[ImpactSymbol] = []
        seen: set[tuple[str, str, str]] = set()
        for item in list(symbols or []):
            key = (
                str(item.file_path or "").strip(),
                str(item.container or "").strip(),
                str(item.symbol or "").strip(),
            )
            if not key[2] or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result[:80]

    def _enrich_changed_symbols_from_source(
        self,
        repo_path: str,
        subject: ReviewSubject,
        symbols: list[ImpactSymbol],
    ) -> list[ImpactSymbol]:
        if not repo_path:
            return symbols
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
        return self._dedupe_symbols([*enriched, *source_candidates])

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
            command.extend(["--", *[_git_path(path) for path in changed_files[:40]]])
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
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
        current_old_line = 0
        current_class = ""
        current_old_class = ""
        for line in str(unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[3].removeprefix("b/") if len(parts) >= 4 else ""
                current_new_line = 0
                current_old_line = 0
                current_class = ""
                current_old_class = ""
                continue
            hunk = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                current_old_line = int(hunk.group(1))
                current_new_line = int(hunk.group(2))
                continue
            normalized_line = line[1:] if line[:1] in {"+", "-", " "} else line
            class_match = re.search(r"\b(?:class|interface|enum|record)\s+(\w+)\b", normalized_line)
            if class_match and not line.startswith("+"):
                current_old_class = class_match.group(1)
            if class_match and not line.startswith("-"):
                current_class = class_match.group(1)
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                candidate_line = "+" + line[1:]
                is_removed = line.startswith("-")
                container = current_old_class if is_removed else current_class
                constructor_name = self._java_constructor_name(candidate_line, container)
                if constructor_name:
                    symbols.append(
                        ImpactSymbol(
                            file_path=current_file,
                            symbol=constructor_name,
                            kind="function",
                            container=container,
                            line_start=current_old_line if is_removed else current_new_line,
                        )
                    )
                    continue
                for pattern in self._SYMBOL_PATTERNS:
                    match = pattern.match(candidate_line)
                    if match:
                        symbol_name = match.group(1)
                        kind = self._symbol_kind(candidate_line)
                        if not self._is_symbol_declaration_line(candidate_line, symbol_name, kind):
                            continue
                        symbols.append(
                            ImpactSymbol(
                                file_path=current_file,
                                symbol=symbol_name,
                                kind=kind,
                                container=container if kind == "function" and container and container != symbol_name else "",
                                line_start=current_old_line if is_removed else current_new_line,
                            )
                        )
                        break
            if not line.startswith("+"):
                current_old_line += 1
            if not line.startswith("-"):
                current_new_line += 1
        return self._dedupe_symbols(symbols)

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
        changed_lines_by_file = self._changed_lines_by_file(subject.unified_diff)
        for path in self._changed_files(subject)[:24]:
            content = self._load_file_content(repo_path, source_ref, path)
            if not content:
                content = self._load_file_content(repo_path, target_ref, path)
            if not content:
                content = self._load_worktree_file_content(repo_path, path)
            if not content:
                continue
            file_symbols = self._extract_symbols_from_source(path, content)
            changed_lines = changed_lines_by_file.get(_git_path(path), set())
            if changed_lines:
                file_symbols = self._filter_symbols_by_changed_lines(file_symbols, changed_lines)
            symbols.extend(file_symbols)
        return symbols[:80]

    def _changed_lines_by_file(self, unified_diff: str) -> dict[str, set[int]]:
        changed: dict[str, set[int]] = {}
        current_file = ""
        current_new_line = 0
        for line in str(unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[3].removeprefix("b/") if len(parts) >= 4 else ""
                current_file = _git_path(current_file)
                current_new_line = 0
                if current_file:
                    changed.setdefault(current_file, set())
                continue
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                current_new_line = int(hunk.group(1))
                continue
            if not current_file or not current_new_line:
                continue
            if line.startswith("+") and not line.startswith("+++"):
                changed.setdefault(current_file, set()).add(current_new_line)
            if not line.startswith("-"):
                current_new_line += 1
        return changed

    def _filter_symbols_by_changed_lines(self, symbols: list[ImpactSymbol], changed_lines: set[int]) -> list[ImpactSymbol]:
        if not changed_lines:
            return symbols
        scoped: list[ImpactSymbol] = []
        ordered = sorted(symbols, key=lambda item: int(item.line_start or 0))
        for index, item in enumerate(ordered):
            start = int(item.line_start or 0)
            if start <= 0:
                continue
            next_start = int(ordered[index + 1].line_start or 0) if index + 1 < len(ordered) else 10**9
            if any(start <= line < next_start for line in changed_lines):
                scoped.append(item)
        functions = [item for item in scoped if str(item.kind or "").lower() == "function"]
        return functions or scoped or symbols[:12]

    def _load_file_content(self, repo_path: str, ref: str, file_path: str) -> str:
        if not ref or not file_path:
            return ""
        command = ["git", "show", f"{ref}:{_git_path(file_path)}"]
        try:
            completed = subprocess.run(
                command,
                cwd=repo_path,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "")

    def _load_worktree_file_content(self, repo_path: str, file_path: str) -> str:
        if not repo_path or not file_path:
            return ""
        root = Path(repo_path).expanduser()
        candidate = (root / _git_path(file_path)).resolve()
        try:
            resolved_root = root.resolve()
            if resolved_root not in candidate.parents and candidate != resolved_root:
                return ""
            if not candidate.is_file():
                return ""
            return candidate.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

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
                    encoding="utf-8",
                    errors="replace",
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
            constructor_name = self._java_constructor_name(f"+{stripped}", current_class)
            if constructor_name:
                symbols.append(
                    ImpactSymbol(
                        file_path=file_path,
                        symbol=constructor_name,
                        kind="function",
                        container=current_class,
                        line_start=index,
                    )
                )
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

    def _java_constructor_name(self, line: str, current_class: str) -> str:
        class_name = str(current_class or "").strip()
        if not class_name:
            return ""
        normalized = str(line or "").lstrip("+").strip()
        if not normalized or normalized.startswith(("@", "//", "*")):
            return ""
        pattern = (
            r"^(?:(?:public|private|protected)\s+)?"
            f"{re.escape(class_name)}"
            r"\s*\([^;]*\)\s*(?:throws\s+[\w\s,<>.?]+)?\s*\{?"
        )
        return class_name if re.match(pattern, normalized) else ""

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
                            confidence_label="confirmed",
                            confirmation_reason="GitNexus impact 返回直接影响路径。",
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
                            confidence_label=str(item.get("confidence_label") or item.get("confidence") or "confirmed"),
                            confirmation_reason=str(item.get("confirmation_reason") or "GitNexus impact 返回结构化影响路径。"),
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
                            confidence_label="inferred",
                            confirmation_reason="由 GitNexus affected_processes 汇总推断。",
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
                            confidence_label="inferred",
                            confirmation_reason="由 GitNexus affected_modules 汇总推断。",
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
                                        confidence_label="candidate",
                                        confirmation_reason="由 GitNexus byDepth 候选影响集合推导。",
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
                        confidence_label="candidate",
                        confirmation_reason="由 GitNexus context outgoing 调用关系推导。",
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
                        confidence_label="candidate",
                        confirmation_reason="由 GitNexus context incoming 调用关系推导。",
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

    def _impact_feedback_profiles(self) -> dict[str, Any]:
        if self.storage_root is None:
            return {}
        try:
            return FeedbackLearnerService(Path(self.storage_root)).build_impact_feedback_profiles()
        except Exception as error:
            logger.warning("gitnexus impact feedback profile load failed error=%s", error)
            return {}

    def _apply_impact_feedback_profiles(
        self,
        impact_paths: list[ImpactPath],
        profiles: dict[str, Any],
    ) -> tuple[list[ImpactPath], list[str]]:
        target_profiles = dict(profiles.get("targets") or {}) if isinstance(profiles, dict) else {}
        if not impact_paths or not target_profiles:
            return impact_paths, []
        annotated: list[ImpactPath] = []
        manual_verification: list[str] = []
        for path in impact_paths:
            target_key = self._impact_path_feedback_key(path)
            profile = target_profiles.get(f"impact_path:{target_key}")
            if not isinstance(profile, dict):
                annotated.append(path)
                continue
            action = str(profile.get("recommended_action") or "")
            sample_count = int(profile.get("sample_count") or 0)
            false_positive_rate = float(profile.get("false_positive_rate") or 0.0)
            accept_rate = float(profile.get("accept_rate") or 0.0)
            reason_suffix = (
                f" 历史反馈显示该影响路径误报率 {false_positive_rate}，确认率 {accept_rate}，样本 {sample_count}。"
            )
            if action == "require_manual_verification":
                updated = path.model_copy(
                    update={
                        "confidence_label": "needs_verification",
                        "confirmation_reason": f"{path.confirmation_reason}{reason_suffix}".strip(),
                    }
                )
                manual_verification.append(
                    f"历史影响反馈要求人工复核：{target_key}（误报率 {false_positive_rate}，样本 {sample_count}）。"
                )
            elif action == "boost_confidence":
                updated = path.model_copy(
                    update={
                        "confidence_label": "historically_confirmed",
                        "confirmation_reason": f"{path.confirmation_reason}{reason_suffix}".strip(),
                    }
                )
            else:
                updated = path.model_copy(
                    update={
                        "confirmation_reason": f"{path.confirmation_reason}{reason_suffix}".strip(),
                    }
                )
            annotated.append(updated)
        return sorted(annotated, key=self._impact_path_feedback_sort_key), manual_verification

    def _impact_path_feedback_key(self, path: ImpactPath) -> str:
        values = path.path or [path.source, path.target]
        return " -> ".join([str(item).strip() for item in values if str(item).strip()])

    def _impact_path_feedback_sort_key(self, path: ImpactPath) -> tuple[int, int, int, str]:
        confidence = str(path.confidence_label or "")
        if confidence == "historically_confirmed":
            feedback_rank = 0
        elif confidence == "needs_verification":
            feedback_rank = 3
        else:
            feedback_rank = 1
        risk_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(path.risk or "").lower(), 2)
        return (feedback_rank, risk_rank, int(path.depth or 0), self._impact_path_feedback_key(path))

    def _apply_impact_feedback_to_test_scopes(
        self,
        test_scopes: list[TestScopeRecommendation],
        impact_paths: list[ImpactPath],
    ) -> list[TestScopeRecommendation]:
        needs_verification: dict[str, ImpactPath] = {
            self._impact_path_feedback_key(path): path
            for path in impact_paths
            if str(path.confidence_label or "") == "needs_verification"
        }
        if not test_scopes or not needs_verification:
            return test_scopes
        calibrated: list[TestScopeRecommendation] = []
        for scope in test_scopes:
            matched_key = self._matching_scope_feedback_key(scope, needs_verification)
            if not matched_key:
                calibrated.append(scope)
                continue
            path = needs_verification[matched_key]
            false_positive_match = re.search(r"误报率 ([0-9.]+)", path.confirmation_reason or "")
            false_positive_rate = false_positive_match.group(1) if false_positive_match else "较高"
            suffix = f"关联路径历史误报率 {false_positive_rate}，执行该测试范围前请先人工确认影响路径。"
            reason = str(scope.reason or "").strip()
            calibrated.append(
                scope.model_copy(
                    update={
                        "reason": f"{reason} {suffix}".strip(),
                    }
                )
            )
        return sorted(calibrated, key=self._test_scope_feedback_sort_key)

    def _matching_scope_feedback_key(
        self,
        scope: TestScopeRecommendation,
        needs_verification: dict[str, ImpactPath],
    ) -> str:
        scope_values = " | ".join([str(scope.scope or ""), str(scope.reason or ""), *[str(path) for path in scope.paths]])
        for key in needs_verification:
            if key and key in scope_values:
                return key
            key_nodes = [part.strip() for part in key.split("->") if part.strip()]
            if key_nodes and all(node in scope_values for node in key_nodes):
                return key
        return ""

    def _test_scope_feedback_sort_key(self, scope: TestScopeRecommendation) -> tuple[int, int, str]:
        verification_rank = 1 if "关联路径历史误报率" in str(scope.reason or "") else 0
        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(scope.priority or "").lower(), 2)
        return (verification_rank, priority_rank, str(scope.scope or ""))

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
        java_files = [path for path in changed_files if path.endswith(".java")]
        web_files = [path for path in changed_files if path.endswith((".ts", ".tsx", ".js", ".jsx"))]
        python_files = [path for path in changed_files if path.endswith(".py")]
        for path in java_files[:4]:
            commands.extend(self._java_test_commands_for_path(path))
        if java_files:
            commands.append("相关模块的集成测试")
        for path in web_files[:4]:
            commands.extend(self._web_test_commands_for_path(path))
        for path in python_files[:4]:
            commands.extend(self._python_test_commands_for_path(path))
        if any(self._is_controller_or_api(path) for path in changed_files):
            commands.append("接口契约/API 回归用例")
        if any(self._is_repository_or_sql(path) for path in changed_files):
            commands.append("数据库查询/事务相关回归用例")
        return self._dedupe(commands)

    def _java_test_commands_for_path(self, path: str) -> list[str]:
        normalized = str(path or "").strip().replace("\\", "/")
        class_name = Path(normalized).stem
        test_pattern = f"{class_name}Test"
        module = self._module_path_before_source_root(normalized)
        if module:
            gradle_module = ":" + module.replace("/", ":")
            return [
                f"mvn test -pl {module} -Dtest={test_pattern}",
                f"mvnw.cmd test -pl {module} -Dtest={test_pattern}",
                f"./gradlew {gradle_module}:test --tests \"*{test_pattern}\"",
                f"gradlew.bat {gradle_module}:test --tests \"*{test_pattern}\"",
            ]
        return [
            f"mvn test -Dtest={test_pattern}",
            f"mvnw.cmd test -Dtest={test_pattern}",
            f"./gradlew test --tests \"*{test_pattern}\"",
            f"gradlew.bat test --tests \"*{test_pattern}\"",
        ]

    def _web_test_commands_for_path(self, path: str) -> list[str]:
        normalized = str(path or "").strip().replace("\\", "/")
        candidate = self._candidate_web_test_path(normalized)
        return [
            f"npm test -- {candidate}",
            f"npm run lint -- {normalized}",
        ]

    def _python_test_commands_for_path(self, path: str) -> list[str]:
        normalized = str(path or "").strip().replace("\\", "/")
        candidate = self._candidate_python_test_path(normalized)
        return [f"pytest {candidate}"]

    def _module_path_before_source_root(self, path: str) -> str:
        normalized = str(path or "").strip().replace("\\", "/")
        for marker in ("/src/main/", "/src/test/"):
            if marker in normalized:
                return normalized.split(marker, 1)[0].strip("/")
        return ""

    def _candidate_web_test_path(self, path: str) -> str:
        normalized = str(path or "").strip().replace("\\", "/")
        suffix = Path(normalized).suffix
        stem_path = normalized[: -len(suffix)] if suffix else normalized
        if ".test." in normalized or ".spec." in normalized:
            return normalized
        return f"{stem_path}.test{suffix or '.ts'}"

    def _candidate_python_test_path(self, path: str) -> str:
        normalized = str(path or "").strip().replace("\\", "/")
        if "/tests/" in normalized or Path(normalized).name.startswith("test_"):
            return normalized
        parent = str(Path(normalized).parent).replace("\\", "/")
        name = Path(normalized).name
        if parent in {"", "."}:
            return f"tests/test_{name}"
        return f"tests/{parent}/test_{name}"

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
        head = normalized.lstrip("+").strip()
        prefix = head.split("(", 1)[0].strip()
        tokens = [token for token in prefix.split() if token]
        if normalized.endswith(";") and "->" not in normalized:
            return len(tokens) >= 2 and tokens[-1] == symbol_name
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
            repository = runtime.resolve_repository(
                repository_id=str(metadata.get("repository_id") or subject.repo_id or "").strip(),
                repo_url=subject.repo_url,
                mr_url=subject.mr_url,
            )
            if repository is not None and str(repository.local_path or "").strip():
                return str(repository.local_path or "").strip()
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
