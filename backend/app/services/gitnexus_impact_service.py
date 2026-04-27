from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.domain.models.report import ImpactFile, ImpactPath, ImpactReport, ImpactSymbol, TestScopeRecommendation
from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json


class GitNexusImpactClient(Protocol):
    """GitNexus 图谱查询客户端。"""

    def analyze_mr(
        self,
        *,
        repo_name: str,
        repo_path: str,
        subject: ReviewSubject,
        changed_symbols: list[ImpactSymbol],
    ) -> dict[str, Any]:
        """基于已建好的 GitNexus 图谱分析本次 MR 影响。"""


class GitNexusMcpImpactClient:
    """通过 GitNexus MCP stdio server 查询已建图谱。

    GitNexus 官方推荐的本地 MCP 启动命令是：

    `npx -y gitnexus@latest mcp`

    这里使用 MCP JSON-RPC stdio 协议调用 `impact` 和 `detect_changes`。
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
    ) -> dict[str, Any]:
        command = self._command()
        requests: list[dict[str, Any]] = [
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "multi-codereview-agent", "version": "0.1.0"},
                },
            },
            {"method": "notifications/initialized", "params": {}},
            {
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "detect_changes",
                    "arguments": {
                        "repo": repo_name,
                        "scope": "all",
                        "baseRef": subject.target_ref,
                        "headRef": subject.source_ref,
                        "changedFiles": list(subject.changed_files),
                    },
                },
            },
        ]
        next_id = 3
        for symbol in changed_symbols[:8]:
            if not symbol.symbol:
                continue
            requests.append(
                {
                    "id": next_id,
                    "method": "tools/call",
                    "params": {
                        "name": "impact",
                        "arguments": {
                            "repo": repo_name,
                            "target": symbol.symbol,
                            "direction": "upstream",
                            "maxDepth": 3,
                            "minConfidence": 0.6,
                            "includeTests": True,
                        },
                    },
                }
            )
            next_id += 1
        responses = self._call_mcp(command, repo_path, requests)
        return {
            "repo": repo_name,
            "detect_changes": self._tool_payload(responses.get(2)),
            "impact_results": [
                self._tool_payload(responses.get(request["id"]))
                for request in requests
                if isinstance(request.get("id"), int) and int(request["id"]) >= 3
            ],
            "raw_response_count": len(responses),
        }

    def _command(self) -> list[str]:
        raw = str(os.getenv("GITNEXUS_MCP_COMMAND") or "").strip()
        if raw:
            return raw.split()
        return ["npx", "-y", "gitnexus@latest", "mcp"]

    def _call_mcp(
        self,
        command: list[str],
        repo_path: str,
        requests: list[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        payload = b"".join(self._encode_message(request) for request in requests)
        completed = subprocess.run(
            command,
            input=payload,
            cwd=repo_path,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="ignore")[-800:]
            raise RuntimeError(f"GitNexus MCP 调用失败: {stderr or completed.returncode}")
        return self._decode_messages(completed.stdout)

    def _encode_message(self, message: dict[str, Any]) -> bytes:
        body = json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False).encode("utf-8")
        return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    def _decode_messages(self, data: bytes) -> dict[int, dict[str, Any]]:
        responses: dict[int, dict[str, Any]] = {}
        index = 0
        while index < len(data):
            header_end = data.find(b"\r\n\r\n", index)
            if header_end < 0:
                break
            headers = data[index:header_end].decode("ascii", errors="ignore")
            length = 0
            for line in headers.splitlines():
                if line.lower().startswith("content-length:"):
                    length = int(line.split(":", 1)[1].strip())
                    break
            body_start = header_end + 4
            body_end = body_start + length
            if length <= 0 or body_end > len(data):
                break
            try:
                message = json.loads(data[body_start:body_end].decode("utf-8"))
            except json.JSONDecodeError:
                index = body_end
                continue
            message_id = message.get("id")
            if isinstance(message_id, int):
                responses[message_id] = message
            index = body_end
        return responses

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
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"text": text}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        return result


class GitNexusImpactService:
    """生成每个 MR 的关联影响报告。

    第一阶段先定义稳定边界：如果 subject metadata 已经写入 GitNexus 分析结果，
    直接标准化为 ImpactReport；否则基于 diff 和路径规则生成降级报告。
    后续后台定时建图只需要把 GitNexus 输出写入同一份 metadata 或替换本服务内部
    的真实调用实现，不影响报告模型和前端。
    """

    _SYMBOL_PATTERNS = [
        re.compile(r"^\+\s*(?:public|private|protected)?\s*(?:static\s+)?[\w<>\[\], ?]+\s+(\w+)\s*\([^;]*\)\s*\{?"),
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
        """返回标准化影响报告，确保 GitNexus 不可用时也有测试建议。"""

        cached = self._cached_gitnexus_report(subject)
        if cached is not None:
            return cached
        graph_report = self._gitnexus_graph_report(subject, runtime)
        if graph_report is not None:
            return graph_report
        return self._fallback_report(subject, runtime)

    def _cached_gitnexus_report(self, subject: ReviewSubject) -> ImpactReport | None:
        metadata = dict(subject.metadata or {})
        raw = metadata.get("gitnexus_impact_report") or metadata.get("impact_report")
        if not isinstance(raw, dict):
            return None
        payload = dict(raw)
        payload.setdefault("changed_files", list(subject.changed_files))
        payload.setdefault("graph_status", "ready")
        return ImpactReport.model_validate(payload)

    def _gitnexus_graph_report(self, subject: ReviewSubject, runtime: RuntimeSettings | None) -> ImpactReport | None:
        repo_path = self._repo_path(subject, runtime)
        if not repo_path:
            return None
        graph_status = self._load_graph_status(repo_path)
        if str(graph_status.get("state") or "") != "ready":
            return None
        changed_symbols = self._extract_changed_symbols(subject.unified_diff)
        try:
            raw = self._mcp_client.analyze_mr(
                repo_name=str(graph_status.get("repo_name") or Path(repo_path).name),
                repo_path=repo_path,
                subject=subject,
                changed_symbols=changed_symbols,
            )
        except Exception as error:
            fallback = self._fallback_report(subject, runtime)
            return fallback.model_copy(
                update={
                    "graph_status": "fallback",
                    "graph_indexed_at": str(graph_status.get("indexed_at") or graph_status.get("updated_at") or ""),
                    "graph_commit": str(graph_status.get("commit") or ""),
                    "limitations": [
                        f"GitNexus 图谱已就绪，但本次 MCP 影响分析调用失败：{error}",
                        *fallback.limitations,
                    ],
                }
            )
        return self._normalize_gitnexus_payload(subject, runtime, graph_status, raw, changed_symbols)

    def _load_graph_status(self, repo_path: str) -> dict[str, Any]:
        candidates: list[Path] = []
        if self.storage_root is not None:
            candidates.append(self.storage_root / "gitnexus" / "index_status.json")
        candidates.append(Path(repo_path) / ".gitnexus" / "index_status.json")
        for path in candidates:
            if not path.exists():
                continue
            try:
                status = read_json(path)
            except Exception:
                continue
            if isinstance(status, dict):
                return status
        if (Path(repo_path) / ".gitnexus").exists():
            return {
                "state": "ready",
                "repo_path": repo_path,
                "repo_name": Path(repo_path).name,
                "updated_at": "",
            }
        return {}

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
        impacted_files = self._merge_impact_files(
            fallback.impacted_files,
            self._impact_files_from_gitnexus(detect_changes, impact_results),
        )
        test_scopes = self._merge_test_scopes(
            fallback.recommended_test_scope,
            self._test_scopes_from_gitnexus(detect_changes, impact_results),
        )
        impact_paths = self._impact_paths_from_gitnexus(impact_results)
        risk_level = str(detect_changes.get("risk_level") or detect_changes.get("riskLevel") or fallback.risk_level)
        return ImpactReport(
            graph_status="ready",
            graph_indexed_at=str(graph_status.get("indexed_at") or graph_status.get("updated_at") or ""),
            graph_commit=str(graph_status.get("commit") or ""),
            changed_files=self._dedupe(
                [
                    *fallback.changed_files,
                    *[str(item) for item in list(detect_changes.get("changed_files") or [])],
                ]
            ),
            changed_symbols=changed_symbols or fallback.changed_symbols,
            impacted_files=impacted_files,
            impacted_modules=self._dedupe(
                [
                    *fallback.impacted_modules,
                    *[str(item) for item in list(detect_changes.get("affected_processes") or [])],
                    *[str(item) for item in list(detect_changes.get("affectedProcesses") or [])],
                ]
            ),
            impact_paths=impact_paths,
            external_entrypoints=fallback.external_entrypoints,
            risk_level=risk_level if risk_level in {"low", "medium", "high", "critical"} else fallback.risk_level,
            recommended_test_scope=test_scopes,
            must_run_tests=fallback.must_run_tests,
            manual_verification=[
                "本次关联影响分析已使用 GitNexus 图谱，请优先核对受影响流程和测试范围。",
                *fallback.manual_verification,
            ],
            limitations=[
                "GitNexus 图谱已用于本次 MR 关联影响分析。",
                "如本次 MR diff 尚未同步到本地仓工作区，GitNexus detect_changes 可能只能覆盖符号级 impact 结果。",
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

    def _extract_changed_symbols(self, unified_diff: str) -> list[ImpactSymbol]:
        symbols: list[ImpactSymbol] = []
        current_file = ""
        current_new_line = 0
        for line in str(unified_diff or "").splitlines():
            if line.startswith("diff --git "):
                parts = line.split()
                current_file = parts[3].removeprefix("b/") if len(parts) >= 4 else ""
                current_new_line = 0
                continue
            hunk = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if hunk:
                current_new_line = int(hunk.group(1))
                continue
            if line.startswith("+") and not line.startswith("+++"):
                for pattern in self._SYMBOL_PATTERNS:
                    match = pattern.match(line)
                    if match:
                        symbols.append(
                            ImpactSymbol(
                                file_path=current_file,
                                symbol=match.group(1),
                                kind=self._symbol_kind(line),
                                line_start=current_new_line,
                            )
                        )
                        break
            if not line.startswith("-"):
                current_new_line += 1
        return symbols[:80]

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

    def _impact_paths_from_gitnexus(self, impact_results: list[dict[str, Any]]) -> list[ImpactPath]:
        paths: list[ImpactPath] = []
        for result in impact_results:
            for item in list(result.get("impact_paths") or result.get("impactPaths") or result.get("paths") or []):
                if isinstance(item, list):
                    values = [str(value) for value in item if str(value).strip()]
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
                    values = [str(value) for value in list(item.get("path") or item.get("nodes") or []) if str(value).strip()]
                    paths.append(
                        ImpactPath(
                            source=str(item.get("source") or (values[0] if values else "")),
                            target=str(item.get("target") or (values[-1] if values else "")),
                            path=values,
                            depth=int(item.get("depth") or max(len(values) - 1, 0)),
                            risk=str(item.get("risk") or item.get("risk_level") or "medium"),
                        )
                    )
        return paths[:80]

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
