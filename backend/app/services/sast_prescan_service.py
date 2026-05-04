from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


class SastPreScanService:
    """Optional, best-effort SAST/linter pre-scan for changed files."""

    def scan_file(self, repo_root: str | Path, file_path: str, *, enabled: bool = False) -> dict[str, object]:
        if not enabled:
            return {"enabled": False, "summary": "", "findings": [], "limitations": []}
        root = Path(str(repo_root or "")).expanduser()
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        if not normalized_file or not root.exists() or not root.is_dir():
            return {"enabled": False, "summary": "", "findings": [], "limitations": ["本地仓库路径不可用，未执行 SAST 预扫描。"]}
        absolute_path = root / normalized_file
        if not absolute_path.exists() or not absolute_path.is_file():
            return {"enabled": False, "summary": "", "findings": [], "limitations": ["目标文件不存在，未执行 SAST 预扫描。"]}

        scanners = self._candidate_scanners(normalized_file)
        if not scanners:
            return {"enabled": False, "summary": "", "findings": [], "limitations": ["未发现可用 SAST/linter 工具，跳过预扫描。"]}

        findings: list[dict[str, object]] = []
        limitations: list[str] = []
        for scanner in scanners[:2]:
            try:
                findings.extend(scanner(root, normalized_file)[:12])
            except Exception as error:
                limitations.append(f"{getattr(scanner, '__name__', 'scanner')} 执行失败：{error}")
        summary = f"SAST/linter 预扫描命中 {len(findings)} 条候选信号。" if findings else "SAST/linter 预扫描未命中候选信号。"
        return {
            "enabled": True,
            "summary": summary,
            "findings": findings[:12],
            "limitations": limitations[:4],
        }

    def _candidate_scanners(self, file_path: str):
        suffix = Path(file_path).suffix.lower()
        scanners = []
        if shutil.which("semgrep"):
            scanners.append(self._scan_semgrep)
        if suffix in {".js", ".jsx", ".ts", ".tsx"} and shutil.which("eslint"):
            scanners.append(self._scan_eslint)
        if suffix == ".py" and shutil.which("bandit"):
            scanners.append(self._scan_bandit)
        return scanners

    def _scan_semgrep(self, root: Path, file_path: str) -> list[dict[str, object]]:
        config = self._first_existing(
            root,
            [".semgrep.yml", ".semgrep.yaml", "semgrep.yml", "semgrep.yaml"],
        )
        command = ["semgrep", "--json", "--quiet"]
        if config:
            command.extend(["--config", str(config)])
        command.append(file_path)
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = self._loads_json(completed.stdout)
        results = payload.get("results") if isinstance(payload, dict) else []
        findings: list[dict[str, object]] = []
        for item in list(results or []):
            if not isinstance(item, dict):
                continue
            extra = dict(item.get("extra") or {})
            metadata = dict(extra.get("metadata") or {})
            start = dict(item.get("start") or {})
            cwe = self._extract_cwe(metadata.get("cwe") or metadata.get("cwe_id") or metadata.get("cwe-id"))
            message = str(extra.get("message") or "")
            findings.append(
                {
                    "tool": "semgrep",
                    "rule_id": str(item.get("check_id") or ""),
                    "message": message,
                    "severity": str(extra.get("severity") or "").lower(),
                    "cwe": cwe,
                    "why_it_matters": self._why_it_matters(tool="semgrep", message=message, cwe=cwe),
                    "file_path": str(item.get("path") or file_path),
                    "line_start": int(start.get("line") or 1),
                }
            )
        return findings

    def _scan_eslint(self, root: Path, file_path: str) -> list[dict[str, object]]:
        config = self._first_existing(
            root,
            [
                "eslint.config.js",
                "eslint.config.mjs",
                "eslint.config.cjs",
                ".eslintrc.js",
                ".eslintrc.cjs",
                ".eslintrc.json",
                ".eslintrc.yml",
                ".eslintrc.yaml",
            ],
        )
        command = ["eslint", "--format", "json"]
        if config:
            command.extend(["--config", str(config)])
        command.append(file_path)
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = self._loads_json(completed.stdout)
        findings: list[dict[str, object]] = []
        for file_result in list(payload or []):
            if not isinstance(file_result, dict):
                continue
            result_path = str(file_result.get("filePath") or file_path)
            for message in list(file_result.get("messages") or []):
                if not isinstance(message, dict):
                    continue
                rule_id = str(message.get("ruleId") or "")
                message_text = str(message.get("message") or "")
                findings.append(
                    {
                        "tool": "eslint",
                        "rule_id": rule_id,
                        "message": message_text,
                        "severity": "high" if int(message.get("severity") or 0) >= 2 else "medium",
                        "cwe": "",
                        "why_it_matters": self._why_it_matters(tool="eslint", rule_id=rule_id, message=message_text),
                        "file_path": result_path,
                        "line_start": int(message.get("line") or 1),
                    }
                )
        return findings

    def _scan_bandit(self, root: Path, file_path: str) -> list[dict[str, object]]:
        config = self._first_existing(root, [".bandit", "bandit.yml", "bandit.yaml", "pyproject.toml"])
        command = ["bandit", "-q", "-f", "json"]
        if config:
            command.extend(["-c", str(config)])
        command.append(file_path)
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        payload = self._loads_json(completed.stdout)
        results = payload.get("results") if isinstance(payload, dict) else []
        findings: list[dict[str, object]] = []
        for item in list(results or []):
            if not isinstance(item, dict):
                continue
            cwe = self._extract_cwe(item.get("issue_cwe") or item.get("cwe"))
            message = str(item.get("issue_text") or "")
            findings.append(
                {
                    "tool": "bandit",
                    "rule_id": str(item.get("test_id") or ""),
                    "message": message,
                    "severity": str(item.get("issue_severity") or "").lower(),
                    "cwe": cwe,
                    "why_it_matters": self._why_it_matters(tool="bandit", message=message, cwe=cwe),
                    "file_path": str(item.get("filename") or file_path),
                    "line_start": int(item.get("line_number") or 1),
                }
            )
        return findings

    def _extract_cwe(self, value: object) -> str:
        if isinstance(value, dict):
            return self._extract_cwe(value.get("id") or value.get("name") or value.get("cwe"))
        if isinstance(value, list):
            for item in value:
                parsed = self._extract_cwe(item)
                if parsed:
                    return parsed
            return ""
        text = str(value or "").strip()
        if not text:
            return ""
        import re

        match = re.search(r"CWE[-_\s]?(\d+)", text, flags=re.IGNORECASE)
        if match:
            return f"CWE-{match.group(1)}"
        if text.isdigit():
            return f"CWE-{text}"
        return text

    def _why_it_matters(self, *, tool: str, message: str, cwe: str = "", rule_id: str = "") -> str:
        lowered = " ".join([str(message or ""), str(rule_id or ""), str(cwe or "")]).lower()
        if cwe:
            return f"{tool} 将该命中归类到 {cwe}，需要核对是否存在真实可达的数据流或输入边界。"
        if any(token in lowered for token in ["eval", "injection", "sql", "xss", "command"]):
            return "该命中涉及输入进入解释器、查询或命令执行边界，误用时可能形成注入类漏洞。"
        if any(token in lowered for token in ["secret", "password", "token", "credential"]):
            return "该命中涉及凭证或敏感信息暴露，需要确认日志、配置和提交内容是否泄漏。"
        if tool == "eslint":
            return "该命中来自项目 linter，通常表示代码约束、潜在缺陷或团队规范被破坏。"
        return "该命中来自静态扫描工具，应作为专家复核的候选证据，而不是直接结论。"

    def _first_existing(self, root: Path, names: list[str]) -> Path | None:
        for name in names:
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _loads_json(self, value: str) -> Any:
        try:
            return json.loads(str(value or "").strip() or "{}")
        except json.JSONDecodeError:
            return {}
