from app.domain.models.review import ReviewSubject
from app.services.platform_adapter import PlatformAdapter


def test_platform_adapter_normalizes_branch_subject():
    adapter = PlatformAdapter()
    subject = ReviewSubject(
        subject_type="branch",
        repo_id="repo_1",
        project_id="proj_1",
        source_ref="feature/demo",
        target_ref="main",
        title="branch review",
    )
    normalized = adapter.normalize(subject)
    assert normalized.subject_type == "branch"
    assert normalized.changed_files
    assert normalized.unified_diff
    assert f"diff --git a/{normalized.changed_files[0]}" in normalized.unified_diff
    assert f"diff --git a/{normalized.changed_files[1]}" in normalized.unified_diff


def test_platform_adapter_infers_refs_from_merge_request_url():
    adapter = PlatformAdapter()
    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://git.example.com/platform/payments/-/merge_requests/42",
        repo_url="https://git.example.com/platform/payments",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.subject_type == "mr"
    assert normalized.project_id == "platform"
    assert normalized.repo_id == "payments"
    assert normalized.source_ref == "mr/42"
    assert normalized.target_ref == "main"
    assert normalized.title == "Merge Request !42"
    assert normalized.metadata["platform"] == "git.example.com"
    assert "diff --git a/src/review/runtime.py" in normalized.unified_diff


def test_platform_adapter_normalizes_github_commit_url(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(
        adapter,
        "_fetch_remote_diff",
        lambda review_url, access_token: (
            "diff --git a/backend/app/main.py b/backend/app/main.py\n"
            "--- a/backend/app/main.py\n"
            "+++ b/backend/app/main.py\n"
            "@@ -1,2 +1,3 @@\n"
            " from fastapi import FastAPI\n"
            "+from app.runtime import bootstrap\n"
            " app = FastAPI()\n"
        ),
    )

    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://github.com/neochen1991/multi-agent-cli/commit/e396447b53dc0c545543b2b4dabe3deb3a66bb9c",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.repo_id == "multi-agent-cli"
    assert normalized.project_id == "neochen1991"
    assert normalized.source_ref == "e396447b53dc0c545543b2b4dabe3deb3a66bb9c"
    assert normalized.title == "Commit e396447b53dc"
    assert normalized.metadata["compare_mode"] == "commit_compare"
    assert normalized.metadata["platform_kind"] == "github"
    assert normalized.metadata["remote_diff_fetched"] is True
    assert normalized.changed_files == ["backend/app/main.py"]
    assert normalized.commits == ["e396447b53dc0c545543b2b4dabe3deb3a66bb9c"]
