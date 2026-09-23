from dataclasses import dataclass

import jwt
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.auth.security import decode_access
from app.db import get_db
from app.models import Project, ProjectMember, ServerSession, User, WorkspaceMember, now


@dataclass(frozen=True)
class Identity:
    user_id: str
    session_id: str


@dataclass(frozen=True)
class ProjectScope:
    user_id: str
    session_id: str
    workspace_id: str
    project_id: str
    role: str


ROLE_PERMISSIONS = {
    "OWNER": {"project.read", "project.edit", "story.edit", "content.edit", "generation.create", "review.approve", "timeline.edit", "audit.read", "members.manage"},
    "DIRECTOR": {"project.read", "project.edit", "story.edit", "content.edit", "generation.create", "review.approve", "timeline.edit"},
    "EDITOR": {"project.read", "story.edit", "content.edit", "generation.create", "timeline.edit"},
    "REVIEWER": {"project.read", "review.approve"},
    "VIEWER": {"project.read"},
}


def current_identity(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Identity:
    if not authorization or not authorization.startswith("Bearer "):
        raise APIError("AUTH_REQUIRED", "Authentication required", 401)
    try:
        claims = decode_access(authorization[7:])
    except jwt.PyJWTError:
        raise APIError("AUTH_REQUIRED", "Invalid access token", 401) from None
    session = db.get(ServerSession, claims["sid"])
    user = db.get(User, claims["sub"])
    if not session or session.user_id != claims["sub"] or session.revoked_at or session.expires_at <= now() or not user or user.disabled:
        raise APIError("AUTH_REQUIRED", "Session expired", 401)
    return Identity(user_id=user.id, session_id=session.id)


def project_scope(project_id: str, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)) -> ProjectScope:
    project = db.get(Project, project_id)
    if not project:
        raise APIError("RESOURCE_NOT_FOUND", "Project not found", 404)
    wm = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == project.workspace_id, WorkspaceMember.user_id == identity.user_id))
    pm = db.scalar(select(ProjectMember).where(ProjectMember.workspace_id == project.workspace_id, ProjectMember.project_id == project.id, ProjectMember.user_id == identity.user_id))
    if not wm or not pm:
        raise APIError("RESOURCE_NOT_FOUND", "Project not found", 404)
    return ProjectScope(identity.user_id, identity.session_id, project.workspace_id, project.id, pm.role)


def require(scope: ProjectScope, permission: str) -> None:
    if permission not in ROLE_PERMISSIONS.get(scope.role, set()):
        raise APIError("PERMISSION_DENIED", "Permission denied", 403)


def scoped_get(db: Session, model, resource_id: str, scope: ProjectScope):
    obj = db.scalar(select(model).where(model.id == resource_id, model.workspace_id == scope.workspace_id, model.project_id == scope.project_id))
    if not obj:
        raise APIError("RESOURCE_NOT_FOUND", "Resource not found", 404)
    return obj
