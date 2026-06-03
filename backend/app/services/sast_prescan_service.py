from __future__ import annotations

import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class SastPreScanService:
    """Optional, best-effort SAST/linter pre-scan for changed files."""

    def scan_file(self, repo_root: str | Path, file_path: str, *, enabled: bool = False) -> dict[str, object]:
        if not enabled:
            return {"enabled": False, "summary": "", "findings": [], "tool_observations": [], "limitations": []}
        root = Path(str(repo_root or "")).expanduser()
        normalized_file = str(file_path or "").strip().replace("\\", "/")
        if not normalized_file or not root.exists() or not root.is_dir():
            return {"enabled": False, "summary": "", "findings": [], "tool_observations": [], "limitations": ["本地仓库路径不可用，未执行 SAST 预扫描。"]}
        absolute_path = root / normalized_file
        if not absolute_path.exists() or not absolute_path.is_file():
            return {"enabled": False, "summary": "", "findings": [], "tool_observations": [], "limitations": ["目标文件不存在，未执行 SAST 预扫描。"]}

        scanners = self._candidate_scanners(root, normalized_file)
        if not scanners:
            return {"enabled": False, "summary": "", "findings": [], "tool_observations": [], "limitations": ["未发现可用 SAST/linter 工具，跳过预扫描。"]}

        findings: list[dict[str, object]] = []
        limitations: list[str] = []
        for scanner in scanners[:6]:
            try:
                findings.extend(scanner(root, normalized_file)[:12])
            except Exception as error:
                limitations.append(f"{getattr(scanner, '__name__', 'scanner')} 执行失败：{error}")
        summary = f"SAST/linter 预扫描命中 {len(findings)} 条候选信号。" if findings else "SAST/linter 预扫描未命中候选信号。"
        return {
            "enabled": True,
            "summary": summary,
            "findings": findings[:12],
            "tool_observations": self._as_tool_observations(findings[:12]),
            "limitations": limitations[:4],
        }

    def _candidate_scanners(self, root: Path, file_path: str):
        suffix = Path(file_path).suffix.lower()
        scanners = []
        if shutil.which("semgrep"):
            scanners.append(self._scan_semgrep)
        if suffix == ".java" and shutil.which("pmd"):
            scanners.append(self._scan_pmd)
        if suffix == ".java" and shutil.which("checkstyle"):
            scanners.append(self._scan_checkstyle)
        if suffix == ".java" and self._has_any_report(
            root,
            [
                "target/spotbugsXml.xml",
                "target/spotbugs.xml",
                "target/site/spotbugs.xml",
                "build/reports/spotbugs/main.xml",
                "build/reports/spotbugs/test.xml",
                "spotbugs.xml",
            ],
        ):
            scanners.append(self._scan_spotbugs_reports)
        if suffix == ".java" and (
            (root / "target" / "surefire-reports").exists()
            or (root / "target" / "failsafe-reports").exists()
            or (root / "build" / "test-results").exists()
        ):
            scanners.append(self._scan_archunit_reports)
        if suffix == ".java" and self._has_any_report(
            root,
            [
                "target/site/jacoco/jacoco.xml",
                "target/site/jacoco-aggregate/jacoco.xml",
                "build/reports/jacoco/test/jacocoTestReport.xml",
                "build/reports/jacoco/testCodeCoverageReport/testCodeCoverageReport.xml",
                "jacoco.xml",
            ],
        ):
            scanners.append(self._scan_jacoco_reports)
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
            encoding="utf-8",
            errors="replace",
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

    def _scan_pmd(self, root: Path, file_path: str) -> list[dict[str, object]]:
        command = ["pmd", "check", "-d", file_path, "-f", "json"]
        config = self._first_existing(root, ["pmd-ruleset.xml", ".pmd.xml", "ruleset.xml"])
        if config:
            command.extend(["-R", str(config)])
        completed = subprocess.run(
            command,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        payload = self._loads_json(completed.stdout)
        findings: list[dict[str, object]] = []
        files_payload = payload.get("files") if isinstance(payload, dict) else []
        for file_item in list(files_payload or []):
            if not isinstance(file_item, dict):
                continue
            result_path = str(file_item.get("filename") or file_path)
            for violation in list(file_item.get("violations") or []):
                if not isinstance(violation, dict):
                    continue
                rule_id = str(violation.get("rule") or violation.get("ruleName") or "").strip()
                message = str(violation.get("description") or violation.get("message") or "").strip()
                priority = int(violation.get("priority") or 3)
                findings.append(
                    {
                        "tool": "pmd",
                        "rule_id": rule_id,
                        "message": message,
                        "severity": "high" if priority <= 2 else "medium" if priority == 3 else "low",
                        "cwe": "",
                        "why_it_matters": self._why_it_matters(tool="pmd", rule_id=rule_id, message=message),
                        "file_path": result_path,
                        "line_start": int(violation.get("beginline") or violation.get("line") or 1),
                    }
                )
        return findings

    def _scan_checkstyle(self, root: Path, file_path: str) -> list[dict[str, object]]:
        config = self._first_existing(
            root,
            ["checkstyle.xml", "config/checkstyle/checkstyle.xml", "google_checks.xml", "sun_checks.xml"],
        )
        if not config:
            return []
        completed = subprocess.run(
            ["checkstyle", "-c", str(config), "-f", "xml", file_path],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        try:
            root_xml = ET.fromstring(completed.stdout.strip() or "<checkstyle />")
        except ET.ParseError:
            return []
        findings: list[dict[str, object]] = []
        for file_node in root_xml.findall("file"):
            result_path = str(file_node.get("name") or file_path)
            if result_path.startswith(str(root)):
                result_path = str(Path(result_path).relative_to(root))
            for error in file_node.findall("error"):
                source = str(error.get("source") or "").strip()
                rule_id = source.rsplit(".", 1)[-1] if source else "checkstyle"
                message = str(error.get("message") or "").strip()
                severity = str(error.get("severity") or "warning").lower()
                findings.append(
                    {
                        "tool": "checkstyle",
                        "rule_id": rule_id,
                        "message": message,
                        "severity": "high" if severity == "error" else "medium",
                        "cwe": "",
                        "why_it_matters": self._why_it_matters(tool="checkstyle", rule_id=rule_id, message=message),
                        "file_path": result_path,
                        "line_start": int(error.get("line") or 1),
                    }
                )
        return findings

    def _scan_spotbugs_reports(self, root: Path, file_path: str) -> list[dict[str, object]]:
        report_paths = self._existing_report_paths(
            root,
            [
                "target/spotbugsXml.xml",
                "target/spotbugs.xml",
                "target/site/spotbugs.xml",
                "build/reports/spotbugs/main.xml",
                "build/reports/spotbugs/test.xml",
                "spotbugs.xml",
            ],
        )
        findings: list[dict[str, object]] = []
        for report in report_paths[:3]:
            try:
                root_xml = ET.fromstring(report.read_text(encoding="utf-8", errors="replace"))
            except ET.ParseError:
                continue
            for bug in root_xml.findall(".//BugInstance"):
                source_line = bug.find(".//SourceLine")
                if source_line is None or not self._report_entry_matches_file(file_path, source_line):
                    continue
                rule_id = str(bug.get("type") or bug.get("category") or "spotbugs").strip()
                priority = int(str(bug.get("priority") or "3").strip() or 3)
                message = self._first_child_text(bug, "LongMessage") or self._first_child_text(bug, "ShortMessage") or rule_id
                findings.append(
                    {
                        "tool": "spotbugs",
                        "rule_id": rule_id,
                        "message": message,
                        "severity": "high" if priority <= 2 else "medium" if priority == 3 else "low",
                        "cwe": "",
                        "why_it_matters": self._why_it_matters(tool="spotbugs", rule_id=rule_id, message=message),
                        "file_path": file_path,
                        "line_start": int(source_line.get("start") or source_line.get("line") or 1),
                    }
                )
        return findings

    def _scan_archunit_reports(self, root: Path, file_path: str) -> list[dict[str, object]]:
        reports = list((root / "target" / "surefire-reports").glob("*.xml"))
        reports.extend((root / "target" / "failsafe-reports").glob("*.xml"))
        reports.extend((root / "build" / "test-results").glob("**/*.xml"))
        class_name = Path(file_path).stem
        findings: list[dict[str, object]] = []
        for report in reports[:20]:
            try:
                root_xml = ET.fromstring(report.read_text(encoding="utf-8", errors="replace"))
            except ET.ParseError:
                continue
            for testcase in root_xml.findall(".//testcase"):
                failure = testcase.find("failure")
                if failure is None:
                    failure = testcase.find("error")
                failure_message = str((failure.get("message") if failure is not None else "") or "")
                blob = " ".join(
                    [
                        str(testcase.get("classname") or ""),
                        str(testcase.get("name") or ""),
                        failure_message,
                        "".join(testcase.itertext()),
                    ]
                )
                lowered = blob.lower()
                if "archunit" not in lowered and "archrule" not in lowered and "architecture" not in lowered:
                    continue
                if class_name and class_name not in blob and file_path not in blob:
                    continue
                message = str(failure_message or blob).strip()
                findings.append(
                    {
                        "tool": "archunit",
                        "rule_id": str(testcase.get("name") or "archunit-rule").strip(),
                        "message": self._compact_text(message, 360),
                        "severity": "high",
                        "cwe": "",
                        "why_it_matters": "该命中来自项目架构测试，通常表示分层依赖、包依赖方向或 DDD 边界被破坏，应由 DDD 架构专家复核。",
                        "file_path": file_path,
                        "line_start": 1,
                    }
                )
        return findings

    def _scan_jacoco_reports(self, root: Path, file_path: str) -> list[dict[str, object]]:
        reports = self._existing_report_paths(
            root,
            [
                "target/site/jacoco/jacoco.xml",
                "target/site/jacoco-aggregate/jacoco.xml",
                "build/reports/jacoco/test/jacocoTestReport.xml",
                "build/reports/jacoco/testCodeCoverageReport/testCodeCoverageReport.xml",
                "jacoco.xml",
            ],
        )
        source_name = Path(file_path).name
        findings: list[dict[str, object]] = []
        for report in reports[:3]:
            try:
                root_xml = ET.fromstring(report.read_text(encoding="utf-8", errors="replace"))
            except ET.ParseError:
                continue
            for source_file in root_xml.findall(".//sourcefile"):
                if str(source_file.get("name") or "") != source_name:
                    continue
                missed_lines = [
                    int(line.get("nr") or 0)
                    for line in source_file.findall("line")
                    if int(line.get("mi") or 0) > 0 and int(line.get("ci") or 0) <= 0
                ]
                if not missed_lines:
                    continue
                findings.append(
                    {
                        "tool": "jacoco",
                        "rule_id": "uncovered-lines",
                        "message": f"JaCoCo 显示该文件仍有 {len(missed_lines)} 行未覆盖，示例行号：{missed_lines[:5]}。",
                        "severity": "medium",
                        "cwe": "",
                        "why_it_matters": "该命中来自覆盖率报告，只能说明测试保护可能不足，应由测试验证专家结合本次变更风险判断是否需要补测试。",
                        "file_path": file_path,
                        "line_start": missed_lines[0],
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
            encoding="utf-8",
            errors="replace",
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
            encoding="utf-8",
            errors="replace",
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
            return f"{tool} 将该命中归类到 {cwe}，应核对是否存在真实可达的数据流或输入边界。"
        if any(token in lowered for token in ["eval", "injection", "sql", "xss", "command"]):
            return "该命中涉及输入进入解释器、查询或命令执行边界，误用时可能形成注入类漏洞。"
        if any(token in lowered for token in ["secret", "password", "token", "credential"]):
            return "该命中涉及凭证或敏感信息暴露，应检查日志、配置和提交内容是否泄漏。"
        if tool == "eslint":
            return "该命中来自项目 linter，通常表示代码约束、潜在缺陷或团队规范被破坏。"
        return "该命中来自静态扫描工具，应作为专家复核的候选证据，而不是直接结论。"

    def _first_existing(self, root: Path, names: list[str]) -> Path | None:
        for name in names:
            candidate = root / name
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def _existing_report_paths(self, root: Path, names: list[str]) -> list[Path]:
        return [candidate for name in names if (candidate := root / name).exists() and candidate.is_file()]

    def _has_any_report(self, root: Path, names: list[str]) -> bool:
        return any((root / name).exists() and (root / name).is_file() for name in names)

    def _report_entry_matches_file(self, file_path: str, node: ET.Element) -> bool:
        source_path = str(node.get("sourcepath") or node.get("relSourcepath") or node.get("path") or "").replace("\\", "/")
        source_file = str(node.get("sourcefile") or node.get("file") or "").replace("\\", "/")
        normalized = file_path.replace("\\", "/")
        return bool(
            (source_path and (source_path == normalized or source_path.endswith("/" + normalized)))
            or (source_file and source_file == Path(normalized).name)
        )

    def _first_child_text(self, node: ET.Element, child_name: str) -> str:
        child = node.find(child_name)
        return str(child.text or "").strip() if child is not None else ""

    def _compact_text(self, value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."

    def _loads_json(self, value: str) -> Any:
        try:
            return json.loads(str(value or "").strip() or "{}")
        except json.JSONDecodeError:
            return {}

    def _as_tool_observations(self, findings: list[dict[str, object]]) -> list[dict[str, object]]:
        observations: list[dict[str, object]] = []
        for item in findings:
            observation = dict(item)
            tool = str(observation.get("tool") or "tool").strip()
            rule_id = str(observation.get("rule_id") or "unknown").strip()
            line_start = int(observation.get("line_start") or 1)
            observation["observation_id"] = f"{tool}:{rule_id}:{line_start}"
            observation["source"] = "sast_prescan"
            observation["observation_type"] = "tool_observation"
            observation["category"] = self._category_for_tool(
                tool,
                str(observation.get("message") or ""),
                rule_id,
                str(observation.get("cwe") or ""),
            )
            observation["confidence"] = self._confidence_for_tool_signal(tool, str(observation.get("severity") or ""))
            observation["evidence_required"] = [
                "必须确认该工具信号落在本次 diff 或受影响上下文中。",
                "必须结合专家通用规范或绑定规范判断是否形成真实风险。",
                "必须给出精确代码位置和直接代码证据后才能升级为正式问题。",
            ]
            observation["is_issue"] = False
            observation["expert_must_decide"] = True
            observations.append(observation)
        return observations

    def _category_for_tool(self, tool: str, message: str, rule_id: str, cwe: str = "") -> str:
        lowered = " ".join([tool, message, rule_id, cwe]).lower()
        if str(cwe or "").strip():
            return "security"
        if any(token in lowered for token in ["sql", "xss", "ssrf", "csrf", "injection", "secret", "password", "token", "auth", "crypto"]):
            return "security"
        if tool in {"spotbugs", "pmd", "checkstyle"}:
            return "java_quality"
        if tool == "archunit":
            return "architecture"
        if tool == "jacoco":
            return "test_coverage"
        if tool == "bandit":
            return "python_security"
        if tool == "eslint":
            return "frontend_quality"
        return "static_analysis"

    def _confidence_for_tool_signal(self, tool: str, severity: str) -> float:
        normalized = str(severity or "").lower()
        base = 0.78 if tool in {"semgrep", "spotbugs", "bandit"} else 0.7
        if normalized in {"error", "critical", "high"}:
            return min(0.9, base + 0.08)
        if normalized in {"medium", "warning", "warn"}:
            return base
        if normalized == "low":
            return max(0.55, base - 0.12)
        return base
