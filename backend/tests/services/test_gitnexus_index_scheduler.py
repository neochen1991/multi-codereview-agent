from pathlib import Path
from types import SimpleNamespace

from app.repositories.fs import write_json
from app.domain.models.runtime_settings import CodeRepositorySettings
from app.services.gitnexus_index_scheduler import GitNexusIndexScheduler
from app.services.review_service import ReviewService


def test_gitnexus_index_scheduler_skips_without_repo_path(storage_root: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    service = ReviewService(storage_root=storage_root)
    scheduler = GitNexusIndexScheduler(service)

    status = scheduler.tick()

    assert status["state"] == "skipped"
    assert "本地代码仓路径" in str(status["message"])


def test_gitnexus_index_scheduler_skips_without_gitnexus_binary(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: None)
    monkeypatch.setattr("app.services.command_resolver._resolve_with_system_where", lambda executable: "")

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "skipped"
    assert status["gitnexus_installed"] is False
    assert "未预装 GitNexus" in str(status["message"])


def test_gitnexus_index_scheduler_records_registry_status(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_bin = tmp_path / "bin" / "gitnexus"
    fake_bin.parent.mkdir()
    fake_bin.write_text("", encoding="utf-8")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / ".gitnexus").mkdir()
    registry_dir = tmp_path / ".gitnexus"
    registry_dir.mkdir(exist_ok=True)
    write_json(registry_dir / "registry.json", {"repositories": [{"name": "repo", "path": str(repo_path)}]})

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: str(fake_bin) if command == "gitnexus" else None)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""))

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["gitnexus_installed"] is True
    assert status["gitnexus_command"] == f"{fake_bin} analyze"
    assert status["registry_registered"] is True
    assert str(status["registry_path"]).endswith(".gitnexus/registry.json")


def test_gitnexus_index_scheduler_fails_when_repo_path_missing(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    missing_repo = tmp_path / "missing-repo"

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(missing_repo)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "failed"
    assert "本地代码仓路径不存在" in str(status["message"])
    assert status["repo_path"] == str(missing_repo)


def test_gitnexus_index_scheduler_uses_resolved_gitnexus_binary(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    fake_bin = tmp_path / "bin" / "gitnexus"
    fake_bin.parent.mkdir()
    fake_bin.write_text("", encoding="utf-8")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: str(fake_bin) if command == "gitnexus" else None)

    captured: list[object] = []

    def _fake_run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert captured[0] == [str(fake_bin), "analyze"]


def test_gitnexus_index_scheduler_retries_non_git_folder_with_skip_git(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    fake_bin = tmp_path / "bin" / "gitnexus"
    fake_bin.parent.mkdir()
    fake_bin.write_text("", encoding="utf-8")
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: str(fake_bin) if command == "gitnexus" else None)

    captured: list[object] = []

    def _fake_run(command, **kwargs):
        captured.append(command)
        if command == [str(fake_bin), "analyze"]:
            return SimpleNamespace(
                returncode=1,
                stdout="Not inside a git repository. Tip: pass --skip-git to index any folder without a .git directory.",
                stderr="fatal: not a git repository",
            )
        return SimpleNamespace(returncode=0, stdout="indexed with skip git", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    analyze_commands = [command for command in captured if command and command[0] == str(fake_bin)]
    assert analyze_commands == [[str(fake_bin), "analyze"], [str(fake_bin), "analyze", "--skip-git"]]
    assert status["gitnexus_command"] == f"{fake_bin} analyze --skip-git"
    assert "--skip-git" in str(status["retry_reason"])


def test_gitnexus_index_scheduler_uses_gitnexus_bin_with_space_path(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    fake_bin = tmp_path / "Program Files" / "GitNexus" / "gitnexus.exe"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITNEXUS_BIN", str(fake_bin))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: None)

    captured: list[object] = []

    def _fake_run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["gitnexus_installed"] is True
    assert status["gitnexus_path"] == str(fake_bin)
    assert captured[0] == [str(fake_bin), "analyze"]


def test_gitnexus_index_scheduler_finds_windows_npm_cmd_when_service_path_is_stale(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    appdata = tmp_path / "AppData" / "Roaming"
    fake_bin = appdata / "npm" / "gitnexus.cmd"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: None)

    captured: list[object] = []

    def _fake_run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["gitnexus_installed"] is True
    assert status["gitnexus_path"] == str(fake_bin)
    assert captured[0] == [str(fake_bin), "analyze"]


def test_gitnexus_index_status_refreshes_stale_installation_flag(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    appdata = tmp_path / "AppData" / "Roaming"
    fake_bin = appdata / "npm" / "gitnexus.cmd"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr("shutil.which", lambda command: None)

    service = ReviewService(storage_root=storage_root)
    status_path = storage_root / "gitnexus" / "index_status.json"
    write_json(
        status_path,
        {
            "state": "skipped",
            "message": "当前机器未预装 GitNexus，跳过建图。",
            "gitnexus_installed": False,
            "gitnexus_command": "gitnexus analyze",
            "gitnexus_path": "",
        },
    )

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.status()

    assert status["gitnexus_installed"] is True
    assert status["gitnexus_path"] == str(fake_bin)
    assert status["gitnexus_command"] == f"{fake_bin} analyze"


def test_gitnexus_index_scheduler_accepts_json_analyze_command(storage_root: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GITNEXUS_INDEX_ENABLED", "true")
    fake_bin = tmp_path / "Tools With Spaces" / "gitnexus.exe"
    fake_bin.parent.mkdir(parents=True)
    fake_bin.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITNEXUS_ANALYZE_COMMAND", f'["{fake_bin}", "analyze", "--verbose"]')
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(update={"code_repo_local_path": str(repo_path)})
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: None)

    captured: list[object] = []

    def _fake_run(command, **kwargs):
        captured.append(command)
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr("subprocess.run", _fake_run)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["gitnexus_command"] == f"{fake_bin} analyze --verbose"
    assert captured[0] == [str(fake_bin), "analyze", "--verbose"]


def test_gitnexus_index_scheduler_registry_matches_windows_path(storage_root: Path, tmp_path: Path, monkeypatch):
    registry_path = tmp_path / "gitnexus-home" / "registry.json"
    registry_path.parent.mkdir()
    write_json(registry_path, {"repositories": [{"name": "repo-win", "path": r"C:\Work\Repo\\"}]})
    monkeypatch.setenv("GITNEXUS_REGISTRY_PATH", str(registry_path))

    service = ReviewService(storage_root=storage_root)
    scheduler = GitNexusIndexScheduler(service)

    registered, used_registry_path = scheduler._registry_status("c:/work/repo/")

    assert registered is True
    assert used_registry_path == str(registry_path)


def test_gitnexus_index_scheduler_does_not_fallback_for_unknown_repository_id(storage_root: Path, tmp_path: Path, monkeypatch):
    legacy_repo = tmp_path / "legacy-repo"
    configured_repo = tmp_path / "configured-repo"
    legacy_repo.mkdir()
    configured_repo.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "code_repo_local_path": str(legacy_repo),
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="configured",
                    clone_url="https://example.com/configured.git",
                    local_path=str(configured_repo),
                )
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick("missing")

    assert status["state"] == "skipped"
    assert status["repository_id"] == "missing"
    assert "本地代码仓路径" in str(status["message"])
    assert "repo_path" not in status


def test_gitnexus_index_scheduler_uses_first_repository_when_default_id_is_empty(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    repo_path = tmp_path / "configured-repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "",
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="configured",
                    clone_url="https://example.com/configured.git",
                    local_path=str(repo_path),
                )
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""))

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["repository_id"] == "configured"
    assert status["repo_path"] == str(repo_path.resolve(strict=False))
    assert (storage_root / "gitnexus" / "configured" / "index_status.json").exists()


def test_gitnexus_index_scheduler_uses_first_repository_when_default_id_is_stale(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    repo_path = tmp_path / "configured-repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "deleted-repo",
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="configured",
                    clone_url="https://example.com/configured.git",
                    local_path=str(repo_path),
                )
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="ok", stderr=""))

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.tick()

    assert status["state"] == "ready"
    assert status["repository_id"] == "configured"
    assert status["repo_path"] == str(repo_path.resolve(strict=False))


def test_gitnexus_index_status_refreshes_repo_path_from_runtime_config(
    storage_root: Path,
    tmp_path: Path,
    monkeypatch,
):
    repo_path = tmp_path / "configured-repo"
    repo_path.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="configured",
                    clone_url="https://example.com/configured.git",
                    local_path=str(repo_path),
                )
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)
    write_json(
        storage_root / "gitnexus" / "configured" / "index_status.json",
        {
            "state": "ready",
            "message": "old",
            "repository_id": "configured",
            "repo_path": "C:/stale/path",
            "repo_name": "path",
            "graph_dir": "C:/stale/path/.gitnexus",
            "graph_dir_exists": True,
        },
    )

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.status("configured")

    assert status["repo_path"] == str(repo_path.resolve(strict=False))
    assert status["repo_name"] == "configured-repo"
    assert status["graph_dir"] == str(repo_path.resolve(strict=False) / ".gitnexus")
    assert status["graph_dir_exists"] is False


def test_gitnexus_manual_index_skips_unknown_repository_without_spawning(storage_root: Path, tmp_path: Path, monkeypatch):
    configured_repo = tmp_path / "configured-repo"
    configured_repo.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="configured",
                    clone_url="https://example.com/configured.git",
                    local_path=str(configured_repo),
                )
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)

    scheduler = GitNexusIndexScheduler(service)
    status = scheduler.trigger_manual_index("missing")

    assert status["state"] == "skipped"
    assert status["repository_id"] == "missing"
    assert status["trigger"] == "manual"


def test_gitnexus_manual_index_reports_other_repository_running(storage_root: Path, tmp_path: Path, monkeypatch):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    service = ReviewService(storage_root=storage_root)
    runtime = service.get_runtime_settings().model_copy(
        update={
            "default_repository_id": "repo-a",
            "code_repositories": [
                CodeRepositorySettings(
                    repository_id="repo-a",
                    clone_url="https://example.com/a.git",
                    local_path=str(repo_a),
                ),
                CodeRepositorySettings(
                    repository_id="repo-b",
                    clone_url="https://example.com/b.git",
                    local_path=str(repo_b),
                ),
            ],
        }
    )
    monkeypatch.setattr(service, "get_runtime_settings", lambda: runtime)
    monkeypatch.setattr("shutil.which", lambda command: "/usr/local/bin/gitnexus" if command == "gitnexus" else None)

    scheduler = GitNexusIndexScheduler(service)
    scheduler._manual_process = SimpleNamespace(is_alive=lambda: True)
    scheduler._manual_repository_id = "repo-a"

    status = scheduler.trigger_manual_index("repo-b")

    assert status["state"] == "blocked"
    assert status["repository_id"] == "repo-b"
    assert status["blocked_by_repository_id"] == "repo-a"
    assert "repo-a" in str(status["message"])
