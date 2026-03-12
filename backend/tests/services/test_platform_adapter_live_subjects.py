from app.domain.models.review import ReviewSubject
from app.services.platform_adapter import PlatformAdapter


def test_platform_adapter_builds_branch_compare_subject():
    adapter = PlatformAdapter()
    subject = adapter.normalize(
        ReviewSubject(
            subject_type="branch",
            repo_id="payments",
            project_id="platform",
            source_ref="feature/risk-guard",
            target_ref="main",
            repo_url="https://git.example.com/platform/payments",
        )
    )

    assert subject.metadata["compare_mode"] == "branch_compare"
    assert subject.metadata["platform_kind"] == "gitlab_like"
    assert subject.metadata["trigger_source"] == "manual"
    assert subject.commits


def test_platform_adapter_marks_commit_compare_mode(monkeypatch):
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

    subject = adapter.normalize(
        ReviewSubject(
            subject_type="mr",
            repo_id="",
            project_id="",
            source_ref="",
            target_ref="",
            mr_url="https://github.com/neochen1991/multi-agent-cli/commit/e396447b53dc0c545543b2b4dabe3deb3a66bb9c",
        )
    )

    assert subject.metadata["compare_mode"] == "commit_compare"
    assert subject.metadata["platform_kind"] == "github"
    assert subject.changed_files == ["backend/app/main.py"]
