import subprocess
from pathlib import Path

from app.services.code_graph.file_selector import CodeGraphFileSelector


def test_file_selector_uses_git_tracked_files_and_code_review_graph_ignore(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".code-review-graphignore").write_text(
        "\n".join(
            [
                "generated/**",
                "*.pb.java",
            ]
        ),
        encoding="utf-8",
    )
    for relative in [
        "src/main/java/com/example/OrderService.java",
        "src/main/java/com/example/OrderController.java",
        "generated/GeneratedClient.java",
        "src/main/java/com/example/Event.pb.java",
        "README.md",
    ]:
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["git", "ls-files"],
            returncode=0,
            stdout="\n".join(
                [
                    "src/main/java/com/example/OrderService.java",
                    "src/main/java/com/example/OrderController.java",
                    "generated/GeneratedClient.java",
                    "src/main/java/com/example/Event.pb.java",
                    "README.md",
                ]
            ),
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    files = CodeGraphFileSelector(repo).select_files(languages=["java"])

    assert files == [
        "src/main/java/com/example/OrderController.java",
        "src/main/java/com/example/OrderService.java",
    ]


def test_file_selector_falls_back_to_filesystem_when_git_is_unavailable(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    build_artifact = repo / "target" / "generated-sources" / "Generated.java"
    source.parent.mkdir(parents=True)
    build_artifact.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")
    build_artifact.write_text("class Generated {}", encoding="utf-8")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=["git", "ls-files"], returncode=128, stdout="", stderr="not a git repo")

    monkeypatch.setattr(subprocess, "run", fake_run)

    files = CodeGraphFileSelector(repo).select_files(languages=["java"])

    assert files == ["src/main/java/com/example/OrderService.java"]


def test_file_selector_normalizes_windows_style_paths(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    source = repo / "src" / "main" / "java" / "com" / "example" / "OrderService.java"
    source.parent.mkdir(parents=True)
    source.write_text("class OrderService {}", encoding="utf-8")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["git", "ls-files"],
            returncode=0,
            stdout=r"src\main\java\com\example\OrderService.java",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    files = CodeGraphFileSelector(repo).select_files(languages=["java"])

    assert files == ["src/main/java/com/example/OrderService.java"]
