
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.asset.storage import store_result
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.generation.events import publish
from app.models import (
    Asset,
    Episode,
    Scene,
    Shot,
    Timeline,
    TimelineItem,
    TimelineTrack,
)
from app.timeline.render import render_timeline

router = APIRouter(prefix="/projects/{project_id}", tags=["timeline"])
TRACK_KINDS = ("VIDEO", "VOICE", "MUSIC", "SFX", "SUBTITLE")


class VersionPayload(BaseModel):
    expected_version: int


class TimelineAudioItemCreate(BaseModel):
    expected_version: int
    kind: str
    asset_id: str
    start_seconds: float = Field(default=0, ge=0)


class TimelineItemRemove(BaseModel):
    expected_version: int


def timeline_data(db: Session, timeline: Timeline) -> dict:
    tracks = db.scalars(select(TimelineTrack).where(TimelineTrack.workspace_id == timeline.workspace_id, TimelineTrack.project_id == timeline.project_id, TimelineTrack.timeline_id == timeline.id).order_by(TimelineTrack.order_no)).all()
    items = db.scalars(select(TimelineItem).where(TimelineItem.workspace_id == timeline.workspace_id, TimelineItem.project_id == timeline.project_id, TimelineItem.timeline_id == timeline.id).order_by(TimelineItem.start_seconds)).all()
    return {"id": timeline.id, "episode_id": timeline.episode_id, "status": timeline.status, "version": timeline.version, "final_asset_id": timeline.final_asset_id, "tracks": [{"id": t.id, "kind": t.kind, "order_no": t.order_no} for t in tracks], "items": [{"id": i.id, "track_id": i.track_id, "shot_id": i.shot_id, "asset_id": i.asset_id, "start_seconds": i.start_seconds, "duration_seconds": i.duration_seconds, "text": i.text} for i in items]}


def reviewed_video(shot: Shot, asset_id: str) -> bool:
    inspection = shot.inspection_json or {}
    if (inspection.get("checks") or {}).get("visible_text_language") == "FAIL":
        return False
    override = inspection.get("review_override") or {}
    return shot.status in {"APPROVED", "LOCKED"} and shot.current_video_asset_id == asset_id and (
        inspection.get("status") == "PASS" or (
            inspection.get("status") == "FAIL" and override.get("video_asset_id") == asset_id and bool(override.get("reason"))
        )
    )


@router.post("/episodes/{episode_id}/timeline")
def create_timeline(episode_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "timeline.edit")
    episode = db.scalar(
        select(Episode).where(
            Episode.id == episode_id,
            Episode.workspace_id == scope.workspace_id,
            Episode.project_id == scope.project_id,
        ).with_for_update()
    )
    if not episode:
        raise APIError("RESOURCE_NOT_FOUND", "Episode not found", 404)
    existing = db.scalar(select(Timeline).where(Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id, Timeline.episode_id == episode_id))
    if existing:
        return ok(request, timeline_data(db, existing))
    timeline = Timeline(workspace_id=scope.workspace_id, project_id=scope.project_id, episode_id=episode_id)
    try:
        db.add(timeline)
        db.flush()
        for order_no, kind in enumerate(TRACK_KINDS):
            db.add(TimelineTrack(workspace_id=scope.workspace_id, project_id=scope.project_id, timeline_id=timeline.id, kind=kind, order_no=order_no))
        record(db, actor_type="USER", actor_id=scope.user_id, action="timeline.create", resource_type="timeline", resource_id=timeline.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Timeline).where(
                Timeline.workspace_id == scope.workspace_id,
                Timeline.project_id == scope.project_id,
                Timeline.episode_id == episode_id,
            )
        )
        if existing:
            return ok(request, timeline_data(db, existing))
        raise
    return ok(request, timeline_data(db, timeline))


@router.get("/episodes/{episode_id}/timeline")
def get_episode_timeline(episode_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    scoped_get(db, Episode, episode_id, scope)
    timeline = db.scalar(select(Timeline).where(Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id, Timeline.episode_id == episode_id))
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline not found", 404)
    return ok(request, timeline_data(db, timeline))


@router.post("/timelines/{timeline_id}/audio-items")
def add_audio_item(timeline_id: str, payload: TimelineAudioItemCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "timeline.edit")
    if payload.kind not in {"MUSIC", "SFX"}:
        raise APIError("INVALID_PARAMETER", "Timeline audio kind must be MUSIC or SFX", 422)
    timeline = db.scalar(select(Timeline).where(Timeline.id == timeline_id, Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id).with_for_update())
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline not found", 404)
    if timeline.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Timeline changed", 409)
    asset = scoped_get(db, Asset, payload.asset_id, scope)
    if asset.status != "READY" or asset.mime != "audio/wav" or not asset.duration:
        raise APIError("INVALID_PARAMETER", "A ready WAV asset is required", 422)
    track = db.scalar(
        select(TimelineTrack).where(
            TimelineTrack.workspace_id == scope.workspace_id,
            TimelineTrack.project_id == scope.project_id,
            TimelineTrack.timeline_id == timeline.id,
            TimelineTrack.kind == payload.kind,
        )
    )
    if not track:
        raise APIError("RESOURCE_CONFLICT", "Timeline audio track is unavailable", 409)
    item = TimelineItem(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        timeline_id=timeline.id,
        track_id=track.id,
        asset_id=asset.id,
        start_seconds=payload.start_seconds,
        duration_seconds=float(asset.duration),
        text=payload.kind,
    )
    db.add(item)
    timeline.final_asset_id = None
    if timeline.status == "COMPLETED":
        timeline.status = "READY"
    timeline.version += 1
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="timeline.audio.add", resource_type="timeline_item", resource_id=item.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=payload.kind)
    db.commit()
    return ok(request, timeline_data(db, timeline))


@router.post("/timelines/{timeline_id}/items/{item_id}/remove")
def remove_timeline_item(timeline_id: str, item_id: str, payload: TimelineItemRemove, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "timeline.edit")
    timeline = db.scalar(select(Timeline).where(Timeline.id == timeline_id, Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id).with_for_update())
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline not found", 404)
    if timeline.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Timeline changed", 409)
    item = db.scalar(
        select(TimelineItem).where(
            TimelineItem.id == item_id,
            TimelineItem.workspace_id == scope.workspace_id,
            TimelineItem.project_id == scope.project_id,
            TimelineItem.timeline_id == timeline.id,
        )
    )
    if not item:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline item not found", 404)
    track_kind = db.scalar(
        select(TimelineTrack.kind).where(
            TimelineTrack.id == item.track_id,
            TimelineTrack.workspace_id == scope.workspace_id,
            TimelineTrack.project_id == scope.project_id,
            TimelineTrack.timeline_id == timeline.id,
        )
    )
    if track_kind not in {"MUSIC", "SFX"}:
        raise APIError("PERMISSION_DENIED", "Only manual audio items can be removed", 403)
    db.delete(item)
    timeline.final_asset_id = None
    if timeline.status == "COMPLETED":
        timeline.status = "READY"
    timeline.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="timeline.audio.remove", resource_type="timeline_item", resource_id=item.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=track_kind)
    db.commit()
    return ok(request, timeline_data(db, timeline))


@router.post("/timelines/{timeline_id}/sync")
def sync_timeline(timeline_id: str, payload: VersionPayload, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "timeline.edit")
    timeline = db.scalar(select(Timeline).where(Timeline.id == timeline_id, Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id).with_for_update())
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline not found", 404)
    if timeline.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Timeline changed", 409)
    shots = db.scalars(select(Shot).join(Scene, Scene.id == Shot.scene_id).where(Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id, Shot.episode_id == timeline.episode_id, Scene.workspace_id == scope.workspace_id, Scene.project_id == scope.project_id).order_by(Scene.scene_no, Shot.shot_no)).all()
    if not shots or any(not s.current_video_asset_id or not reviewed_video(s, s.current_video_asset_id) for s in shots):
        raise APIError("RESOURCE_CONFLICT", "Every shot needs an approved video", 409)
    tracks = {t.kind: t for t in db.scalars(select(TimelineTrack).where(TimelineTrack.workspace_id == scope.workspace_id, TimelineTrack.project_id == scope.project_id, TimelineTrack.timeline_id == timeline.id)).all()}
    generated_track_ids = [tracks[kind].id for kind in ("VIDEO", "VOICE", "SUBTITLE")]
    db.execute(
        delete(TimelineItem).where(
            TimelineItem.workspace_id == scope.workspace_id,
            TimelineItem.project_id == scope.project_id,
            TimelineItem.timeline_id == timeline.id,
            TimelineItem.track_id.in_(generated_track_ids),
        )
    )
    elapsed = 0.0
    for shot in shots:
        for kind, asset_id, text in (("VIDEO", shot.current_video_asset_id, ""), ("VOICE", shot.current_audio_asset_id, ""), ("SUBTITLE", None, shot.dialogue)):
            if asset_id or text:
                db.add(TimelineItem(workspace_id=scope.workspace_id, project_id=scope.project_id, timeline_id=timeline.id, track_id=tracks[kind].id, shot_id=shot.id, asset_id=asset_id, start_seconds=elapsed, duration_seconds=shot.duration, text=text))
        elapsed += shot.duration
    timeline.version += 1
    timeline.status = "READY"
    record(db, actor_type="USER", actor_id=scope.user_id, action="timeline.sync", resource_type="timeline", resource_id=timeline.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=f"{len(shots)} approved shots")
    db.commit()
    return ok(request, timeline_data(db, timeline))


@router.post("/timelines/{timeline_id}/render")
def render(timeline_id: str, payload: VersionPayload, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "timeline.edit")
    timeline = db.scalar(select(Timeline).where(Timeline.id == timeline_id, Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id).with_for_update())
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Timeline not found", 404)
    if timeline.version != payload.expected_version:
        raise APIError("RESOURCE_VERSION_CONFLICT", "Timeline changed", 409)
    if timeline.status != "READY":
        raise APIError("RESOURCE_CONFLICT", "Timeline is not ready", 409)
    tracks = {t.id: t.kind for t in db.scalars(select(TimelineTrack).where(TimelineTrack.workspace_id == scope.workspace_id, TimelineTrack.project_id == scope.project_id, TimelineTrack.timeline_id == timeline.id)).all()}
    items = db.scalars(select(TimelineItem).where(TimelineItem.workspace_id == scope.workspace_id, TimelineItem.project_id == scope.project_id, TimelineItem.timeline_id == timeline.id).order_by(TimelineItem.start_seconds)).all()
    video_items = [i for i in items if tracks[i.track_id] == "VIDEO"]
    for item in video_items:
        shot = scoped_get(db, Shot, item.shot_id, scope)
        if not reviewed_video(shot, item.asset_id):
            raise APIError("RESOURCE_CONFLICT", "Timeline video changed or needs review", 409)
    voice_by_shot = {i.shot_id: i.asset_id for i in items if tracks[i.track_id] == "VOICE"}
    subtitle_by_shot = {i.shot_id: i.text for i in items if tracks[i.track_id] == "SUBTITLE"}
    extra_audio = []
    for item in items:
        kind = tracks[item.track_id]
        if kind not in {"MUSIC", "SFX"}:
            continue
        if not item.asset_id:
            raise APIError("RESOURCE_CONFLICT", "Timeline audio item has no asset", 409)
        asset = scoped_get(db, Asset, item.asset_id, scope)
        if asset.status != "READY" or asset.mime != "audio/wav":
            raise APIError("RESOURCE_CONFLICT", "Timeline audio asset is unavailable", 409)
        extra_audio.append((asset, item.start_seconds, item.duration_seconds, kind))
    clips = []
    for item in video_items:
        video = scoped_get(db, Asset, item.asset_id, scope)
        voice = scoped_get(db, Asset, voice_by_shot[item.shot_id], scope) if voice_by_shot.get(item.shot_id) else None
        clips.append((video, voice, subtitle_by_shot.get(item.shot_id, ""), item.duration_seconds))
    publish(scope.project_id, "episode.rendering", {"timeline_id": timeline.id})
    result = render_timeline(clips, extra_audio)
    asset = store_result(workspace_id=scope.workspace_id, project_id=scope.project_id, job_id=f"timeline-{timeline.id}-v{timeline.version}", result=result, source_job_id=None)
    asset.source_job_id = None
    db.add(asset)
    db.flush()
    timeline.final_asset_id = asset.id
    timeline.status = "COMPLETED"
    timeline.version += 1
    record(db, actor_type="USER", actor_id=scope.user_id, action="timeline.render", resource_type="timeline", resource_id=timeline.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=f"{result.duration:.1f}s final video")
    db.commit()
    publish(scope.project_id, "episode.completed", {"timeline_id": timeline.id, "asset_id": asset.id})
    return ok(request, timeline_data(db, timeline))
