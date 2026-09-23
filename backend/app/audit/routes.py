from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import (
    AgentRun,
    Asset,
    AuditLog,
    GenerationJob,
    Shot,
    StorySource,
    Timeline,
    TimelineItem,
    ToolCall,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["audit"])


@router.get("/audit")
def list_audit(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "audit.read")
    rows = db.scalars(select(AuditLog).where(AuditLog.workspace_id == scope.workspace_id, AuditLog.project_id == scope.project_id).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return ok(request, [{"id": a.id, "actor_type": a.actor_type, "actor_id": a.actor_id, "action": a.action, "resource_type": a.resource_type, "resource_id": a.resource_id, "permission_result": a.permission_result, "result": a.result, "agent_run_id": a.agent_run_id, "trace_id": a.trace_id, "safe_summary": a.safe_summary, "time": a.created_at.isoformat()} for a in rows])


@router.get("/assets/{asset_id}/lineage")
def lineage(asset_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    asset = scoped_get(db, Asset, asset_id, scope)
    timeline = db.scalar(select(Timeline).where(Timeline.workspace_id == scope.workspace_id, Timeline.project_id == scope.project_id, Timeline.final_asset_id == asset.id))
    if not timeline:
        raise APIError("RESOURCE_NOT_FOUND", "Final timeline not found", 404)
    items = db.scalars(select(TimelineItem).where(TimelineItem.workspace_id == scope.workspace_id, TimelineItem.project_id == scope.project_id, TimelineItem.timeline_id == timeline.id)).all()
    shots = []
    seen = set()
    for item in items:
        if not item.shot_id or item.shot_id in seen:
            continue
        seen.add(item.shot_id)
        shot = scoped_get(db, Shot, item.shot_id, scope)
        asset_nodes = []
        for shot_asset_id in (shot.current_image_asset_id, shot.current_video_asset_id, shot.current_audio_asset_id):
            if not shot_asset_id:
                continue
            shot_asset = scoped_get(db, Asset, shot_asset_id, scope)
            job = db.scalar(select(GenerationJob).where(GenerationJob.id == shot_asset.source_job_id, GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id)) if shot_asset.source_job_id else None
            tool = db.scalar(select(ToolCall).where(ToolCall.id == job.tool_call_id, ToolCall.workspace_id == scope.workspace_id, ToolCall.project_id == scope.project_id)) if job and job.tool_call_id else None
            run = db.scalar(select(AgentRun).where(AgentRun.id == job.agent_run_id, AgentRun.workspace_id == scope.workspace_id, AgentRun.project_id == scope.project_id)) if job and job.agent_run_id else None
            asset_nodes.append({"asset_id": shot_asset.id, "mime": shot_asset.mime, "generation_job_id": job.id if job else None, "provider": job.provider if job else None, "tool_call_id": tool.id if tool else None, "agent_run_id": run.id if run else None, "retrieved_sources": run.retrieved_sources if run else []})
        shots.append({"shot_id": shot.id, "shot_no": shot.shot_no, "assets": asset_nodes})
    stories = db.scalars(select(StorySource).where(StorySource.workspace_id == scope.workspace_id, StorySource.project_id == scope.project_id)).all()
    return ok(request, {"final_asset_id": asset.id, "timeline_id": timeline.id, "episode_id": timeline.episode_id, "shots": shots, "story_sources": [{"id": s.id, "title": s.title} for s in stories]})
