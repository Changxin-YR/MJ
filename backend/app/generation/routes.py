import json
from collections.abc import Generator
from uuid import uuid4

import redis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require
from app.config import settings
from app.db import SessionLocal, get_db
from app.generation.service import job_data, request_generation
from app.models import GenerationJob, ProjectMember, ServerSession, User, WorkspaceMember, now

router = APIRouter(prefix="/projects/{project_id}", tags=["generation"])


class GenerationRequest(BaseModel):
    kind: str
    idempotency_key: str = Field(min_length=8, max_length=160)


@router.post("/shots/{shot_id}/generations")
def create_generation(shot_id: str, payload: GenerationRequest, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    job = request_generation(db, scope, shot_id, payload.kind, payload.idempotency_key, trace_id(request))
    record(db, actor_type="USER", actor_id=scope.user_id, action="generation.request", resource_type="generation_job", resource_id=job.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), safe_summary=payload.kind)
    db.commit()
    return ok(request, job_data(job))


@router.get("/generation-jobs")
def list_jobs(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id).order_by(GenerationJob.created_at.desc()).limit(100)).all()
    return ok(request, [job_data(j) for j in jobs])


@router.get("/generation-jobs/{job_id}")
def get_job(job_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id))
    if not job:
        from app.api.errors import APIError
        raise APIError("RESOURCE_NOT_FOUND", "Job not found", 404)
    return ok(request, job_data(job))


def _stream_scope_valid(scope: ProjectScope) -> bool:
    with SessionLocal() as db:
        session = db.get(ServerSession, scope.session_id)
        user = db.get(User, scope.user_id)
        workspace_member = db.scalar(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == scope.workspace_id,
                WorkspaceMember.user_id == scope.user_id,
            )
        )
        project_member = db.scalar(
            select(ProjectMember).where(
                ProjectMember.workspace_id == scope.workspace_id,
                ProjectMember.project_id == scope.project_id,
                ProjectMember.user_id == scope.user_id,
            )
        )
        return bool(
            session
            and session.user_id == scope.user_id
            and not session.revoked_at
            and session.expires_at > now()
            and user
            and not user.disabled
            and workspace_member
            and project_member
        )


@router.get("/events")
def events(scope: ProjectScope = Depends(project_scope)):
    require(scope, "project.read")

    def stream() -> Generator[str, None, None]:
        subscriber = redis.from_url(settings.redis_url).pubsub()
        subscriber.subscribe(f"project:{scope.project_id}:events")
        try:
            yield ": connected\n\n"
            while True:
                if not _stream_scope_valid(scope):
                    break
                message = subscriber.get_message(ignore_subscribe_messages=True, timeout=15)
                if message:
                    payload = json.loads(message["data"])
                    yield f"id: {uuid4()}\nevent: {payload['event']}\ndata: {json.dumps(payload['data'])}\n\n"
                else:
                    yield ": heartbeat\n\n"
        finally:
            subscriber.close()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
