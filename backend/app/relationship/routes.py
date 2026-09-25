from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import Character, CharacterRelationship

router = APIRouter(prefix="/projects/{project_id}/character-relationships", tags=["character-relationships"])


class RelationshipCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_character_id: str
    target_character_id: str
    description: str = Field(min_length=1, max_length=5000)


class RelationshipEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    description: str = Field(min_length=1, max_length=5000)


class VersionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


def _data(item: CharacterRelationship, names: dict[str, str]) -> dict:
    return {
        "id": item.id,
        "source_character_id": item.source_character_id,
        "source_character_name": names.get(item.source_character_id, ""),
        "target_character_id": item.target_character_id,
        "target_character_name": names.get(item.target_character_id, ""),
        "description": item.description,
        "version": item.version,
        "created_at": item.created_at.isoformat(),
    }


def _names(db: Session, scope: ProjectScope) -> dict[str, str]:
    rows = db.execute(
        select(Character.id, Character.name).where(
            Character.workspace_id == scope.workspace_id,
            Character.project_id == scope.project_id,
        )
    ).all()
    return {character_id: name for character_id, name in rows}


@router.get("")
def list_relationships(
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "project.read")
    rows = db.scalars(
        select(CharacterRelationship)
        .where(
            CharacterRelationship.workspace_id == scope.workspace_id,
            CharacterRelationship.project_id == scope.project_id,
        )
        .order_by(CharacterRelationship.created_at)
    ).all()
    names = _names(db, scope)
    return ok(request, [_data(item, names) for item in rows])


@router.post("")
def create_relationship(
    payload: RelationshipCreate,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "content.edit")
    if payload.source_character_id == payload.target_character_id:
        raise APIError("INVALID_PARAMETER", "A character cannot relate to itself", 422)
    scoped_get(db, Character, payload.source_character_id, scope)
    scoped_get(db, Character, payload.target_character_id, scope)
    if db.scalar(
        select(CharacterRelationship.id).where(
            CharacterRelationship.workspace_id == scope.workspace_id,
            CharacterRelationship.project_id == scope.project_id,
            CharacterRelationship.source_character_id == payload.source_character_id,
            CharacterRelationship.target_character_id == payload.target_character_id,
        )
    ):
        raise APIError("RESOURCE_CONFLICT", "Character relationship already exists", 409)
    item = CharacterRelationship(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        source_character_id=payload.source_character_id,
        target_character_id=payload.target_character_id,
        description=payload.description.strip(),
    )
    db.add(item)
    db.flush()
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="character_relationship.create",
        resource_type="character_relationship",
        resource_id=item.id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise APIError("RESOURCE_CONFLICT", "Character relationship already exists", 409) from error
    return ok(request, _data(item, _names(db, scope)))


@router.patch("/{relationship_id}")
def edit_relationship(
    relationship_id: str,
    payload: RelationshipEdit,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "content.edit")
    item = db.scalar(
        select(CharacterRelationship)
        .where(
            CharacterRelationship.id == relationship_id,
            CharacterRelationship.workspace_id == scope.workspace_id,
            CharacterRelationship.project_id == scope.project_id,
        )
        .with_for_update()
    )
    if not item:
        raise APIError("RESOURCE_NOT_FOUND", "Character relationship not found", 404)
    if item.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Character relationship changed", 409)
    item.description = payload.description.strip()
    item.version += 1
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="character_relationship.edit",
        resource_type="character_relationship",
        resource_id=item.id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    db.commit()
    return ok(request, _data(item, _names(db, scope)))


@router.delete("/{relationship_id}")
def delete_relationship(
    relationship_id: str,
    payload: VersionPayload,
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "content.edit")
    item = db.scalar(
        select(CharacterRelationship)
        .where(
            CharacterRelationship.id == relationship_id,
            CharacterRelationship.workspace_id == scope.workspace_id,
            CharacterRelationship.project_id == scope.project_id,
        )
        .with_for_update()
    )
    if not item:
        raise APIError("RESOURCE_NOT_FOUND", "Character relationship not found", 404)
    if item.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Character relationship changed", 409)
    resource_id = item.id
    db.delete(item)
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="character_relationship.delete",
        resource_type="character_relationship",
        resource_id=resource_id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
    )
    db.commit()
    return ok(request, {"id": resource_id, "deleted": True})
