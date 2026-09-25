from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require
from app.db import get_db
from app.models import ProjectBible

router = APIRouter(prefix="/projects/{project_id}/bibles", tags=["project-bible"])


class BibleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=200_000)


class BibleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content: str | None = Field(default=None, min_length=1, max_length=200_000)


class VersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def bible_data(item: ProjectBible) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "content": item.content,
        "version": item.version,
        "created_at": item.created_at.isoformat(),
    }


@router.get("")
def list_bibles(
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "project.read")
    rows = db.scalars(
        select(ProjectBible)
        .where(
            ProjectBible.workspace_id == scope.workspace_id,
            ProjectBible.project_id == scope.project_id,
        )
        .order_by(ProjectBible.created_at)
    ).all()
    return ok(request, [bible_data(item) for item in rows])


@router.post("")
def create_bible(
    payload: BibleCreate,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "story.edit")
    item = ProjectBible(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        title=payload.title.strip(),
        content=payload.content.strip(),
    )
    db.add(item)
    db.flush()
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="project_bible.create",
        resource_type="project_bible",
        resource_id=item.id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    db.commit()
    return ok(request, bible_data(item))


@router.patch("/{bible_id}")
def edit_bible(
    bible_id: str,
    payload: BibleEdit,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "story.edit")
    item = db.scalar(
        select(ProjectBible)
        .where(
            ProjectBible.id == bible_id,
            ProjectBible.workspace_id == scope.workspace_id,
            ProjectBible.project_id == scope.project_id,
        )
        .with_for_update()
    )
    if not item:
        raise APIError("RESOURCE_NOT_FOUND", "Project Bible entry not found", 404)
    if item.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Project Bible entry changed", 409)
    updates = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
    if "title" in updates:
        item.title = updates["title"].strip()
    if "content" in updates:
        item.content = updates["content"].strip()
    item.version += 1
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="project_bible.edit",
        resource_type="project_bible",
        resource_id=item.id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    db.commit()
    return ok(request, bible_data(item))


@router.delete("/{bible_id}")
def delete_bible(
    bible_id: str,
    payload: VersionPayload,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "story.edit")
    item = db.scalar(
        select(ProjectBible)
        .where(
            ProjectBible.id == bible_id,
            ProjectBible.workspace_id == scope.workspace_id,
            ProjectBible.project_id == scope.project_id,
        )
        .with_for_update()
    )
    if not item:
        raise APIError("RESOURCE_NOT_FOUND", "Project Bible entry not found", 404)
    if item.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Project Bible entry changed", 409)
    resource_id = item.id
    db.delete(item)
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="project_bible.delete",
        resource_type="project_bible",
        resource_id=resource_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    db.commit()
    return ok(request, {"id": resource_id, "deleted": True})
