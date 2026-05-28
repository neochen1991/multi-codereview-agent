from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.domain.models.runtime_settings import CodeRepositorySettings, ProjectSettings
import app.services.review_service as review_service_module

router = APIRouter()


class UpsertProjectRequest(BaseModel):
    """定义创建或更新项目租户时的请求体。"""

    project_id: str
    name: str
    description: str = ""
    owner_team: str = ""
    status: str = "active"
    repositories: list[CodeRepositorySettings] = Field(default_factory=list)


class UpdateProjectRepositoriesRequest(BaseModel):
    """定义项目下代码仓绑定的请求体。"""

    repositories: list[CodeRepositorySettings] = Field(default_factory=list)


@router.get("/projects")
def list_projects() -> dict[str, object]:
    """返回可选项目列表和当前默认项目。"""

    runtime = review_service_module.review_service.get_runtime_settings()
    return {
        "default_project_id": runtime.default_project_id,
        "projects": [item.model_dump(mode="json") for item in runtime.projects],
    }


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_project(payload: UpsertProjectRequest) -> dict[str, object]:
    """创建一个新项目。"""

    project = _normalize_project_payload(payload)
    runtime = review_service_module.review_service.get_runtime_settings()
    if any(item.project_id == project.project_id for item in runtime.projects):
        raise HTTPException(status_code=409, detail="project already exists")
    projects = [*runtime.projects, project]
    update_payload = runtime.model_dump(mode="json")
    update_payload["projects"] = [item.model_dump(mode="json") for item in projects]
    if not update_payload.get("default_project_id"):
        update_payload["default_project_id"] = project.project_id
    updated = review_service_module.review_service.update_runtime_settings(update_payload)
    saved = next(item for item in updated.projects if item.project_id == project.project_id)
    return saved.model_dump(mode="json")


@router.put("/projects/{project_id}")
def update_project(project_id: str, payload: UpsertProjectRequest) -> dict[str, object]:
    """更新项目基本信息和绑定仓库。"""

    normalized_project_id = _normalize_project_id(project_id)
    project = _normalize_project_payload(payload, fallback_project_id=normalized_project_id)
    if project.project_id != normalized_project_id:
        raise HTTPException(status_code=400, detail="project_id in path and body must match")
    runtime = review_service_module.review_service.get_runtime_settings()
    projects = []
    found = False
    for item in runtime.projects:
        if item.project_id == normalized_project_id:
            projects.append(project)
            found = True
        else:
            projects.append(item)
    if not found:
        raise HTTPException(status_code=404, detail="project not found")
    update_payload = runtime.model_dump(mode="json")
    update_payload["projects"] = [item.model_dump(mode="json") for item in projects]
    updated = review_service_module.review_service.update_runtime_settings(update_payload)
    saved = next(item for item in updated.projects if item.project_id == normalized_project_id)
    return saved.model_dump(mode="json")


@router.delete("/projects/{project_id}")
def delete_project(project_id: str) -> dict[str, object]:
    """删除项目配置。默认项目不能删除，已归档项目也可从配置中移除。"""

    normalized_project_id = _normalize_project_id(project_id)
    runtime = review_service_module.review_service.get_runtime_settings()
    if normalized_project_id == runtime.default_project_id:
        raise HTTPException(status_code=409, detail="default project can not be deleted")
    projects = [item for item in runtime.projects if item.project_id != normalized_project_id]
    if len(projects) == len(runtime.projects):
        raise HTTPException(status_code=404, detail="project not found")
    update_payload = runtime.model_dump(mode="json")
    update_payload["projects"] = [item.model_dump(mode="json") for item in projects]
    updated = review_service_module.review_service.update_runtime_settings(update_payload)
    return {
        "project_id": normalized_project_id,
        "status": "deleted",
        "default_project_id": updated.default_project_id,
    }


@router.put("/projects/{project_id}/repositories")
def update_project_repositories(project_id: str, payload: UpdateProjectRepositoriesRequest) -> dict[str, object]:
    """更新项目绑定的代码仓。"""

    normalized_project_id = _normalize_project_id(project_id)
    runtime = review_service_module.review_service.get_runtime_settings()
    projects = []
    found = False
    for item in runtime.projects:
        if item.project_id == normalized_project_id:
            projects.append(item.model_copy(update={"repositories": payload.repositories}))
            found = True
        else:
            projects.append(item)
    if not found:
        raise HTTPException(status_code=404, detail="project not found")
    update_payload = runtime.model_dump(mode="json")
    update_payload["projects"] = [item.model_dump(mode="json") for item in projects]
    updated = review_service_module.review_service.update_runtime_settings(update_payload)
    saved = next(item for item in updated.projects if item.project_id == normalized_project_id)
    return saved.model_dump(mode="json")


@router.put("/projects/default/{project_id}")
def set_default_project(project_id: str) -> dict[str, object]:
    """设置当前默认项目。代码仓始终挂在项目下，不再同步到全局仓库字段。"""

    normalized_project_id = _normalize_project_id(project_id)
    runtime = review_service_module.review_service.get_runtime_settings()
    project = next((item for item in runtime.projects if item.project_id == normalized_project_id), None)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    update_payload = runtime.model_dump(mode="json")
    update_payload["default_project_id"] = normalized_project_id
    updated = review_service_module.review_service.update_runtime_settings(update_payload)
    return {
        "default_project_id": updated.default_project_id,
        "projects": [item.model_dump(mode="json") for item in updated.projects],
    }


def _normalize_project_payload(payload: UpsertProjectRequest, *, fallback_project_id: str = "") -> ProjectSettings:
    project_id = _normalize_project_id(payload.project_id or fallback_project_id)
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    name = str(payload.name or "").strip() or project_id
    status_value = str(payload.status or "active").strip().lower()
    if status_value not in {"active", "archived"}:
        raise HTTPException(status_code=400, detail="project status must be active or archived")
    return ProjectSettings(
        project_id=project_id,
        name=name,
        description=str(payload.description or "").strip(),
        owner_team=str(payload.owner_team or "").strip(),
        status=status_value,
        repositories=list(payload.repositories),
    )


def _normalize_project_id(value: str) -> str:
    return str(value or "").strip()
