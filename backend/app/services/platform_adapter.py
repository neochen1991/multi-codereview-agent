from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.http_client_factory import HttpClientFactory

logger = logging.getLogger(__name__)


class PlatformAdapter:
    """Platform-neutral review subject normalizer.

    当前仍是本地占位实现，但接口已经按设计文档抽成统一入口，后续可以替换成
    GitHub/GitLab/自建平台的真实 diff 拉取器。
    """

    def normalize(self, subject: ReviewSubject, runtime_settings: RuntimeSettings | None = None) -> ReviewSubject:
        review_url = subject.mr_url or subject.repo_url
        review_mode = self._infer_review_mode(subject.subject_type, review_url)
        repo_project = self._infer_repo_project(subject.repo_url, review_url)
        mr_id = self._infer_merge_request_id(review_url)
        commit_sha = self._infer_commit_sha(review_url)
        repo_id = subject.repo_id or repo_project["repo_id"]
        project_id = subject.project_id or repo_project["project_id"]
        source_ref = subject.source_ref or self._infer_source_ref(review_mode, mr_id, commit_sha)
        target_ref = subject.target_ref or "main"
        title = subject.title or self._infer_title(review_mode, mr_id, commit_sha, source_ref, target_ref)

        changed_files = list(subject.changed_files)
        remote_diff_fetched = False
        unified_diff = subject.unified_diff
        if review_mode in {"commit_compare", "mr_compare"} and not unified_diff and review_url:
            logger.info("normalizing remote review review_url=%s review_mode=%s", review_url, review_mode)
            unified_diff = self._fetch_remote_diff(review_url, subject.access_token, runtime_settings)
            remote_diff_fetched = bool(unified_diff)
            if unified_diff:
                changed_preview = self._infer_changed_files_from_diff(unified_diff)
                diff_preview = "\n".join(unified_diff.splitlines()[:20])
                logger.info(
                    "github diff payload review_url=%s file_count=%s changed_files=%s diff_preview=\n%s",
                    review_url,
                    len(changed_preview),
                    changed_preview,
                    diff_preview,
                )
            logger.info(
                "remote diff normalization finished review_url=%s fetched=%s changed_files=%s",
                review_url,
                remote_diff_fetched,
                self._infer_changed_files_from_diff(unified_diff) if unified_diff else [],
            )
        if unified_diff and not changed_files:
            changed_files = self._infer_changed_files_from_diff(unified_diff)
        if not changed_files and review_mode == "branch_compare":
            changed_files = self._infer_changed_files(title=title, source_ref=source_ref)
        if not unified_diff and review_mode == "branch_compare":
            unified_diff = self._build_default_diff(changed_files)

        commits = list(subject.commits) or ([commit_sha] if commit_sha else [f"{source_ref}-head"])
        metadata = dict(subject.metadata)
        if repo_project["platform"]:
            metadata.setdefault("platform", repo_project["platform"])
        metadata.setdefault("normalized_from", "platform_adapter")
        metadata.setdefault("review_mode", subject.subject_type)
        metadata.setdefault("compare_mode", review_mode)
        metadata.setdefault("platform_kind", self._infer_platform_kind(review_url))
        metadata.setdefault("trigger_source", "manual")
        metadata.setdefault("remote_diff_fetched", remote_diff_fetched)
        metadata.setdefault("remote_diff_available", bool(unified_diff))
        if commit_sha:
            metadata.setdefault("commit_sha", commit_sha)

        return subject.model_copy(
            update={
                "repo_id": repo_id,
                "project_id": project_id,
                "source_ref": source_ref,
                "target_ref": target_ref,
                "title": title,
                "changed_files": changed_files,
                "unified_diff": unified_diff,
                "commits": commits,
                "metadata": metadata,
            }
        )

    def _infer_repo_project(self, repo_url: str, review_url: str) -> dict[str, str]:
        candidate = repo_url or review_url
        if not candidate:
            return {"platform": "", "project_id": "", "repo_id": ""}
        parsed = urlparse(candidate)
        path_parts = [segment for segment in parsed.path.split("/") if segment]
        if "-" in path_parts:
            path_parts = path_parts[: path_parts.index("-")]
        if "merge_requests" in path_parts:
            path_parts = path_parts[: path_parts.index("merge_requests")]
        if "pull" in path_parts:
            path_parts = path_parts[: path_parts.index("pull")]
        if "commit" in path_parts:
            path_parts = path_parts[: path_parts.index("commit")]
        repo_id = path_parts[-1] if len(path_parts) >= 1 else ""
        project_id = path_parts[-2] if len(path_parts) >= 2 else ""
        return {"platform": parsed.netloc, "project_id": project_id, "repo_id": repo_id}

    def _infer_merge_request_id(self, review_url: str) -> str:
        if not review_url:
            return ""
        path_parts = [segment for segment in urlparse(review_url).path.split("/") if segment]
        if "merge_requests" not in path_parts:
            if "pull" in path_parts:
                index = path_parts.index("pull")
                if index + 1 < len(path_parts):
                    return path_parts[index + 1]
            return ""
        index = path_parts.index("merge_requests")
        if index + 1 >= len(path_parts):
            return ""
        return path_parts[index + 1]

    def _infer_commit_sha(self, review_url: str) -> str:
        if not review_url:
            return ""
        path_parts = [segment for segment in urlparse(review_url).path.split("/") if segment]
        if "commit" not in path_parts:
            return ""
        index = path_parts.index("commit")
        if index + 1 >= len(path_parts):
            return ""
        return path_parts[index + 1]

    def _infer_review_mode(self, subject_type: str, review_url: str) -> str:
        if "/commit/" in review_url:
            return "commit_compare"
        if "/pull/" in review_url or "merge_requests" in review_url or subject_type == "mr":
            return "mr_compare"
        return "branch_compare"

    def _infer_source_ref(self, review_mode: str, mr_id: str, commit_sha: str) -> str:
        if review_mode == "commit_compare" and commit_sha:
            return commit_sha
        if review_mode == "mr_compare" and mr_id:
            return f"mr/{mr_id}"
        return "feature/auto-review"

    def _infer_title(
        self,
        review_mode: str,
        mr_id: str,
        commit_sha: str,
        source_ref: str,
        target_ref: str,
    ) -> str:
        if review_mode == "commit_compare" and commit_sha:
            return f"Commit {commit_sha[:12]}"
        if review_mode == "mr_compare" and mr_id:
            return f"Merge Request !{mr_id}"
        return f"{source_ref} -> {target_ref}"

    def _infer_platform_kind(self, review_url: str) -> str:
        if "github.com" in review_url:
            return "github"
        return "gitlab_like"

    def _fetch_remote_diff(
        self,
        review_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> str:
        if "github.com" not in review_url:
            logger.info("skip remote diff fetch for non-github url=%s", review_url)
            return ""
        candidate_urls = self._build_remote_diff_candidates(review_url)
        if not candidate_urls:
            logger.warning("no remote diff candidates built for url=%s", review_url)
            return ""
        headers: dict[str, str] = {}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        headers["User-Agent"] = "multi-codereview-agent/1.0"
        try:
            with HttpClientFactory.create(
                timeout=httpx.Timeout(45.0, connect=10.0, read=45.0),
                runtime_settings=runtime_settings,
                follow_redirects=False,
            ) as client:
                for candidate_url in candidate_urls:
                    try:
                        logger.info("attempt remote diff fetch review_url=%s candidate=%s", review_url, candidate_url)
                        diff_text = self._fetch_candidate_diff(client, candidate_url, headers)
                        if diff_text:
                            logger.info("remote diff fetch succeeded review_url=%s candidate=%s", review_url, candidate_url)
                            return diff_text
                    except Exception as error:
                        logger.warning("remote diff candidate failed for %s via %s: %s", review_url, candidate_url, error)
        except Exception as error:
            logger.warning("remote diff fetch failed for %s: %s", review_url, error)
        return ""

    def _build_remote_diff_candidates(self, review_url: str) -> list[str]:
        if "/commit/" in review_url or "/pull/" in review_url:
            return [f"{review_url}.patch", f"{review_url}.diff"]
        return []

    def _fetch_candidate_diff(
        self,
        client: httpx.Client,
        candidate_url: str,
        headers: dict[str, str],
    ) -> str:
        current_url = candidate_url
        for _ in range(3):
            response = client.get(current_url, headers=headers)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "").strip()
                if not location:
                    return ""
                current_url = location
                continue
            response.raise_for_status()
            text = response.text
            if "diff --git " in text:
                return text
            return ""
        return ""

    def _infer_changed_files_from_diff(self, unified_diff: str) -> list[str]:
        changed_files: list[str] = []
        for line in unified_diff.splitlines():
            if not line.startswith("diff --git "):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            right = parts[3]
            file_path = right[2:] if right.startswith("b/") else right
            if file_path and file_path not in changed_files:
                changed_files.append(file_path)
        return changed_files

    def _infer_changed_files(self, title: str, source_ref: str) -> list[str]:
        token_source = f"{title} {source_ref}".lower()
        if any(token in token_source for token in ["security", "auth", "oauth", "permission"]):
            return [
                "backend/app/security/authz.py",
                "backend/app/api/auth.py",
            ]
        if any(token in token_source for token in ["migration", "schema", "sql", "db"]):
            return [
                "backend/db/migrations/20260312_review.sql",
                "backend/app/repositories/review_repository.py",
            ]
        if any(token in token_source for token in ["frontend", "ui", "react", "a11y"]):
            return [
                "frontend/src/pages/ReviewWorkbench/index.tsx",
                "frontend/src/components/review/IssueThreadList.tsx",
            ]
        return [
            "src/review/runtime.py",
            "src/review/policies.py",
        ]

    def _build_default_diff(self, changed_files: list[str]) -> str:
        sections = [self._build_file_diff(file_path) for file_path in changed_files]
        return "\n".join(section for section in sections if section)

    def _build_file_diff(self, file_path: str) -> str:
        lower = file_path.lower()
        if file_path.endswith("src/review/runtime.py"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -10,4 +12,5 @@\n"
                " def review_guard(payload):\n"
                "+    enabled = payload.get('enabled')\n"
                "     if payload.get('enabled'):\n"
                "         return True\n"
                "     return False\n"
                "@@ -40,4 +42,5 @@\n"
                " def build_runtime_service(payload):\n"
                "+    service = RuntimeService()\n"
                "+    service.repo.save(payload)\n"
                "+    return service.policy.allow(payload)\n"
                "     return RuntimeService()\n"
                "@@ -70,4 +73,5 @@\n"
                " def review_guard(payload):\n"
                "     if payload.get('enabled'):\n"
                "         return True\n"
                "+    return False  # no regression test covers this branch\n"
                "     return False\n"
            )
        if file_path.endswith("src/review/policies.py"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -18,4 +20,5 @@\n"
                " def allow(payload):\n"
                "+    return payload.get('enabled')\n"
                "     if payload.get('role') == 'admin':\n"
                "         return True\n"
                "     return False\n"
            )
        if lower.endswith("backend/app/security/authz.py"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -16,4 +18,5 @@\n"
                " def can_access(user, payload):\n"
                "+    if payload.get('enabled'):\n"
                "+        return True\n"
                "     if user.is_admin:\n"
                "         return True\n"
                "     return False\n"
            )
        if lower.endswith("backend/app/api/auth.py"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -24,4 +26,5 @@\n"
                " def login(payload):\n"
                "+    token = payload.get('token')\n"
                "     if not payload.get('user'):\n"
                "         raise ValueError('missing user')\n"
                "     return issue_token(payload)\n"
            )
        if lower.endswith("backend/db/migrations/20260312_review.sql"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -55,4 +57,5 @@\n"
                " SELECT * FROM review_events;\n"
                "+ALTER TABLE review_events ADD COLUMN execution_mode VARCHAR(32);\n"
                "+UPDATE review_events SET execution_mode = 'sync';\n"
                " COMMIT;\n"
            )
        if lower.endswith("backend/app/repositories/review_repository.py"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -59,4 +61,5 @@\n"
                " def load_reviews(db):\n"
                "+    rows = db.query('select * from reviews')\n"
                "+    return [hydrate(row) for row in rows]\n"
                "     return []\n"
            )
        if lower.endswith("frontend/src/pages/reviewworkbench/index.tsx"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -40,4 +42,5 @@\n"
                " const ReviewWorkbenchPage = () => {\n"
                "+  const service = createReviewService();\n"
                "+  service.repo.save(payload);\n"
                "   return <Page />;\n"
            )
        if lower.endswith("frontend/src/components/review/issuethreadlist.tsx"):
            return (
                f"diff --git a/{file_path} b/{file_path}\n"
                f"--- a/{file_path}\n"
                f"+++ b/{file_path}\n"
                "@@ -26,4 +28,5 @@\n"
                " <List.Item>\n"
                "+  <span>{item.summary}</span>\n"
                " </List.Item>\n"
            )
        return (
            f"diff --git a/{file_path} b/{file_path}\n"
            f"--- a/{file_path}\n"
            f"+++ b/{file_path}\n"
            "@@ -10,4 +12,5 @@\n"
            " def review_guard(payload):\n"
            "+    enabled = payload.get('enabled')\n"
            "     if payload.get('enabled'):\n"
            "         return True\n"
            "     return False\n"
        )
