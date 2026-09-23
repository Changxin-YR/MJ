from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import Character, CharacterVersion, Episode, Scene, Shot
from app.storyboard.state import transition_shot

router = APIRouter(prefix="/projects/{project_id}", tags=["storyboard"])


class EpisodeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = ""


class SceneCreate(BaseModel):
    heading: str = Field(min_length=1, max_length=200)
    description: str = ""


class SceneEdit(BaseModel):
    expected_version: int
    heading: str | None = None
    description: str | None = None


class ShotCreate(BaseModel):
    shot_type: str = "medium"
    camera_angle: str = "eye-level"
    camera_movement: str = "static"
    duration: float = Field(default=6, ge=1, le=30)
    description: str = ""
    action: str = ""
    dialogue: str = ""
    emotion: str = ""
    character_ids: list[str] = Field(default_factory=list)
    prompt: str = ""
    negative_prompt: str = ""


class ShotEdit(BaseModel):
    expected_version: int
    shot_type: str | None = None
    camera_angle: str | None = None
    camera_movement: str | None = None
    duration: float | None = Field(default=None, ge=1, le=30)
    description: str | None = None
    action: str | None = None
    dialogue: str | None = None
    emotion: str | None = None
    character_ids: list[str] | None = None
    prompt: str | None = None
    negative_prompt: str | None = None


class ShotAction(BaseModel):
    expected_version: int
    target: str


class Reorder(BaseModel):
    expected_version: int
    target_no: int = Field(ge=1)


def episode_data(e: Episode) -> dict:
    return {"id": e.id, "episode_no": e.episode_no, "title": e.title, "synopsis": e.synopsis, "status": e.status}


def scene_data(s: Scene) -> dict:
    return {"id": s.id, "episode_id": s.episode_id, "scene_no": s.scene_no, "heading": s.heading, "description": s.description, "version": s.version}


def shot_data(s: Shot) -> dict:
    return {name: getattr(s, name) for name in ("id", "episode_id", "scene_id", "shot_no", "shot_type", "camera_angle", "camera_movement", "duration", "description", "action", "dialogue", "emotion", "character_ids", "prompt", "negative_prompt", "current_image_asset_id", "current_video_asset_id", "current_audio_asset_id", "inspection_json", "status", "version")}


def next_no(db: Session, model, column, scope: ProjectScope, parent_column=None, parent_id=None):
    clauses = [model.workspace_id == scope.workspace_id, model.project_id == scope.project_id]
    if parent_column is not None:
        clauses.append(parent_column == parent_id)
    return (db.scalar(select(func.max(column)).where(*clauses)) or 0) + 1


def validate_character_ids(db: Session, scope: ProjectScope, ids: list[str]):
    for cid in set(ids):
        scoped_get(db, Character, cid, scope)


@router.post("/episodes")
def create_episode(payload: EpisodeCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    e = Episode(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_no=next_no(db, Episode, Episode.episode_no, scope), title=payload.title, synopsis=payload.synopsis)
    db.add(e)
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="episode.create", resource_type="episode", resource_id=e.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, episode_data(e))


@router.get("/episodes")
def list_episodes(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    rows = db.scalars(select(Episode).where(Episode.workspace_id == scope.workspace_id, Episode.project_id == scope.project_id).order_by(Episode.episode_no)).all()
    return ok(request, [episode_data(e) for e in rows])


@router.post("/episodes/{episode_id}/scenes")
def create_scene(episode_id: str, payload: SceneCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    scoped_get(db, Episode, episode_id, scope)
    scene = Scene(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_id=episode_id, scene_no=next_no(db, Scene, Scene.scene_no, scope, Scene.episode_id, episode_id), heading=payload.heading, description=payload.description)
    db.add(scene)
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="scene.create", resource_type="scene", resource_id=scene.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, scene_data(scene))


@router.get("/episodes/{episode_id}/scenes")
def list_scenes(episode_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    scoped_get(db, Episode, episode_id, scope)
    rows = db.scalars(select(Scene).where(Scene.workspace_id == scope.workspace_id, Scene.project_id == scope.project_id, Scene.episode_id == episode_id).order_by(Scene.scene_no)).all()
    return ok(request, [scene_data(s) for s in rows])


@router.patch("/scenes/{scene_id}")
def edit_scene(scene_id: str, payload: SceneEdit, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    scene = db.scalar(select(Scene).where(Scene.id == scene_id, Scene.workspace_id == scope.workspace_id, Scene.project_id == scope.project_id).with_for_update())
    if not scene:
        raise APIError("RESOURCE_NOT_FOUND", "Scene not found", 404)
    if scene.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Scene changed", 409)
    for key, value in payload.model_dump(exclude_unset=True, exclude={"expected_version"}).items():
        setattr(scene, key, value)
    scene.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="scene.edit", resource_type="scene", resource_id=scene.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, scene_data(scene))


@router.post("/scenes/{scene_id}/shots")
def create_shot(scene_id: str, payload: ShotCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    scene = scoped_get(db, Scene, scene_id, scope)
    validate_character_ids(db, scope, payload.character_ids)
    shot = Shot(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_id=scene.episode_id, scene_id=scene_id, shot_no=next_no(db, Shot, Shot.shot_no, scope, Shot.scene_id, scene_id), **payload.model_dump())
    db.add(shot)
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="shot.create", resource_type="shot", resource_id=shot.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, shot_data(shot))


@router.get("/scenes/{scene_id}/shots")
def list_shots(scene_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    scoped_get(db, Scene, scene_id, scope)
    rows = db.scalars(select(Shot).where(Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id, Shot.scene_id == scene_id).order_by(Shot.shot_no)).all()
    return ok(request, [shot_data(s) for s in rows])


@router.get("/shots/{shot_id}")
def get_shot(shot_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    return ok(request, shot_data(scoped_get(db, Shot, shot_id, scope)))


@router.patch("/shots/{shot_id}")
def edit_shot(shot_id: str, payload: ShotEdit, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    shot = db.scalar(select(Shot).where(Shot.id == shot_id, Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id).with_for_update())
    if not shot:
        raise APIError("RESOURCE_NOT_FOUND", "Shot not found", 404)
    if shot.status == "LOCKED":
        raise APIError("SHOT_LOCKED", "Shot is locked", 409)
    if shot.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Shot changed", 409)
    updates = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if "character_ids" in updates and updates["character_ids"] is not None:
        validate_character_ids(db, scope, updates["character_ids"])
    for key, value in updates.items():
        setattr(shot, key, value)
    shot.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="shot.edit", resource_type="shot", resource_id=shot.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, shot_data(shot))


@router.post("/shots/{shot_id}/transition")
def transition(shot_id: str, payload: ShotAction, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    if payload.target in {"APPROVED", "LOCKED"}:
        require(scope, "review.approve")
    elif payload.target in {"PLANNED", "STORYBOARD_READY", "REJECTED"}:
        require(scope, "content.edit")
    else:
        raise APIError("INVALID_PARAMETER", "Internal transition", 422)
    shot = db.scalar(select(Shot).where(Shot.id == shot_id, Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id).with_for_update())
    if not shot:
        raise APIError("RESOURCE_NOT_FOUND", "Shot not found", 404)
    if shot.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Shot changed", 409)
    transition_shot(shot, payload.target)
    record(db, actor_type="USER", actor_id=scope.user_id, action="shot.transition", resource_type="shot", resource_id=shot.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=payload.target)
    db.commit()
    return ok(request, shot_data(shot))


@router.post("/shots/{shot_id}/reorder")
def reorder(shot_id: str, payload: Reorder, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    shot = db.scalar(select(Shot).where(Shot.id == shot_id, Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id).with_for_update())
    if not shot:
        raise APIError("RESOURCE_NOT_FOUND", "Shot not found", 404)
    if shot.status == "LOCKED":
        raise APIError("SHOT_LOCKED", "Shot is locked", 409)
    if shot.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Shot changed", 409)
    other = db.scalar(select(Shot).where(Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id, Shot.scene_id == shot.scene_id, Shot.shot_no == payload.target_no).with_for_update())
    if not other:
        raise APIError("RESOURCE_NOT_FOUND", "Target position not found", 404)
    if other.id != shot.id:
        old_no = shot.shot_no
        shot.shot_no = -1
        db.flush()
        other.shot_no = old_no
        other.version += 1
        db.flush()
        shot.shot_no = payload.target_no
        shot.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="shot.reorder", resource_type="shot", resource_id=shot.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, shot_data(shot))


@router.get("/shots/{shot_id}/built-prompt")
def built_prompt(shot_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    shot = scoped_get(db, Shot, shot_id, scope)
    anchors, negatives = [], []
    for cid in shot.character_ids:
        c = scoped_get(db, Character, cid, scope)
        if c.active_version_id:
            v = scoped_get(db, CharacterVersion, c.active_version_id, scope)
            anchors.append(f"{c.name}: {v.dna.get('prompt_anchor', '')}; face {v.dna.get('face', '')}; hair {v.dna.get('hair', '')}; costume {v.dna.get('costume', '')}; style {v.dna.get('style', '')}")
            negatives.append(v.dna.get("negative_prompt", ""))
    return ok(request, {"prompt": ". ".join(filter(None, [shot.description, shot.action, shot.prompt, *anchors])), "negative_prompt": ", ".join(filter(None, [shot.negative_prompt, *negatives]))})
