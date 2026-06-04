from pathlib import Path
from unittest.mock import patch

from app.services.sast_prescan_service import SastPreScanService


def test_sast_prescan_is_disabled_by_default(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hello')\n", encoding="utf-8")

    with patch("app.services.sast_prescan_service.shutil.which") as which:
        payload = SastPreScanService().scan_file(repo, "src/app.py")

    assert payload["enabled"] is False
    assert payload["findings"] == []
    assert payload["tool_observations"] == []
    assert payload["summary"] == ""
    which.assert_not_called()


def test_sast_prescan_skips_when_tools_are_unavailable(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("print('hello')\n", encoding="utf-8")

    with patch("app.services.sast_prescan_service.shutil.which", return_value=None):
        payload = SastPreScanService().scan_file(repo, "src/app.py", enabled=True)

    assert payload["enabled"] is False
    assert payload["findings"] == []
    assert payload["tool_observations"] == []
    assert "未发现可用" in payload["limitations"][0]


def test_sast_prescan_skips_java_when_no_tools_or_reports_are_available(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}\n", encoding="utf-8")

    with patch("app.services.sast_prescan_service.shutil.which", return_value=None):
        payload = SastPreScanService().scan_file(repo, "src/main/java/app/OrderService.java", enabled=True)

    assert payload["enabled"] is False
    assert payload["findings"] == []
    assert payload["tool_observations"] == []
    assert "未发现可用" in payload["limitations"][0]


def test_sast_prescan_parses_semgrep_json(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("eval(user_input)\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        assert command[:3] == ["semgrep", "--json", "--quiet"]
        return type(
            "Completed",
            (),
            {
                "stdout": (
                    '{"results":[{"check_id":"python.lang.security.audit.eval",'
                    '"path":"src/app.py","start":{"line":1},'
                    '"extra":{"message":"Use of eval","severity":"ERROR",'
                    '"metadata":{"cwe":["CWE-95"]}}}]}'
                ),
                "stderr": "",
                "returncode": 1,
            },
        )()

    with patch("app.services.sast_prescan_service.shutil.which", side_effect=lambda name: "/usr/bin/semgrep" if name == "semgrep" else None):
        with patch("app.services.sast_prescan_service.subprocess.run", side_effect=fake_run):
            payload = SastPreScanService().scan_file(repo, "src/app.py", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "semgrep"
    assert payload["findings"][0]["rule_id"] == "python.lang.security.audit.eval"
    assert payload["findings"][0]["cwe"] == "CWE-95"
    assert "CWE-95" in payload["findings"][0]["why_it_matters"]
    assert payload["tool_observations"][0]["source"] == "sast_prescan"
    assert payload["tool_observations"][0]["observation_type"] == "tool_observation"
    assert payload["tool_observations"][0]["id"] == "sast:semgrep:python.lang.security.audit.eval:src/app.py:1"
    assert payload["tool_observations"][0]["observation_id"] == "sast:semgrep:python.lang.security.audit.eval:src/app.py:1"
    assert payload["tool_observations"][0]["category"] == "security"
    assert payload["tool_observations"][0]["confidence"] >= 0.8
    assert payload["tool_observations"][0]["evidence_required"]
    assert payload["tool_observations"][0]["is_issue"] is False
    assert payload["tool_observations"][0]["expert_must_decide"] is True
    assert "Use of eval" in payload["summary"] or payload["findings"][0]["message"] == "Use of eval"


def test_sast_prescan_uses_project_semgrep_config(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("eval(user_input)\n", encoding="utf-8")
    (repo / ".semgrep.yml").write_text("rules: []\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        assert "--config" in command
        assert str(repo / ".semgrep.yml") in command
        return type("Completed", (), {"stdout": '{"results":[]}', "stderr": "", "returncode": 0})()

    with patch("app.services.sast_prescan_service.shutil.which", side_effect=lambda name: "/usr/bin/semgrep" if name == "semgrep" else None):
        with patch("app.services.sast_prescan_service.subprocess.run", side_effect=fake_run):
            payload = SastPreScanService().scan_file(repo, "src/app.py", enabled=True)

    assert payload["enabled"] is True


def test_sast_prescan_parses_pmd_json_for_java(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        assert command[:4] == ["pmd", "check", "-d", "src/main/java/app/OrderService.java"]
        assert "-f" in command and "json" in command
        return type(
            "Completed",
            (),
            {
                "stdout": (
                    '{"files":[{"filename":"src/main/java/app/OrderService.java",'
                    '"violations":[{"beginline":12,"rule":"EmptyCatchBlock",'
                    '"priority":2,"description":"Avoid empty catch blocks"}]}]}'
                ),
                "stderr": "",
                "returncode": 4,
            },
        )()

    with patch(
        "app.services.sast_prescan_service.shutil.which",
        side_effect=lambda name: "/usr/bin/pmd" if name == "pmd" else None,
    ):
        with patch("app.services.sast_prescan_service.subprocess.run", side_effect=fake_run):
            payload = SastPreScanService().scan_file(repo, "src/main/java/app/OrderService.java", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "pmd"
    assert payload["findings"][0]["rule_id"] == "EmptyCatchBlock"
    assert payload["findings"][0]["severity"] == "high"
    assert payload["tool_observations"][0]["is_issue"] is False


def test_sast_prescan_parses_checkstyle_xml_for_java(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}\n", encoding="utf-8")
    (repo / "checkstyle.xml").write_text("<module name=\"Checker\" />\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None, **kwargs):
        assert command[:4] == ["checkstyle", "-c", str(repo / "checkstyle.xml"), "-f"]
        return type(
            "Completed",
            (),
            {
                "stdout": (
                    '<checkstyle><file name="src/main/java/app/OrderService.java">'
                    '<error line="7" severity="warning" message="Name must match pattern" '
                    'source="com.puppycrawl.tools.checkstyle.checks.naming.MethodNameCheck"/>'
                    "</file></checkstyle>"
                ),
                "stderr": "",
                "returncode": 1,
            },
        )()

    with patch(
        "app.services.sast_prescan_service.shutil.which",
        side_effect=lambda name: "/usr/bin/checkstyle" if name == "checkstyle" else None,
    ):
        with patch("app.services.sast_prescan_service.subprocess.run", side_effect=fake_run):
            payload = SastPreScanService().scan_file(repo, "src/main/java/app/OrderService.java", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "checkstyle"
    assert payload["findings"][0]["rule_id"] == "MethodNameCheck"
    assert payload["findings"][0]["line_start"] == 7


def test_sast_prescan_parses_spotbugs_report_for_java(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}\n", encoding="utf-8")
    report = repo / "target/spotbugsXml.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """
        <BugCollection>
          <BugInstance type="NP_NULL_ON_SOME_PATH" priority="2">
            <LongMessage>Possible null pointer dereference in OrderService.load</LongMessage>
            <SourceLine sourcepath="src/main/java/app/OrderService.java" start="18" />
          </BugInstance>
        </BugCollection>
        """,
        encoding="utf-8",
    )

    with patch("app.services.sast_prescan_service.shutil.which", return_value=None):
        payload = SastPreScanService().scan_file(repo, "src/main/java/app/OrderService.java", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "spotbugs"
    assert payload["findings"][0]["rule_id"] == "NP_NULL_ON_SOME_PATH"
    assert payload["findings"][0]["severity"] == "high"
    assert payload["tool_observations"][0]["expert_must_decide"] is True


def test_sast_prescan_parses_archunit_report_for_java(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/domain/OrderAggregate.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderAggregate {}\n", encoding="utf-8")
    report = repo / "target/surefire-reports/TEST-app.ArchitectureTest.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """
        <testsuite>
          <testcase classname="app.ArchitectureTest" name="ArchUnit domain layer should not access repository">
            <failure message="ArchRule violated: OrderAggregate depends on repository" />
          </testcase>
        </testsuite>
        """,
        encoding="utf-8",
    )

    with patch("app.services.sast_prescan_service.shutil.which", return_value=None):
        payload = SastPreScanService().scan_file(repo, "src/main/java/app/domain/OrderAggregate.java", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "archunit"
    assert payload["findings"][0]["severity"] == "high"
    assert "DDD 架构专家" in payload["findings"][0]["why_it_matters"]


def test_sast_prescan_parses_jacoco_report_for_java(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/main/java/app/OrderService.java"
    target.parent.mkdir(parents=True)
    target.write_text("class OrderService {}\n", encoding="utf-8")
    report = repo / "target/site/jacoco/jacoco.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        """
        <report>
          <package name="app">
            <sourcefile name="OrderService.java">
              <line nr="23" mi="1" ci="0" />
              <line nr="24" mi="0" ci="1" />
            </sourcefile>
          </package>
        </report>
        """,
        encoding="utf-8",
    )

    with patch("app.services.sast_prescan_service.shutil.which", return_value=None):
        payload = SastPreScanService().scan_file(repo, "src/main/java/app/OrderService.java", enabled=True)

    assert payload["enabled"] is True
    assert payload["findings"][0]["tool"] == "jacoco"
    assert payload["findings"][0]["rule_id"] == "uncovered-lines"
    assert payload["findings"][0]["line_start"] == 23


def test_sast_tool_status_reports_command_and_report_availability(tmp_path: Path):
    repo = tmp_path / "repo"
    report = repo / "target/site/jacoco/jacoco.xml"
    report.parent.mkdir(parents=True)
    report.write_text("<report />\n", encoding="utf-8")

    with patch("app.services.sast_prescan_service.shutil.which", side_effect=lambda name: "C:/tools/semgrep.exe" if name == "semgrep" else None):
        payload = SastPreScanService().tool_status(enabled=True, repo_root=repo)

    assert payload["enabled"] is True
    semgrep = next(item for item in payload["command_tools"] if item["tool"] == "semgrep")
    assert semgrep["status"] == "available"
    assert semgrep["executable"] == "C:/tools/semgrep.exe"
    jacoco = next(item for item in payload["report_tools"] if item["tool"] == "jacoco")
    assert jacoco["status"] == "available"
    assert "target/site/jacoco/jacoco.xml" in jacoco["existing_report_paths"]
    assert "命令类工具可用" in payload["summary"]


def test_sast_tool_status_finds_windows_common_install_path_when_backend_path_misses(tmp_path: Path, monkeypatch):
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    eslint = npm_dir / "eslint.cmd"
    eslint.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    with (
        patch("app.services.sast_prescan_service.platform.system", return_value="Windows"),
        patch("app.services.sast_prescan_service.shutil.which", return_value=None),
    ):
        payload = SastPreScanService().tool_status(enabled=True)

    eslint_status = next(item for item in payload["command_tools"] if item["tool"] == "eslint")
    assert eslint_status["status"] == "available"
    assert eslint_status["executable"] == str(eslint)
    assert eslint_status["detection_method"] == "windows_common_path"
