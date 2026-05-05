from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import RuntimeSettings
from app.repositories.fs import read_json, write_json
from app.services.repository_config_resolver import RepositoryConfigResolver

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewWorkspaceResult:
    status: str
    workspace_path: str
    base_repo_path: str
    repository_id: str
    review_id: str
    target_ref: str
    source_ref: str
    base_sha: str
    source_sha: str
    snapshot_commit: str
    snapshot_mode: str
    diff_hash: str
    message: str
    error_type: str = ""

    def model_dump(self) -> dict[str, object]:
        return asdict(self)


class ReviewWorkspaceService:
    """为 MR 审核构建隔离的合入后代码快照。"""

    def __init__(self, storage_root: Path, repository_resolver: RepositoryConfigResolver | None = None) -> None:
        self.storage_root = Path(storage_root)
        self.repository_resolver = repository_resolver or RepositoryConfigResolver()

    def prepare(
        self,
        *,
        review_id: str,
        subject: ReviewSubject,
        runtime: RuntimeSettings,
    ) -> ReviewWorkspaceResult:
        repository = self.repository_resolver.resolve(runtime, subject)
        repository_id = str(repository.repository_id or subject.repo_id or runtime.default_repository_id or "default-repository").strip()
        base_repo_path = str(repository.local_path or "").strip()
        target_ref = str(subject.target_ref or repository.default_branch or runtime.default_target_branch or "main").strip()
        source_ref = str(subject.source_ref or "").strip()
        diff_hash = self._diff_hash(subject.unified_diff)
        workspace_path = self._workspace_path(repository_id, review_id)

        if str(subject.subject_type or "").lower() != "mr":
            return self._result("skipped", workspace_path, base_repo_path, repository_id, review_id, target_ref, source_ref, diff_hash, "非 MR 任务无需构建快照。")
        if not str(subject.unified_diff or "").strip():
            return self._result("skipped", workspace_path, base_repo_path, repository_id, review_id, target_ref, source_ref, diff_hash, "当前 MR 缺少 diff，跳过快照构建。")
        base_repo = Path(base_repo_path).expanduser() if base_repo_path else Path()
        if not base_repo_path or not base_repo.exists() or not base_repo.is_dir():
            return self._result("failed", workspace_path, base_repo_path, repository_id, review_id, target_ref, source_ref, diff_hash, f"本地基础仓库不存在：{base_repo_path}")
        if not (base_repo / ".git").exists():
            return self._result("skipped", workspace_path, base_repo_path, repository_id, review_id, target_ref, source_ref, diff_hash, "本地基础仓库不是 Git 工作区，无法创建 MR 快照。")

        cached = self._cached_ready_workspace(workspace_path, diff_hash)
        if cached is not None:
            return cached

        self._best_effort_fetch(base_repo, target_ref)
        self._best_effort_fetch(base_repo, source_ref)
        for source_fetch_ref in self._source_fetch_refspecs(subject, source_ref):
            self._best_effort_fetch(base_repo, source_fetch_ref)
        base_ref = self._resolve_ref(base_repo, self._target_ref_candidates(target_ref))
        source_resolved_ref = self._resolve_ref(base_repo, self._source_ref_candidates(subject))
        if not base_ref:
            return self._result("failed", workspace_path, base_repo_path, repository_id, review_id, target_ref, source_ref, diff_hash, f"无法解析目标分支：{target_ref}")

        base_sha = self._git_commit(base_repo, base_ref)
        source_sha = self._git_commit(base_repo, source_resolved_ref) if source_resolved_ref else ""
        self._reset_workspace(base_repo, workspace_path)
        added = self._run_git(
            ["git", "worktree", "add", "--detach", str(workspace_path), base_ref],
            cwd=base_repo,
            timeout=self._timeout("REVIEW_WORKSPACE_WORKTREE_TIMEOUT_SECONDS", 600),
        )
        if added.returncode != 0:
            return self._result(
                "failed",
                workspace_path,
                base_repo_path,
                repository_id,
                review_id,
                target_ref,
                source_ref,
                diff_hash,
                f"创建 MR worktree 失败：{(added.stderr or added.stdout)[-800:]}",
                base_sha=base_sha,
                source_sha=source_sha,
            )

        if source_resolved_ref:
            merged = self._run_git(
                [
                    "git",
                    "-c",
                    "user.name=Code Review Agent",
                    "-c",
                    "user.email=code-review-agent@example.local",
                    "merge",
                    "--no-ff",
                    "--no-edit",
                    source_resolved_ref,
                ],
                cwd=workspace_path,
                timeout=self._timeout("REVIEW_WORKSPACE_MERGE_TIMEOUT_SECONDS", 600),
            )
            if merged.returncode == 0:
                return self._write_ready_meta(
                    workspace_path=workspace_path,
                    base_repo_path=base_repo_path,
                    repository_id=repository_id,
                    review_id=review_id,
                    target_ref=target_ref,
                    source_ref=source_ref,
                    base_sha=base_sha,
                    source_sha=source_sha,
                    snapshot_mode="merge",
                    diff_hash=diff_hash,
                    message="MR 快照已基于目标分支合入 source/head 代码。",
                )
            self._abort_merge(workspace_path)
            return self._result(
                "failed",
                workspace_path,
                base_repo_path,
                repository_id,
                review_id,
                target_ref,
                source_ref,
                diff_hash,
                f"MR source/head 合入快照失败，请先处理本地合并冲突：{(merged.stderr or merged.stdout)[-800:]}",
                base_sha=base_sha,
                source_sha=source_sha,
                snapshot_mode="merge_conflict",
            )

        applied = self._apply_diff(workspace_path, subject.unified_diff)
        if applied.returncode != 0:
            return self._result(
                "failed",
                workspace_path,
                base_repo_path,
                repository_id,
                review_id,
                target_ref,
                source_ref,
                diff_hash,
                f"无法 fetch MR source/head，且平台 diff apply 失败：{(applied.stderr or applied.stdout)[-800:]}",
                base_sha=base_sha,
                snapshot_mode="diff_apply_failed",
            )
        committed = self._commit_workspace_changes(workspace_path)
        if committed.returncode != 0:
            return self._result(
                "failed",
                workspace_path,
                base_repo_path,
                repository_id,
                review_id,
                target_ref,
                source_ref,
                diff_hash,
                f"平台 diff 已应用，但临时快照 commit 创建失败：{(committed.stderr or committed.stdout)[-800:]}",
                base_sha=base_sha,
                snapshot_mode="diff_commit_failed",
            )
        return self._write_ready_meta(
            workspace_path=workspace_path,
            base_repo_path=base_repo_path,
            repository_id=repository_id,
            review_id=review_id,
            target_ref=target_ref,
            source_ref=source_ref,
            base_sha=base_sha,
            source_sha="",
            snapshot_mode="diff_apply",
            diff_hash=diff_hash,
            message="MR 快照已基于目标分支应用平台 diff。",
        )

    def _write_ready_meta(
        self,
        *,
        workspace_path: Path,
        base_repo_path: str,
        repository_id: str,
        review_id: str,
        target_ref: str,
        source_ref: str,
        base_sha: str,
        source_sha: str,
        snapshot_mode: str,
        diff_hash: str,
        message: str,
    ) -> ReviewWorkspaceResult:
        snapshot_commit = self._git_commit(workspace_path, "HEAD")
        result = ReviewWorkspaceResult(
            status="ready",
            workspace_path=str(workspace_path),
            base_repo_path=base_repo_path,
            repository_id=repository_id,
            review_id=review_id,
            target_ref=target_ref,
            source_ref=source_ref,
            base_sha=base_sha,
            source_sha=source_sha,
            snapshot_commit=snapshot_commit,
            snapshot_mode=snapshot_mode,
            diff_hash=diff_hash,
            message=message,
        )
        meta_dir = workspace_path / ".review-workspace"
        meta_dir.mkdir(parents=True, exist_ok=True)
        write_json(meta_dir / "meta.json", {**result.model_dump(), "created_at": datetime.now(UTC).isoformat()})
        return result

    def _cached_ready_workspace(self, workspace_path: Path, diff_hash: str) -> ReviewWorkspaceResult | None:
        meta_path = workspace_path / ".review-workspace" / "meta.json"
        if not workspace_path.exists() or not meta_path.exists():
            return None
        try:
            payload = read_json(meta_path)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        if str(payload.get("status") or "") != "ready" or str(payload.get("diff_hash") or "") != diff_hash:
            return None
        return ReviewWorkspaceResult(
            status="ready",
            workspace_path=str(payload.get("workspace_path") or workspace_path),
            base_repo_path=str(payload.get("base_repo_path") or ""),
            repository_id=str(payload.get("repository_id") or ""),
            review_id=str(payload.get("review_id") or ""),
            target_ref=str(payload.get("target_ref") or ""),
            source_ref=str(payload.get("source_ref") or ""),
            base_sha=str(payload.get("base_sha") or ""),
            source_sha=str(payload.get("source_sha") or ""),
            snapshot_commit=str(payload.get("snapshot_commit") or ""),
            snapshot_mode=str(payload.get("snapshot_mode") or ""),
            diff_hash=str(payload.get("diff_hash") or ""),
            message="复用已有 MR 快照工作区。",
        )

    def _workspace_path(self, repository_id: str, review_id: str) -> Path:
        return self.storage_root / "rw" / self._safe_id(repository_id or "repo") / self._safe_id(review_id or "review")

    def cleanup_stale(self, *, older_than_days: int = 7) -> dict[str, object]:
        root = self.storage_root / "rw"
        if not root.exists():
            return {
                "root": str(root),
                "older_than_days": older_than_days,
                "removed": 0,
                "removed_count": 0,
                "removed_paths": [],
                "failed": [],
                "failed_count": 0,
                "failed_items": [],
            }
        cutoff = datetime.now(UTC).timestamp() - max(1, int(older_than_days or 7)) * 86400
        removed = 0
        removed_paths: list[str] = []
        failed: list[dict[str, str]] = []
        for workspace in [item for item in root.glob("*/*") if item.is_dir()]:
            try:
                if workspace.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            base_repo = self._base_repo_from_meta(workspace)
            if base_repo:
                self._run_git(
                    ["git", "worktree", "remove", "--force", str(workspace)],
                    cwd=base_repo,
                    timeout=self._timeout("REVIEW_WORKSPACE_CLEANUP_TIMEOUT_SECONDS", 300),
                )
                self._run_git(
                    ["git", "worktree", "prune"],
                    cwd=base_repo,
                    timeout=self._timeout("REVIEW_WORKSPACE_PRUNE_TIMEOUT_SECONDS", 120),
                )
            if workspace.exists():
                shutil.rmtree(workspace, ignore_errors=True)
            if workspace.exists():
                failed.append({"path": str(workspace), "error": "目录被占用或无权限删除"})
            else:
                removed += 1
                removed_paths.append(str(workspace))
        failed_items = failed[:20]
        return {
            "root": str(root),
            "older_than_days": older_than_days,
            "removed": removed,
            "removed_count": removed,
            "removed_paths": removed_paths[:50],
            "failed": failed_items,
            "failed_count": len(failed),
            "failed_items": failed_items,
        }

    def _base_repo_from_meta(self, workspace_path: Path) -> Path | None:
        meta_path = workspace_path / ".review-workspace" / "meta.json"
        if not meta_path.exists():
            return None
        try:
            payload = read_json(meta_path)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        raw = str(payload.get("base_repo_path") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.exists() and path.is_dir() else None

    def _reset_workspace(self, base_repo: Path, workspace_path: Path) -> None:
        if not workspace_path.exists():
            return
        self._run_git(
            ["git", "worktree", "remove", "--force", str(workspace_path)],
            cwd=base_repo,
            timeout=self._timeout("REVIEW_WORKSPACE_CLEANUP_TIMEOUT_SECONDS", 300),
        )
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)
        self._run_git(
            ["git", "worktree", "prune"],
            cwd=base_repo,
            timeout=self._timeout("REVIEW_WORKSPACE_PRUNE_TIMEOUT_SECONDS", 120),
        )

    def _apply_diff(self, workspace_path: Path, unified_diff: str) -> subprocess.CompletedProcess[str]:
        diff_path = workspace_path.parent / f"{workspace_path.name}.mr.diff"
        diff_path.write_text(str(unified_diff or ""), encoding="utf-8")
        attempts = [
            ["git", "apply", "--3way", "--whitespace=nowarn", str(diff_path)],
            ["git", "apply", "--whitespace=nowarn", str(diff_path)],
            ["git", "apply", "-p0", "--whitespace=nowarn", str(diff_path)],
            ["git", "apply", "--reject", "--whitespace=nowarn", str(diff_path)],
            ["git", "apply", "-p0", "--reject", "--whitespace=nowarn", str(diff_path)],
        ]
        failures: list[str] = []
        try:
            for index, command in enumerate(attempts):
                if index:
                    self._reset_diff_attempt(workspace_path)
                completed = self._run_git(
                    command,
                    cwd=workspace_path,
                    timeout=self._timeout("REVIEW_WORKSPACE_APPLY_TIMEOUT_SECONDS", 600),
                )
                if completed.returncode == 0:
                    logger.info("review workspace diff apply succeeded cwd=%s command=%s", workspace_path, " ".join(command))
                    return completed
                failure = (completed.stderr or completed.stdout or "").strip()
                failures.append(f"{' '.join(command[:-1])}: {failure[-800:]}")
                logger.warning(
                    "review workspace diff apply attempt failed cwd=%s command=%s return_code=%s stderr=%s",
                    workspace_path,
                    " ".join(command),
                    completed.returncode,
                    failure[-800:],
                )
            self._reset_diff_attempt(workspace_path)
            return subprocess.CompletedProcess(
                ["git", "apply"],
                returncode=1,
                stdout="",
                stderr="平台 diff 多策略应用失败：\n" + "\n".join(failures[-5:]),
            )
        finally:
            try:
                diff_path.unlink(missing_ok=True)
            except OSError:
                logger.debug("review workspace temp diff cleanup failed path=%s", diff_path)

    def _reset_diff_attempt(self, workspace_path: Path) -> None:
        self._run_git(
            ["git", "reset", "--hard", "HEAD"],
            cwd=workspace_path,
            timeout=self._timeout("REVIEW_WORKSPACE_APPLY_TIMEOUT_SECONDS", 600),
        )
        self._run_git(
            ["git", "clean", "-fd"],
            cwd=workspace_path,
            timeout=self._timeout("REVIEW_WORKSPACE_APPLY_TIMEOUT_SECONDS", 600),
        )

    def _commit_workspace_changes(self, workspace_path: Path) -> subprocess.CompletedProcess[str]:
        added = self._run_git(
            ["git", "add", "-A"],
            cwd=workspace_path,
            timeout=self._timeout("REVIEW_WORKSPACE_COMMIT_TIMEOUT_SECONDS", 300),
        )
        if added.returncode != 0:
            return added
        return self._run_git(
            [
                "git",
                "-c",
                "user.name=Code Review Agent",
                "-c",
                "user.email=code-review-agent@example.local",
                "commit",
                "-m",
                "review workspace diff snapshot",
            ],
            cwd=workspace_path,
            timeout=self._timeout("REVIEW_WORKSPACE_COMMIT_TIMEOUT_SECONDS", 300),
        )

    def _abort_merge(self, workspace_path: Path) -> None:
        self._run_git(
            ["git", "merge", "--abort"],
            cwd=workspace_path,
            timeout=self._timeout("REVIEW_WORKSPACE_MERGE_TIMEOUT_SECONDS", 600),
        )

    def _best_effort_fetch(self, base_repo: Path, ref: str) -> None:
        normalized = str(ref or "").strip()
        if not normalized:
            return
        self._run_git(
            ["git", "fetch", "origin", normalized],
            cwd=base_repo,
            timeout=self._timeout("REVIEW_WORKSPACE_FETCH_TIMEOUT_SECONDS", 600),
        )

    def _resolve_ref(self, repo_path: Path, candidates: list[str]) -> str:
        for candidate in candidates:
            if self._git_commit(repo_path, candidate):
                return candidate
        return ""

    def _git_commit(self, repo_path: Path, ref: str) -> str:
        if not ref:
            return ""
        completed = self._run_git(
            ["git", "rev-parse", ref],
            cwd=repo_path,
            timeout=self._timeout("REVIEW_WORKSPACE_GIT_LOOKUP_TIMEOUT_SECONDS", 30),
        )
        if completed.returncode != 0:
            return ""
        return str(completed.stdout or "").strip()

    def _run_git(self, command: list[str], *, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
        logger.info("review workspace git command cwd=%s timeout=%s command=%s", cwd, timeout, " ".join(command))
        try:
            return subprocess.run(
                command,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except Exception as error:
            return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr=f"{error.__class__.__name__}: {error}")

    def _timeout(self, env_name: str, default_seconds: int) -> int:
        try:
            return max(1, int(os.getenv(env_name, str(default_seconds)) or default_seconds))
        except (TypeError, ValueError):
            return int(default_seconds)

    def _source_ref_candidates(self, subject: ReviewSubject) -> list[str]:
        metadata = dict(subject.metadata or {})
        source_ref = str(subject.source_ref or "").strip()
        mr_number = self._infer_mr_number(subject, source_ref)
        values = [
            *[str(item or "").strip() for item in list(subject.commits or [])],
            str(metadata.get("auto_queue_head_sha") or "").strip(),
            str(metadata.get("head_sha") or "").strip(),
            *self._ref_aliases(source_ref),
        ]
        if mr_number:
            values.extend(
                [
                    f"refs/remotes/origin/mr/{mr_number}",
                    f"origin/mr/{mr_number}",
                    f"refs/remotes/origin/pr/{mr_number}",
                    f"origin/pr/{mr_number}",
                ]
            )
        if source_ref:
            values.append("FETCH_HEAD")
        return self._dedupe(values)

    def _source_fetch_refspecs(self, subject: ReviewSubject, source_ref: str) -> list[str]:
        mr_number = self._infer_mr_number(subject, source_ref)
        if not mr_number:
            return []
        return self._dedupe(
            [
                f"refs/merge-requests/{mr_number}/head:refs/remotes/origin/mr/{mr_number}",
                f"refs/pull/{mr_number}/head:refs/remotes/origin/pr/{mr_number}",
            ]
        )

    def _infer_mr_number(self, subject: ReviewSubject, source_ref: str) -> str:
        metadata = dict(subject.metadata or {})
        for value in (
            source_ref,
            str(metadata.get("mr_number") or ""),
            str(metadata.get("mr_id") or ""),
            str(metadata.get("iid") or ""),
            str(metadata.get("number") or ""),
            str(subject.mr_url or ""),
            str(subject.repo_url or ""),
        ):
            number = self._extract_mr_number(value)
            if number:
                return number
        return ""

    def _extract_mr_number(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        normalized = text.replace("\\", "/")
        markers = ["/merge_requests/", "/pull/", "mr/", "pr/"]
        for marker in markers:
            if marker not in normalized:
                continue
            tail = normalized.split(marker, 1)[1]
            digits = []
            for char in tail:
                if char.isdigit():
                    digits.append(char)
                    continue
                break
            if digits:
                return "".join(digits)
        return text if text.isdigit() else ""

    def _target_ref_candidates(self, target_ref: str) -> list[str]:
        values = self._ref_aliases(str(target_ref or "").strip())
        if target_ref not in {"main", "master"}:
            values.extend(self._ref_aliases("main"))
            values.extend(self._ref_aliases("master"))
        values.append("HEAD")
        return self._dedupe(values)

    def _ref_aliases(self, ref: str) -> list[str]:
        normalized = str(ref or "").strip()
        if not normalized:
            return []
        if normalized.startswith("refs/"):
            return [normalized]
        return [
            normalized,
            f"refs/heads/{normalized}",
            f"origin/{normalized}",
            f"refs/remotes/origin/{normalized}",
            f"upstream/{normalized}",
            f"refs/remotes/upstream/{normalized}",
        ]

    def _result(
        self,
        status: str,
        workspace_path: Path,
        base_repo_path: str,
        repository_id: str,
        review_id: str,
        target_ref: str,
        source_ref: str,
        diff_hash: str,
        message: str,
        *,
        base_sha: str = "",
        source_sha: str = "",
        snapshot_commit: str = "",
        snapshot_mode: str = "",
        error_type: str = "",
    ) -> ReviewWorkspaceResult:
        return ReviewWorkspaceResult(
            status=status,
            workspace_path=str(workspace_path),
            base_repo_path=base_repo_path,
            repository_id=repository_id,
            review_id=review_id,
            target_ref=target_ref,
            source_ref=source_ref,
            base_sha=base_sha,
            source_sha=source_sha,
            snapshot_commit=snapshot_commit,
            snapshot_mode=snapshot_mode,
            diff_hash=diff_hash,
            message=message,
            error_type=error_type,
        )

    def _diff_hash(self, unified_diff: str) -> str:
        return hashlib.sha256(str(unified_diff or "").encode("utf-8", errors="ignore")).hexdigest()

    def _safe_id(self, value: str) -> str:
        text = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in str(value or "").strip())
        return text[:80] or "item"

    def _dedupe(self, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result
