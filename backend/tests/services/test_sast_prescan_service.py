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
    assert "未发现可用" in payload["limitations"][0]


def test_sast_prescan_parses_semgrep_json(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("eval(user_input)\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None):
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
    assert "Use of eval" in payload["summary"] or payload["findings"][0]["message"] == "Use of eval"


def test_sast_prescan_uses_project_semgrep_config(tmp_path: Path):
    repo = tmp_path / "repo"
    target = repo / "src/app.py"
    target.parent.mkdir(parents=True)
    target.write_text("eval(user_input)\n", encoding="utf-8")
    (repo / ".semgrep.yml").write_text("rules: []\n", encoding="utf-8")

    def fake_run(command, cwd=None, capture_output=None, text=None, timeout=None, check=None):
        assert "--config" in command
        assert str(repo / ".semgrep.yml") in command
        return type("Completed", (), {"stdout": '{"results":[]}', "stderr": "", "returncode": 0})()

    with patch("app.services.sast_prescan_service.shutil.which", side_effect=lambda name: "/usr/bin/semgrep" if name == "semgrep" else None):
        with patch("app.services.sast_prescan_service.subprocess.run", side_effect=fake_run):
            payload = SastPreScanService().scan_file(repo, "src/app.py", enabled=True)

    assert payload["enabled"] is True
