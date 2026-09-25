from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import Character, CharacterVersion

router = APIRouter(prefix="/projects/{project_id}/characters", tags=["characters"])
DNA_FIELDS = {"face", "hair", "body", "costume", "accessories", "weapon", "color", "style", "voice", "reference_assets", "prompt_anchor", "negative_prompt"}


class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    background: str = ""
    dna: dict = Field(default_factory=dict)


class CharacterDraft(BaseModel):
    expected_version: int
    background: str = ""
    dna: dict = Field(default_factory=dict)


class VersionAction(BaseModel):
    expected_version: int


def validate_dna(dna: dict):
    if set(dna) - DNA_FIELDS:
        raise APIError("INVALID_PARAMETER", "Unknown Character DNA field", 422)


def version_data(v: CharacterVersion) -> dict:
    return {"id": v.id, "character_id": v.character_id, "version_no": v.version_no, "status": v.status, "dna": v.dna, "background": v.background}


def data(c: Character, versions: list[CharacterVersion] | None = None) -> dict:
    return {"id": c.id, "name": c.name, "active_version_id": c.active_version_id, "version": c.version, "versions": [version_data(v) for v in versions] if versions is not None else None}


@router.post("")
def create_character(payload: CharacterCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    validate_dna(payload.dna)
    character = Character(workspace_id=scope.workspace_id, project_id=scope.project_id, name=payload.name)
    db.add(character)
    db.flush()
    version = CharacterVersion(workspace_id=scope.workspace_id, project_id=scope.project_id, character_id=character.id, version_no=1, status="DRAFT", dna=payload.dna, background=payload.background, created_by=scope.user_id)
    db.add(version)
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="character.create", resource_type="character", resource_id=character.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, data(character, [version]))


@router.get("")
def list_characters(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    characters = db.scalars(select(Character).where(Character.workspace_id == scope.workspace_id, Character.project_id == scope.project_id).order_by(Character.created_at)).all()
    return ok(request, [data(c) for c in characters])


@router.get("/{character_id}")
def get_character(character_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    c = scoped_get(db, Character, character_id, scope)
    versions = db.scalars(select(CharacterVersion).where(CharacterVersion.workspace_id == scope.workspace_id, CharacterVersion.project_id == scope.project_id, CharacterVersion.character_id == c.id).order_by(CharacterVersion.version_no.desc())).all()
    return ok(request, data(c, versions))


@router.post("/{character_id}/versions")
def create_draft(character_id: str, payload: CharacterDraft, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    validate_dna(payload.dna)
    c = db.scalar(select(Character).where(Character.id == character_id, Character.workspace_id == scope.workspace_id, Character.project_id == scope.project_id).with_for_update())
    if not c:
        raise APIError("RESOURCE_NOT_FOUND", "Character not found", 404)
    if c.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Character changed", 409)
    version_no = db.scalar(select(func.max(CharacterVersion.version_no)).where(CharacterVersion.workspace_id == scope.workspace_id, CharacterVersion.project_id == scope.project_id, CharacterVersion.character_id == c.id)) + 1
    v = CharacterVersion(workspace_id=scope.workspace_id, project_id=scope.project_id, character_id=c.id, version_no=version_no, status="DRAFT", dna=payload.dna, background=payload.background, created_by=scope.user_id)
    db.add(v)
    c.version += 1
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="character.draft.create", resource_type="character_version", resource_id=v.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"character": data(c), "draft": version_data(v)})


@router.post("/{character_id}/versions/{version_id}/activate")
def activate(character_id: str, version_id: str, payload: VersionAction, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "review.approve")
    c = db.scalar(select(Character).where(Character.id == character_id, Character.workspace_id == scope.workspace_id, Character.project_id == scope.project_id).with_for_update())
    if not c:
        raise APIError("RESOURCE_NOT_FOUND", "Character not found", 404)
    if c.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Character changed", 409)
    v = scoped_get(db, CharacterVersion, version_id, scope)
    if v.character_id != c.id or v.status != "DRAFT":
        raise APIError("RESOURCE_CONFLICT", "Version cannot be activated", 409)
    if c.active_version_id:
        old = scoped_get(db, CharacterVersion, c.active_version_id, scope)
        old.status = "SUPERSEDED"
    v.status = "ACTIVE"
    c.active_version_id = v.id
    c.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="character.version.activate", resource_type="character_version", resource_id=v.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, data(c, [v]))
