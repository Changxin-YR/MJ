"""Restartable production coordinator; durable database state drives every step."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.agent.production import initialize_run
from app.api.errors import APIError
from app.asset.storage import store_result, validate
from app.audit.service import record
from app.auth.dependencies import ProjectScope, require
from app.db import SessionLocal
from app.generation.service import request_generation
from app.models import (
    AgentRun,
    Asset,
    Episode,
    GenerationJob,
    OutboxEvent,
    ProjectMember,
    Shot,
    Timeline,
    TimelineItem,
    TimelineTrack,
    User,
    WorkspaceMember,
    now,
)
from app.storyboard.state import transition_shot
from app.timeline.render import render_timeline


def scope_for_run(db, run):
    member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == run.project_id, ProjectMember.workspace_id == run.workspace_id, ProjectMember.user_id == run.user_id))
    workspace_member = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == run.workspace_id, WorkspaceMember.user_id == run.user_id))
    user = db.get(User, run.user_id)
    if not member or not workspace_member or not user or user.disabled:
        raise APIError("PERMISSION_DENIED", "Production owner no longer has project access", 403)
    scope = ProjectScope(run.user_id, run.session_id, run.workspace_id, run.project_id, member.role)
    require(scope, "generation.create")
    require(scope, "timeline.edit")
    return scope


def append_warnings(run, warnings: list[str], limit: int = 50) -> None:
    """Keep coordinator warnings useful across repeated polling ticks."""
    current = list(run.warnings or [])
    for warning in warnings:
        text = str(warning).strip()
        if text and text not in current:
            current.append(text)
    run.warnings = current[-limit:]


def recover_job(db, job):
    """Redeliver expired work without replacing the job or reserving twice."""
    if job.status != "RUNNING" or not job.lease_expires_at or job.lease_expires_at > now():
        return
    key = f"production:recover:{job.id}:{job.lease_expires_at.isoformat()}"
    if not db.scalar(select(OutboxEvent.id).where(OutboxEvent.deduplication_key == key)):
        db.add(OutboxEvent(workspace_id=job.workspace_id, project_id=job.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=key))


def finish_episode(db, run, episode_id, shots):
    db.scalar(select(Episode).where(Episode.id == episode_id, Episode.workspace_id == run.workspace_id, Episode.project_id == run.project_id).with_for_update())
    timeline = db.scalar(select(Timeline).where(Timeline.workspace_id == run.workspace_id, Timeline.project_id == run.project_id, Timeline.episode_id == episode_id).with_for_update())
    if timeline and timeline.status == "COMPLETED" and timeline.final_asset_id:
        return {"episode_id": episode_id, "timeline_id": timeline.id, "asset_id": timeline.final_asset_id}
    if timeline:
        raise APIError("TIMELINE_CONFLICT", "Automatic production timeline was modified", 409)
    timeline = Timeline(workspace_id=run.workspace_id, project_id=run.project_id, episode_id=episode_id, status="RENDERING")
    db.add(timeline)
    db.flush()
    tracks = {}
    for order, kind in enumerate(("VIDEO", "VOICE", "MUSIC", "SFX", "SUBTITLE")):
        track = TimelineTrack(workspace_id=run.workspace_id, project_id=run.project_id, timeline_id=timeline.id, kind=kind, order_no=order)
        db.add(track)
        db.flush()
        tracks[kind] = track.id
    clips = []
    elapsed = 0.0
    for shot in shots:
        def asset(asset_id):
            row = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == run.workspace_id, Asset.project_id == run.project_id, Asset.status == "READY"))
            if not row:
                raise APIError("ASSET_MISSING", "Production asset unavailable", 409)
            return row
        video = asset(shot.current_video_asset_id)
        voice = asset(shot.current_audio_asset_id) if shot.dialogue.strip() else None
        duration = max(shot.duration, float(voice.duration or 0) if voice else 0)
        clips.append((video, voice, shot.dialogue, duration))
        for kind, asset_id, text in (("VIDEO", video.id, ""), ("VOICE", voice.id if voice else None, ""), ("SUBTITLE", None, shot.dialogue)):
            if asset_id or text:
                db.add(TimelineItem(workspace_id=run.workspace_id, project_id=run.project_id, timeline_id=timeline.id, track_id=tracks[kind], shot_id=shot.id, asset_id=asset_id, start_seconds=elapsed, duration_seconds=duration, text=text))
        elapsed += duration
    result = render_timeline(clips)
    validate(result)
    # Timeline renders are derived assets and have no generation-job UUID.
    final = store_result(workspace_id=run.workspace_id, project_id=run.project_id, job_id=timeline.id, result=result, source_job_id=None)
    db.add(final)
    db.flush()
    timeline.final_asset_id = final.id
    timeline.status = "COMPLETED"
    timeline.version += 1
    record(db, actor_type="AGENT", actor_id=run.id, action="production.render", resource_type="timeline", resource_id=timeline.id, workspace_id=run.workspace_id, project_id=run.project_id, trace_id=run.trace_id, agent_run_id=run.id, safe_summary=f"{elapsed:.1f}s final video")
    return {"episode_id": episode_id, "timeline_id": timeline.id, "asset_id": final.id}


def coordinate(db, run):
    scope = scope_for_run(db, run)
    data = dict(run.production_json)
    shot_ids = data.get("shot_ids", [])
    if not shot_ids:
        raise APIError("PRODUCTION_INVALID", "Production contains no shots", 409)
    rows = db.scalars(select(Shot).where(Shot.id.in_(shot_ids), Shot.workspace_id == run.workspace_id, Shot.project_id == run.project_id)).all()
    by_id = {s.id: s for s in rows}
    if len(by_id) != len(shot_ids):
        raise APIError("PRODUCTION_INVALID", "Production shot is missing", 409)
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.agent_run_id == run.id, GenerationJob.workspace_id == run.workspace_id, GenerationJob.project_id == run.project_id).order_by(GenerationJob.created_at, GenerationJob.id)).all()
    run.estimated_cost = sum((Decimal(j.estimated_cost) for j in jobs), Decimal(0))
    limit = data.get("options", {}).get("max_quality_retries", 2)
    ready = []
    for shot_id in shot_ids:
        shot = by_id[shot_id]
        all_ready = True
        for kind in ("IMAGE", "VIDEO", "VOICE"):
            if kind == "VOICE" and not shot.dialogue.strip():
                continue
            history = [j for j in jobs if j.resource_id == shot_id and j.kind == kind]
            history.sort(key=lambda j: (j.input_json.get("production_attempt", 0), j.created_at, j.id))
            job = history[-1] if history else None
            if job and job.status in {"FAILED", "CANCELLED"}:
                raise APIError("MEDIA_FAILED", f"{kind} task {job.id}: {job.error_code or job.status}", 409)
            if job and job.status != "SUCCEEDED":
                recover_job(db, job)
                all_ready = False
                break
            if job is None:
                queued = request_generation(
                    db,
                    scope,
                    shot.id,
                    kind,
                    f"autoproduce:{run.id}:{kind.lower()}:{shot.id}:0",
                    run.trace_id,
                    agent_run_id=run.id,
                )
                queued.input_json = {**queued.input_json, "production_attempt": 0}
                all_ready = False
                break
            inspection = job.inspection_json or {}
            # Vision models can over-report soft continuity issues (for example,
            # counting incidental background figures). Only objective defects or
            # a directly visible story contradiction should stop unattended
            # production; preserve all other findings as audit warnings.
            # Keep model findings advisory during unattended production. A
            # human can review the rendered shots later; only failed provider
            # jobs or missing media are execution blockers.
            hard_fail = inspection.get("status") == "FAIL" and not data.get("unattended", False)
            if inspection.get("status") in {"PASS", "UNVERIFIED"} or not hard_fail:
                if inspection.get("status") == "FAIL":
                    append_warnings(run, [str(issue) for issue in inspection.get("issues", [])[:2]])
                if inspection.get("status") == "UNVERIFIED":
                    append_warnings(run, [str(issue) for issue in inspection.get("issues", [])[:1]])
                continue
            if job and len(history) > limit:
                detail = "; ".join(str(issue) for issue in inspection.get("issues", [])[:2])
                raise APIError("QUALITY_RETRIES_EXHAUSTED", f"{kind} shot {shot.id} did not pass automatic inspection{': ' + detail if detail else ''}", 409)
            # A new attempt keeps the rejected artifact and its inspection for audit.
            queued = request_generation(db, scope, shot.id, kind, f"autoproduce:{run.id}:{kind.lower()}:{shot.id}:{len(history)}", run.trace_id, agent_run_id=run.id)
            queued.input_json = {**queued.input_json, "production_attempt": len(history)}
            all_ready = False
            break
        if all_ready:
            if shot.status == "REVIEW_REQUIRED":
                transition_shot(shot, "APPROVED")
                record(db, actor_type="AGENT", actor_id=run.id, action="production.auto_approve", resource_type="shot", resource_id=shot.id, workspace_id=run.workspace_id, project_id=run.project_id, trace_id=run.trace_id, agent_run_id=run.id, safe_summary="Automatic media checks passed")
            if shot.status not in {"APPROVED", "LOCKED"}:
                raise APIError("SHOT_CHANGED", "Production shot state was modified", 409)
            ready.append(shot)
    data["progress"] = {"ready_shots": len(ready), "total_shots": len(shot_ids)}
    data["stage"] = "MEDIA_GENERATING"
    run.current_node = "Automatic Media Production"
    if len(ready) == len(shot_ids):
        data["stage"] = "RENDERING"
        run.current_node = "Render Episodes"
        run.production_json = data
        db.flush()
        finals = []
        for episode_id in dict.fromkeys(s.episode_id for s in ready):
            finals.append(finish_episode(db, run, episode_id, [s for s in ready if s.episode_id == episode_id]))
        data["finals"] = finals
        data["stage"] = "COMPLETED"
        run.status = "COMPLETED"
        run.current_node = "Production Completed"
        run.decision_summary = f"自动制作完成：{len(ready)} 个镜头、{len(finals)} 集成片，已包含配音与字幕。"
    else:
        run.decision_summary = f"自动制作中：{len(ready)}/{len(shot_ids)} 个镜头已就绪，完成后将自动合成成片。"
    # Assign a fresh object so SQLAlchemy persists mutations made during render.
    run.production_json = dict(data)
    flag_modified(run, "production_json")


def advance_production(run_id):
    with SessionLocal() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update(skip_locked=True))
        if not run or run.status not in {"PLANNING", "RUNNING"} or (run.production_json or {}).get("mode") != "AUTOPRODUCE":
            return
        try:
            with db.begin_nested():
                if run.status == "PLANNING":
                    initialize_run(db, run, scope_for_run(db, run))
                else:
                    coordinate(db, run)
        except APIError as error:
            run.status = "FAILED"
            run.warnings = [*(run.warnings or []), f"{error.code}: {error.message}"]
            run.production_json = {**run.production_json, "stage": "FAILED"}
        except Exception as error:
            attempts = (run.production_json or {}).get("automatic_errors", 0) + 1
            run.production_json = {**run.production_json, "automatic_errors": attempts}
            run.warnings = [*(run.warnings or []), f"Automatic retry {attempts}: {type(error).__name__}: {str(error)[:240]}"]
            if attempts >= 3:
                run.status = "FAILED"
                run.production_json = {**run.production_json, "stage": "FAILED"}
        db.commit()
