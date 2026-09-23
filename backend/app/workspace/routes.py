from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import (
    Identity,
    ProjectScope,
    current_identity,
    project_scope,
    require,
)
from app.db import get_db
from app.models import Project, ProjectMember, User, Workspace, WorkspaceMember

router = APIRouter(tags=["workspace"])


class NamePayload(BaseModel):
    name: str = Field(min_length=1, max_length=180)


class ProjectCreate(NamePayload):
    description: str = Field(default="", max_length=5000)


class MemberAdd(BaseModel):
    email: str
    role: str


class SettingsUpdate(BaseModel):
    expected_version: int
    settings: dict
    budget_limit: float | None = Field(default=None, ge=0)


def project_data(project: Project, role: str) -> dict:
    return {"id": project.id, "workspace_id": project.workspace_id, "name": project.name, "description": project.description, "version": project.version, "settings": project.settings_json, "budget_limit": float(project.budget_limit), "budget_used": float(project.budget_used), "budget_reserved": float(project.budget_reserved), "role": role}


@router.post("/workspaces")
def create_workspace(payload: NamePayload, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    workspace = Workspace(name=payload.name, owner_id=identity.user_id)
    db.add(workspace)
    db.flush()
    db.add(WorkspaceMember(workspace_id=workspace.id, user_id=identity.user_id, role="OWNER"))
    record(db, actor_type="USER", actor_id=identity.user_id, action="workspace.create", resource_type="workspace", resource_id=workspace.id, workspace_id=workspace.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"id": workspace.id, "name": workspace.name, "role": "OWNER"})


@router.get("/workspaces")
def list_workspaces(request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    rows = db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id).where(WorkspaceMember.user_id == identity.user_id)).all()
    return ok(request, [{"id": w.id, "name": w.name, "role": role} for w, role in rows])


@router.post("/workspaces/{workspace_id}/members")
def add_workspace_member(workspace_id: str, payload: MemberAdd, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    membership = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == identity.user_id))
    if not membership or membership.role != "OWNER":
        raise APIError("PERMISSION_DENIED", "Workspace owner required", 403)
    if payload.role not in {"OWNER", "MEMBER"}:
        raise APIError("INVALID_PARAMETER", "Invalid workspace role")
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user:
        raise APIError("RESOURCE_NOT_FOUND", "User not found", 404)
    existing = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user.id))
    if existing:
        raise APIError("RESOURCE_CONFLICT", "Already a member", 409)
    db.add(WorkspaceMember(workspace_id=workspace_id, user_id=user.id, role=payload.role))
    record(db, actor_type="USER", actor_id=identity.user_id, action="workspace.member.add", resource_type="user", resource_id=user.id, workspace_id=workspace_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"user_id": user.id, "role": payload.role})


@router.post("/workspaces/{workspace_id}/projects")
def create_project(workspace_id: str, payload: ProjectCreate, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    membership = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == identity.user_id))
    if not membership or membership.role != "OWNER":
        raise APIError("PERMISSION_DENIED", "Workspace owner required", 403)
    project = Project(workspace_id=workspace_id, name=payload.name, description=payload.description, settings_json={})
    db.add(project)
    db.flush()
    db.add(ProjectMember(workspace_id=workspace_id, project_id=project.id, user_id=identity.user_id, role="OWNER"))
    record(db, actor_type="USER", actor_id=identity.user_id, action="project.create", resource_type="project", resource_id=project.id, workspace_id=workspace_id, project_id=project.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, project_data(project, "OWNER"))


@router.get("/workspaces/{workspace_id}/projects")
def list_projects(workspace_id: str, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    rows = db.execute(select(Project, ProjectMember.role).join(ProjectMember, ProjectMember.project_id == Project.id).join(WorkspaceMember, WorkspaceMember.workspace_id == Project.workspace_id).where(Project.workspace_id == workspace_id, ProjectMember.user_id == identity.user_id, WorkspaceMember.user_id == identity.user_id)).all()
    return ok(request, [project_data(p, role) for p, role in rows])


@router.get("/projects/{project_id}")
def get_project(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    return ok(request, project_data(db.get(Project, scope.project_id), scope.role))


@router.patch("/projects/{project_id}/settings")
def update_settings(payload: SettingsUpdate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.edit")
    project = db.scalar(select(Project).where(Project.id == scope.project_id, Project.workspace_id == scope.workspace_id).with_for_update())
    if project.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Project settings changed", 409)
    project.settings_json = payload.settings
    if payload.budget_limit is not None:
        if scope.role != "OWNER":
            raise APIError("PERMISSION_DENIED", "Only owner can change budget", 403)
        if payload.budget_limit < float(project.budget_used) + float(project.budget_reserved):
            raise APIError("BUDGET_EXCEEDED", "Limit is below current usage", 409)
        project.budget_limit = payload.budget_limit
    project.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="project.settings.update", resource_type="project", resource_id=project.id, workspace_id=scope.workspace_id, project_id=project.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, project_data(project, scope.role))


@router.post("/projects/{project_id}/members")
def add_project_member(payload: MemberAdd, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "members.manage")
    if payload.role not in {"OWNER", "DIRECTOR", "EDITOR", "REVIEWER", "VIEWER"}:
        raise APIError("INVALID_PARAMETER", "Invalid project role")
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == scope.workspace_id, WorkspaceMember.user_id == user.id)):
        raise APIError("RESOURCE_NOT_FOUND", "Workspace member not found", 404)
    if db.scalar(select(ProjectMember).where(ProjectMember.project_id == scope.project_id, ProjectMember.user_id == user.id)):
        raise APIError("RESOURCE_CONFLICT", "Already a project member", 409)
    db.add(ProjectMember(workspace_id=scope.workspace_id, project_id=scope.project_id, user_id=user.id, role=payload.role))
    record(db, actor_type="USER", actor_id=scope.user_id, action="project.member.add", resource_type="user", resource_id=user.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"user_id": user.id, "role": payload.role})
