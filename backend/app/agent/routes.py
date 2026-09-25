from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.workflow import execute
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
from app.models import (
    AgentRun,
    GenerationJob,
    PendingAction,
    ProjectMember,
    ToolCall,
    WorkspaceMember,
    now,
)

router = APIRouter(tags=["director"])


class RunCreate(BaseModel):
    request: str = Field(min_length=1, max_length=5000)
    intent: str = "ANALYZE"
    shot_id: str | None = None


def run_data(db: Session, run: AgentRun) -> dict:
    calls = db.scalars(select(ToolCall).where(ToolCall.workspace_id == run.workspace_id, ToolCall.project_id == run.project_id, ToolCall.agent_run_id == run.id).order_by(ToolCall.created_at)).all()
    jobs = db.scalars(select(GenerationJob).where(GenerationJob.workspace_id == run.workspace_id, GenerationJob.project_id == run.project_id, GenerationJob.agent_run_id == run.id)).all()
    pending = db.scalars(select(PendingAction).where(PendingAction.workspace_id == run.workspace_id, PendingAction.project_id == run.project_id, PendingAction.agent_run_id == run.id)).all()
    return {"id": run.id, "project_id": run.project_id, "request": run.request, "status": run.status, "current_node": run.current_node, "decision_summary": run.decision_summary, "retrieved_sources": run.retrieved_sources, "warnings": run.warnings, "trace_id": run.trace_id, "cost": float(run.estimated_cost), "tool_calls": [{"id": c.id, "name": c.tool_name, "status": c.status, "summary": c.safe_summary} for c in calls], "generation_jobs": [{"id": j.id, "kind": j.kind, "status": j.status, "provider": j.provider} for j in jobs], "pending_actions": [{"id": p.id, "action": p.action, "target_id": p.target_id, "status": p.status, "requester_id": p.requester_id, "approver_id": p.approver_id} for p in pending]}


def advance(db: Session, run: AgentRun, initial: dict | None = None) -> AgentRun:
    try:
        state, status = execute(run.id, initial)
    except APIError as error:
        run.status = "BLOCKED_AUTH" if error.code in {"AUTH_REQUIRED", "PERMISSION_DENIED"} else "FAILED"
        run.warnings = [error.code]
        db.commit()
        raise
    run.status = status
    run.current_node = "Check Approval" if status == "WAITING_APPROVAL" else state.get("current_stage", "END")
    run.decision_summary = state.get("decision_summary", "")
    run.retrieved_sources = [{"document_id": s["document_id"], "source_id": s["source_id"], "score": s["score"]} for s in state.get("story_context", [])]
    run.warnings = state.get("warnings", [])
    run.estimated_cost = sum(j.get("estimated_cost", 0) for j in state.get("generation_jobs", []))
    db.commit()
    return run


@router.post("/projects/{project_id}/director/runs")
def create_run(payload: RunCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    if payload.intent != "ANALYZE":
        require(scope, "generation.create")
    run = AgentRun(workspace_id=scope.workspace_id, project_id=scope.project_id, user_id=scope.user_id, session_id=scope.session_id, request=payload.request, status="RUNNING", current_node="START", trace_id=trace_id(request))
    db.add(run)
    db.commit()
    initial = {"identity": {"run_id": run.id, "user_id": scope.user_id}, "scope": {"workspace_id": scope.workspace_id, "project_id": scope.project_id}, "request": payload.request, "intent": payload.intent, "shot_id": payload.shot_id, "generation_jobs": [], "retry_state": {}, "errors": [], "current_stage": "START"}
    advance(db, run, initial)
    return ok(request, run_data(db, run))


def authorized_run(db: Session, run_id: str, identity: Identity) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if not run:
        raise APIError("RESOURCE_NOT_FOUND", "Run not found", 404)
    wm = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == run.workspace_id, WorkspaceMember.user_id == identity.user_id))
    pm = db.scalar(select(ProjectMember).where(ProjectMember.workspace_id == run.workspace_id, ProjectMember.project_id == run.project_id, ProjectMember.user_id == identity.user_id))
    if not wm or not pm:
        raise APIError("RESOURCE_NOT_FOUND", "Run not found", 404)
    return run


@router.get("/director/runs/{run_id}")
def get_run(run_id: str, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    return ok(request, run_data(db, authorized_run(db, run_id, identity)))


@router.post("/director/runs/{run_id}/resume")
def resume(run_id: str, request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    run = authorized_run(db, run_id, identity)
    if run.user_id != identity.user_id or run.session_id != identity.session_id:
        raise APIError("PERMISSION_DENIED", "Only the initiating session may resume", 403)
    if run.status not in {"WAITING_APPROVAL", "BLOCKED_AUTH"}:
        raise APIError("RESOURCE_CONFLICT", "Run is not paused", 409)
    advance(db, run)
    return ok(request, run_data(db, run))


@router.get("/projects/{project_id}/director/runs")
def list_runs(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    runs = db.scalars(select(AgentRun).where(AgentRun.workspace_id == scope.workspace_id, AgentRun.project_id == scope.project_id).order_by(AgentRun.created_at.desc()).limit(50)).all()
    return ok(request, [run_data(db, run) for run in runs])


@router.get("/projects/{project_id}/pending-actions")
def list_pending(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    rows = db.scalars(select(PendingAction).where(PendingAction.workspace_id == scope.workspace_id, PendingAction.project_id == scope.project_id).order_by(PendingAction.created_at.desc()).limit(100)).all()
    return ok(request, [{"id": p.id, "agent_run_id": p.agent_run_id, "action": p.action, "target_id": p.target_id, "status": p.status, "requester_id": p.requester_id, "approver_id": p.approver_id, "expires_at": p.expires_at.isoformat()} for p in rows])


@router.post("/projects/{project_id}/pending-actions/{action_id}/approve")
def approve(action_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "review.approve")
    pending = db.scalar(select(PendingAction).where(PendingAction.id == action_id, PendingAction.workspace_id == scope.workspace_id, PendingAction.project_id == scope.project_id).with_for_update())
    if not pending:
        raise APIError("RESOURCE_NOT_FOUND", "Action not found", 404)
    if pending.status != "PENDING":
        raise APIError("RESOURCE_CONFLICT", "Action is not pending", 409)
    if pending.expires_at <= now():
        pending.status = "EXPIRED"
        db.commit()
        raise APIError("RESOURCE_CONFLICT", "Action expired", 409)
    if pending.requester_id == scope.user_id and scope.role != "OWNER":
        raise APIError("PERMISSION_DENIED", "Separate approver required", 403)
    pending.status = "APPROVED"
    pending.approver_id = scope.user_id
    record(db, actor_type="USER", actor_id=scope.user_id, action="pending_action.approve", resource_type="pending_action", resource_id=pending.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), agent_run_id=pending.agent_run_id, safe_summary="owner override" if pending.requester_id == scope.user_id else "maker-checker")
    db.commit()
    return ok(request, {"id": pending.id, "status": pending.status})


@router.post("/projects/{project_id}/pending-actions/{action_id}/reject")
def reject(action_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "review.approve")
    pending = db.scalar(select(PendingAction).where(PendingAction.id == action_id, PendingAction.workspace_id == scope.workspace_id, PendingAction.project_id == scope.project_id).with_for_update())
    if not pending:
        raise APIError("RESOURCE_NOT_FOUND", "Action not found", 404)
    if pending.status != "PENDING":
        raise APIError("RESOURCE_CONFLICT", "Action is not pending", 409)
    pending.status = "REJECTED"
    pending.approver_id = scope.user_id
    record(db, actor_type="USER", actor_id=scope.user_id, action="pending_action.reject", resource_type="pending_action", resource_id=pending.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request), agent_run_id=pending.agent_run_id)
    db.commit()
    return ok(request, {"id": pending.id, "status": pending.status})
