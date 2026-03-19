from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.services.http_client_factory import HttpClientFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewPlatformProvider:
    """代码平台 provider 抽象。"""
    platform_kind: str

    def supports(self, review_url: str) -> bool:
        raise NotImplementedError

    def fetch_remote_diff(
        self,
        review_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> str:
        raise NotImplementedError

    def build_remote_diff_candidates(self, review_url: str) -> list[str]:
        raise NotImplementedError

    def list_open_merge_requests(
        self,
        repo_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> list["OpenMergeRequest"]:
        raise NotImplementedError

    def build_headers(self, access_token: str) -> dict[str, str]:
        headers = {"User-Agent": "multi-codereview-agent/1.0"}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        return headers

    def fetch_candidate_diff(
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


@dataclass(frozen=True)
class OpenMergeRequest:
    """描述代码平台上一个处于开启状态的 MR/PR。"""

    mr_url: str
    title: str
    source_ref: str = ""
    target_ref: str = "main"
    number: str = ""
    head_sha: str = ""


class GitHubReviewProvider(ReviewPlatformProvider):
    def __init__(self) -> None:
        super().__init__(platform_kind="github")

    def supports(self, review_url: str) -> bool:
        return "github.com" in review_url

    def fetch_remote_diff(
        self,
        review_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> str:
        candidate_urls = self.build_remote_diff_candidates(review_url)
        if not candidate_urls:
            logger.warning("no remote diff candidates built for url=%s", review_url)
            return ""
        headers = self.build_headers(access_token)
        try:
            with HttpClientFactory.create(
                timeout=httpx.Timeout(45.0, connect=10.0, read=45.0),
                runtime_settings=runtime_settings,
                follow_redirects=False,
            ) as client:
                for candidate_url in candidate_urls:
                    try:
                        logger.info("attempt remote diff fetch review_url=%s candidate=%s", review_url, candidate_url)
                        diff_text = self.fetch_candidate_diff(client, candidate_url, headers)
                        if diff_text:
                            logger.info("remote diff fetch succeeded review_url=%s candidate=%s", review_url, candidate_url)
                            return diff_text
                    except Exception as error:
                        logger.warning("remote diff candidate failed for %s via %s: %s", review_url, candidate_url, error)
        except Exception as error:
            logger.warning("remote diff fetch failed for %s: %s", review_url, error)
        return ""

    def build_remote_diff_candidates(self, review_url: str) -> list[str]:
        if "/commit/" in review_url or "/pull/" in review_url:
            return [f"{review_url}.patch", f"{review_url}.diff"]
        return []

    def list_open_merge_requests(
        self,
        repo_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> list[OpenMergeRequest]:
        owner, repo = self._extract_repo_slug(repo_url)
        if not owner or not repo:
            return []
        headers = self.build_headers(access_token)
        headers["Accept"] = "application/vnd.github+json"
        api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&sort=created&direction=asc&per_page=100"
        try:
            with HttpClientFactory.create(
                timeout=httpx.Timeout(30.0, connect=10.0, read=30.0),
                runtime_settings=runtime_settings,
                follow_redirects=True,
            ) as client:
                response = client.get(api_url, headers=headers)
                response.raise_for_status()
                rows = response.json()
        except Exception as error:
            logger.warning("list github open pull requests failed repo_url=%s error=%s", repo_url, error)
            return []

        merge_requests: list[OpenMergeRequest] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            mr_url = str(item.get("html_url") or "").strip()
            if not mr_url:
                continue
            merge_requests.append(
                OpenMergeRequest(
                    mr_url=mr_url,
                    title=str(item.get("title") or f"Pull Request #{item.get('number', '')}").strip(),
                    source_ref=str((item.get("head") or {}).get("ref") or "").strip(),
                    target_ref=str((item.get("base") or {}).get("ref") or "main").strip() or "main",
                    number=str(item.get("number") or "").strip(),
                    head_sha=str((item.get("head") or {}).get("sha") or "").strip(),
                )
            )
        merge_requests.sort(key=lambda item: int(item.number) if item.number.isdigit() else 0)
        return merge_requests

    def _extract_repo_slug(self, repo_url: str) -> tuple[str, str]:
        candidate = repo_url.strip()
        if candidate.startswith("git@"):
            normalized = candidate.split("@", 1)[1]
            host, _, path = normalized.partition(":")
            candidate = f"https://{host}/{path}"
        elif "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        path_parts = [segment for segment in parsed.path.split("/") if segment]
        if path_parts and path_parts[-1].endswith(".git"):
            path_parts[-1] = path_parts[-1][: -len(".git")]
        if len(path_parts) < 2:
            return "", ""
        return path_parts[-2], path_parts[-1]


class GitLabReviewProvider(ReviewPlatformProvider):
    def __init__(self) -> None:
        super().__init__(platform_kind="gitlab_like")

    def supports(self, review_url: str) -> bool:
        lowered = review_url.lower()
        if "github.com" in lowered:
            return False
        return any(
            token in lowered
            for token in (
                "gitlab",
                "codehub",
                "merge_requests",
                "/-/commit/",
                "/commit/",
            )
        )

    def fetch_remote_diff(
        self,
        review_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> str:
        candidate_urls = self.build_remote_diff_candidates(review_url)
        if not candidate_urls:
            logger.info("skip remote diff fetch for unsupported gitlab-like url=%s", review_url)
            return ""
        headers = self.build_headers(access_token)
        if access_token:
            headers["PRIVATE-TOKEN"] = access_token
        try:
            with HttpClientFactory.create(
                timeout=httpx.Timeout(45.0, connect=10.0, read=45.0),
                runtime_settings=runtime_settings,
                follow_redirects=False,
            ) as client:
                for candidate_url in candidate_urls:
                    try:
                        logger.info("attempt remote diff fetch review_url=%s candidate=%s", review_url, candidate_url)
                        diff_text = self.fetch_candidate_diff(client, candidate_url, headers)
                        if diff_text:
                            logger.info("remote diff fetch succeeded review_url=%s candidate=%s", review_url, candidate_url)
                            return diff_text
                    except Exception as error:
                        logger.warning("remote diff candidate failed for %s via %s: %s", review_url, candidate_url, error)
        except Exception as error:
            logger.warning("remote diff fetch failed for %s: %s", review_url, error)
        return ""

    def build_remote_diff_candidates(self, review_url: str) -> list[str]:
        parsed = urlparse(review_url)
        path = parsed.path.rstrip("/")
        if "/-/merge_requests/" in path:
            return [f"{review_url}.patch", f"{review_url}.diff"]
        if "/-/commit/" in path or "/commit/" in path:
            return [f"{review_url}.patch", f"{review_url}.diff"]
        return []

    def list_open_merge_requests(
        self,
        repo_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> list[OpenMergeRequest]:
        web_url = self._normalize_repo_url(repo_url)
        if not web_url:
            return []
        parsed = urlparse(web_url)
        project_path = self._extract_project_path(parsed.path)
        if not project_path:
            return []
        api_url = (
            f"{parsed.scheme}://{parsed.netloc}/api/v4/projects/{quote(project_path, safe='')}"
            "/merge_requests?state=opened&order_by=created_at&sort=asc&per_page=100"
        )
        headers = self.build_headers(access_token)
        if access_token:
            headers["PRIVATE-TOKEN"] = access_token
        try:
            with HttpClientFactory.create(
                timeout=httpx.Timeout(30.0, connect=10.0, read=30.0),
                runtime_settings=runtime_settings,
                follow_redirects=True,
            ) as client:
                response = client.get(api_url, headers=headers)
                response.raise_for_status()
                rows = response.json()
        except Exception as error:
            logger.warning("list gitlab/codehub open merge requests failed repo_url=%s error=%s", repo_url, error)
            return []

        merge_requests: list[OpenMergeRequest] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            mr_url = str(item.get("web_url") or "").strip()
            if not mr_url:
                continue
            merge_requests.append(
                OpenMergeRequest(
                    mr_url=mr_url,
                    title=str(item.get("title") or f"Merge Request !{item.get('iid', '')}").strip(),
                    source_ref=str(item.get("source_branch") or "").strip(),
                    target_ref=str(item.get("target_branch") or "main").strip() or "main",
                    number=str(item.get("iid") or item.get("id") or "").strip(),
                    head_sha=str(item.get("sha") or "").strip(),
                )
            )
        merge_requests.sort(key=lambda item: int(item.number) if item.number.isdigit() else 0)
        return merge_requests

    def _normalize_repo_url(self, repo_url: str) -> str:
        candidate = repo_url.strip()
        if not candidate:
            return ""
        if candidate.startswith("git@"):
            normalized = candidate.split("@", 1)[1]
            host, _, path = normalized.partition(":")
            candidate = f"https://{host}/{path}"
        elif "://" not in candidate:
            candidate = f"https://{candidate}"
        if candidate.endswith(".git"):
            candidate = candidate[: -len(".git")]
        return candidate

    def _extract_project_path(self, path: str) -> str:
        parts = [segment for segment in path.split("/") if segment]
        clean_parts: list[str] = []
        for segment in parts:
            if segment in {"-", "merge_requests", "pull", "commit"}:
                break
            clean_parts.append(segment)
        return "/".join(clean_parts)


class PlatformAdapter:
    """Platform-neutral review subject normalizer.

    当前仍是本地占位实现，但接口已经按设计文档抽成统一入口，后续可以替换成
    GitHub/GitLab/自建平台的真实 diff 拉取器。
    """

    def __init__(self, providers: list[ReviewPlatformProvider] | None = None) -> None:
        self.providers = providers or [
            GitHubReviewProvider(),
            GitLabReviewProvider(),
        ]

    def normalize(self, subject: ReviewSubject, runtime_settings: RuntimeSettings | None = None) -> ReviewSubject:
        """把外部 Git 平台输入归一化成统一的 ReviewSubject。"""
        review_url = subject.mr_url or subject.repo_url
        review_mode = self._infer_review_mode(subject.subject_type, review_url)
        provider = self._resolve_provider(review_url)
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
        metadata.setdefault("platform_kind", provider.platform_kind if provider else self._infer_platform_kind(review_url))
        metadata.setdefault("platform_provider", provider.__class__.__name__ if provider else "")
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

    def list_open_merge_requests(
        self,
        repo_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> list[OpenMergeRequest]:
        """从代码平台拉取仓库当前处于开启状态的 MR/PR 列表。"""

        provider = self._resolve_provider(repo_url)
        if provider is None:
            logger.info("skip listing open merge requests for unsupported repo_url=%s", repo_url)
            return []
        return provider.list_open_merge_requests(repo_url, access_token, runtime_settings)

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
        provider = self._resolve_provider(review_url)
        if provider is not None:
            return provider.platform_kind
        if "github.com" in review_url:
            return "github"
        return "gitlab_like"

    def _fetch_remote_diff(
        self,
        review_url: str,
        access_token: str,
        runtime_settings: RuntimeSettings | None = None,
    ) -> str:
        provider = self._resolve_provider(review_url)
        if provider is None:
            logger.info("skip remote diff fetch for unsupported url=%s", review_url)
            return ""
        return provider.fetch_remote_diff(review_url, access_token, runtime_settings)

    def _build_remote_diff_candidates(self, review_url: str) -> list[str]:
        provider = self._resolve_provider(review_url)
        if provider is not None:
            return provider.build_remote_diff_candidates(review_url)
        return []

    def _fetch_candidate_diff(
        self,
        client: httpx.Client,
        candidate_url: str,
        headers: dict[str, str],
    ) -> str:
        provider = self._resolve_provider(candidate_url)
        if provider is not None:
            return provider.fetch_candidate_diff(client, candidate_url, headers)
        return ""

    def _resolve_provider(self, review_url: str) -> ReviewPlatformProvider | None:
        if not review_url:
            return None
        for provider in self.providers:
            if provider.supports(review_url):
                return provider
        return None

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
