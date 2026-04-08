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
    assert normalized.changed_files == []
    assert normalized.unified_diff == ""
    assert normalized.metadata["remote_diff_available"] is False


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
    assert normalized.unified_diff == ""
    assert normalized.changed_files == []
    assert normalized.metadata["remote_diff_available"] is False


def test_platform_adapter_normalizes_github_commit_url(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(
        adapter,
        "_fetch_remote_diff",
        lambda review_url, access_token, runtime_settings=None: (
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


def test_platform_adapter_normalizes_github_pull_request_url(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(
        adapter,
        "_fetch_remote_diff",
        lambda review_url, access_token, runtime_settings=None: (
            "diff --git a/backend/app/api/orders.py b/backend/app/api/orders.py\n"
            "--- a/backend/app/api/orders.py\n"
            "+++ b/backend/app/api/orders.py\n"
            "@@ -40,2 +40,5 @@\n"
            " def create_order(payload):\n"
            "+    cache_key = payload.get('cache_key')\n"
            "+    return persist_order(payload)\n"
        ),
    )

    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://github.com/example-org/payments-service/pull/128",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.repo_id == "payments-service"
    assert normalized.project_id == "example-org"
    assert normalized.source_ref == "mr/128"
    assert normalized.target_ref == "main"
    assert normalized.title == "Merge Request !128"
    assert normalized.metadata["compare_mode"] == "mr_compare"
    assert normalized.metadata["platform_kind"] == "github"
    assert normalized.metadata["remote_diff_fetched"] is True
    assert normalized.changed_files == ["backend/app/api/orders.py"]


def test_platform_adapter_keeps_remote_review_empty_when_github_diff_unavailable(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(adapter, "_fetch_remote_diff", lambda review_url, access_token, runtime_settings=None: "")

    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://github.com/example-org/payments-service/pull/128",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.unified_diff == ""
    assert normalized.changed_files == []
    assert normalized.metadata["remote_diff_fetched"] is False
    assert normalized.metadata["remote_diff_available"] is False


def test_platform_adapter_fetch_remote_diff_follows_redirect(monkeypatch):
    adapter = PlatformAdapter()

    class FakeResponse:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, headers: dict[str, str] | None = None):
            if url.endswith(".patch"):
                return FakeResponse(302, headers={"Location": "https://patch-diff.githubusercontent.com/raw/example/repo/pull/128.patch"})
            return FakeResponse(
                200,
                text=(
                    "diff --git a/backend/app/api/orders.py b/backend/app/api/orders.py\n"
                    "--- a/backend/app/api/orders.py\n"
                    "+++ b/backend/app/api/orders.py\n"
                    "@@ -1,1 +1,2 @@\n"
                    "+return persist_order(payload)\n"
                ),
            )

    monkeypatch.setattr("app.services.platform_adapter.HttpClientFactory.create", lambda **kwargs: FakeClient())

    diff = adapter._fetch_remote_diff("https://github.com/example-org/payments-service/pull/128", "")

    assert "diff --git a/backend/app/api/orders.py" in diff


def test_platform_adapter_fetch_remote_diff_falls_back_to_diff_when_patch_fails(monkeypatch):
    adapter = PlatformAdapter()

    class FakeResponse:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url: str, headers: dict[str, str] | None = None):
            if url.endswith(".patch"):
                raise RuntimeError("patch unavailable")
            return FakeResponse(
                200,
                text=(
                    "diff --git a/backend/app/api/orders.py b/backend/app/api/orders.py\n"
                    "--- a/backend/app/api/orders.py\n"
                    "+++ b/backend/app/api/orders.py\n"
                    "@@ -1,1 +1,2 @@\n"
                    "+return persist_order(payload)\n"
                ),
            )

    monkeypatch.setattr("app.services.platform_adapter.HttpClientFactory.create", lambda **kwargs: FakeClient())

    diff = adapter._fetch_remote_diff("https://github.com/example-org/payments-service/pull/128", "")

    assert "diff --git a/backend/app/api/orders.py" in diff


def test_platform_adapter_exposes_gitlab_provider_candidates():
    adapter = PlatformAdapter()

    candidates = adapter._build_remote_diff_candidates(
        "https://gitlab.example.com/platform/payments/-/merge_requests/128"
    )

    assert candidates == [
        "https://gitlab.example.com/platform/payments/-/merge_requests/128.patch",
        "https://gitlab.example.com/platform/payments/-/merge_requests/128.diff",
    ]


def test_platform_adapter_marks_gitlab_provider_metadata(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(
        adapter,
        "_fetch_remote_diff",
        lambda review_url, access_token, runtime_settings=None: (
            "diff --git a/backend/app/api/orders.py b/backend/app/api/orders.py\n"
            "--- a/backend/app/api/orders.py\n"
            "+++ b/backend/app/api/orders.py\n"
            "@@ -1,1 +1,2 @@\n"
            "+return persist_order(payload)\n"
        ),
    )

    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://gitlab.example.com/platform/payments/-/merge_requests/128",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.metadata["platform_kind"] == "gitlab_like"
    assert normalized.metadata["platform_provider"] == "GitLabReviewProvider"
    assert normalized.changed_files == ["backend/app/api/orders.py"]


def test_platform_adapter_normalizes_codehub_merge_request_url_without_dash_segment(monkeypatch):
    adapter = PlatformAdapter()

    monkeypatch.setattr(
        adapter,
        "_fetch_remote_diff",
        lambda review_url, access_token, runtime_settings=None: (
            "diff --git a/backend/app/api/orders.py b/backend/app/api/orders.py\n"
            "--- a/backend/app/api/orders.py\n"
            "+++ b/backend/app/api/orders.py\n"
            "@@ -12,2 +12,3 @@ def create_order(payload):\n"
            "     return persist_order(payload)\n"
            "+    return publish_event(payload)\n"
        ),
    )

    subject = ReviewSubject(
        subject_type="mr",
        repo_id="",
        project_id="",
        source_ref="",
        target_ref="",
        mr_url="https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/128",
        title="",
    )

    normalized = adapter.normalize(subject)

    assert normalized.project_id == "FND"
    assert normalized.repo_id == "projectname"
    assert normalized.source_ref == "mr/128"
    assert normalized.metadata["platform_kind"] == "gitlab_like"
    assert normalized.metadata["remote_diff_fetched"] is True
    assert normalized.changed_files == ["backend/app/api/orders.py"]


def test_platform_adapter_exposes_codehub_candidates_without_dash_segment():
    adapter = PlatformAdapter()

    candidates = adapter._build_remote_diff_candidates(
        "https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/128"
    )

    assert candidates == [
        "https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/128.patch",
        "https://codehub-g.huawei.com/PIP/FND/projectname/merge_requests/128.diff",
    ]
