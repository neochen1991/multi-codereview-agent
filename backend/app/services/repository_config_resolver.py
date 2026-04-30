from __future__ import annotations

from dataclasses import dataclass

from app.domain.models.review import ReviewSubject
from app.domain.models.runtime_settings import CodeRepositorySettings, RuntimeSettings
from app.services.repository_context_service import RepositoryContextService


@dataclass(frozen=True)
class ResolvedRepository:
    """一次审核实际使用的代码仓配置。"""

    repository_id: str
    name: str
    provider: str
    clone_url: str
    local_path: str
    default_branch: str
    auto_sync: bool
    gitnexus_enabled: bool


class RepositoryConfigResolver:
    """按 review subject 从多仓配置中解析当前代码仓。"""

    def resolve(self, runtime: RuntimeSettings, subject: ReviewSubject) -> ResolvedRepository:
        metadata = dict(subject.metadata or {})
        repository_id = str(metadata.get("repository_id") or subject.repo_id or "").strip()
        repo = runtime.resolve_repository(
            repository_id=repository_id,
            repo_url=subject.repo_url,
            mr_url=subject.mr_url,
        )
        if repo is None:
            repo = CodeRepositorySettings(
                repository_id=repository_id or runtime.default_repository_id or "default-repository",
                clone_url=runtime.code_repo_clone_url,
                local_path=runtime.code_repo_local_path,
                default_branch=runtime.code_repo_default_branch or runtime.default_target_branch or "main",
                auto_sync=runtime.code_repo_auto_sync,
                gitnexus_enabled=True,
            )
        return ResolvedRepository(
            repository_id=repo.repository_id or repository_id or "default-repository",
            name=repo.name or repo.repository_id or repository_id or "默认代码仓",
            provider=repo.provider,
            clone_url=repo.clone_url,
            local_path=repo.local_path,
            default_branch=repo.default_branch or runtime.default_target_branch or "main",
            auto_sync=repo.auto_sync,
            gitnexus_enabled=repo.gitnexus_enabled,
        )

    def build_context_service(
        self,
        runtime: RuntimeSettings,
        subject: ReviewSubject | dict[str, object] | None = None,
        access_token: str = "",
    ) -> RepositoryContextService:
        review_subject = (
            subject
            if isinstance(subject, ReviewSubject)
            else ReviewSubject.model_validate(
                subject
                or {
                    "subject_type": "branch",
                    "repo_id": runtime.default_repository_id or "default-repository",
                    "project_id": "",
                    "source_ref": "",
                    "target_ref": runtime.default_target_branch or "main",
                }
            )
        )
        repo = self.resolve(runtime, review_subject)
        return RepositoryContextService.from_review_context(
            clone_url=repo.clone_url,
            local_path=repo.local_path,
            default_branch=repo.default_branch or runtime.default_target_branch,
            access_token=access_token or runtime.code_repo_access_token,
            auto_sync=repo.auto_sync,
            subject=review_subject,
        )
