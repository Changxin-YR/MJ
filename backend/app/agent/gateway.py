import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from app.api.errors import APIError
from app.audit.service import record
from app.auth.dependencies import ProjectScope, require
from app.db import SessionLocal
from app.generation.service import job_data, request_generation
from app.models import (
    AgentRun,
    Character,
    CharacterRelationship,
    CharacterVersion,
    Episode,
    GenerationJob,
    PendingAction,
    Project,
    ProjectBible,
    ProjectMember,
    ServerSession,
    Shot,
    ToolCall,
    User,
    WorkspaceMember,
    now,
)
from app.rag.service import retrieve


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict
    required_permission: str
    risk_level: str
    idempotent: bool
    requires_confirmation: bool
    scope: str


CONTRACTS = {
    "load_project_context": ToolContract("load_project_context", "Read structured project state", {}, "project.read", "LOW", True, False, "PROJECT"),
    "retrieve_semantic_context": ToolContract("retrieve_semantic_context", "Search active scoped knowledge", {"query": "string"}, "project.read", "LOW", True, False, "PROJECT"),
    "load_generation_job": ToolContract("load_generation_job", "Read scoped generation status", {"job_id": "uuid"}, "project.read", "LOW", True, False, "PROJECT"),
    "generate_shot": ToolContract("generate_shot", "Create a scoped generation job", {"shot_id": "uuid", "kind": "IMAGE|VIDEO|VOICE", "idempotency_key": "string"}, "generation.create", "HIGH", True, True, "PROJECT"),
}
AGENT_ALLOWLIST = frozenset(CONTRACTS)


class ToolGateway:
    def audit(self, run_id: str, action: str, summary: str) -> None:
        run, scope = self.authorize(run_id)
        with SessionLocal() as db:
            record(db, actor_type="AGENT", actor_id=run_id, action=action, resource_type="agent_run", resource_id=run_id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=run.trace_id, agent_run_id=run_id, safe_summary=summary)
            db.commit()

    def authorize(self, run_id: str) -> tuple[AgentRun, ProjectScope]:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            if not run:
                raise APIError("RESOURCE_NOT_FOUND", "Director run not found", 404)
            session = db.get(ServerSession, run.session_id)
            user = db.get(User, run.user_id)
            if not session or session.user_id != run.user_id or session.revoked_at or session.expires_at <= now() or not user or user.disabled:
                raise APIError("AUTH_REQUIRED", "Session revoked", 401)
            project = db.scalar(select(Project).where(Project.id == run.project_id, Project.workspace_id == run.workspace_id))
            wm = db.scalar(select(WorkspaceMember).where(WorkspaceMember.workspace_id == run.workspace_id, WorkspaceMember.user_id == run.user_id))
            pm = db.scalar(select(ProjectMember).where(ProjectMember.workspace_id == run.workspace_id, ProjectMember.project_id == run.project_id, ProjectMember.user_id == run.user_id))
            if not project or not wm or not pm:
                raise APIError("PERMISSION_DENIED", "Project access revoked", 403)
            return run, ProjectScope(run.user_id, run.session_id, run.workspace_id, run.project_id, pm.role)

    def prepare_action(self, run_id: str, action: str, target_id: str, arguments: dict) -> PendingAction:
        _, scope = self.authorize(run_id)
        contract = CONTRACTS.get(action)
        if not contract or not contract.requires_confirmation:
            raise APIError("INVALID_PARAMETER", "Action does not require confirmation", 422)
        require(scope, contract.required_permission)
        if action == "generate_shot":
            with SessionLocal() as db:
                shot = db.scalar(select(Shot).where(Shot.id == target_id, Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id))
                if not shot or shot.status not in {"STORYBOARD_READY", "REVIEW_REQUIRED"}:
                    raise APIError("RESOURCE_CONFLICT", "Shot cannot generate", 409)
        with SessionLocal() as db:
            pending = db.scalar(select(PendingAction).where(PendingAction.agent_run_id == run_id, PendingAction.action == action, PendingAction.target_id == target_id))
            if pending:
                return pending
            pending = PendingAction(workspace_id=scope.workspace_id, project_id=scope.project_id, agent_run_id=run_id, requester_id=scope.user_id, action=action, target_id=target_id, arguments_json=arguments, status="PENDING", expires_at=now() + timedelta(hours=24))
            db.add(pending)
            db.flush()
            record(db, actor_type="AGENT", actor_id=run_id, action="pending_action.create", resource_type="pending_action", resource_id=pending.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=db.get(AgentRun, run_id).trace_id, agent_run_id=run_id)
            db.commit()
            return pending

    def invoke(self, run_id: str, tool_name: str, arguments: dict, pending_action_id: str | None = None):
        run, scope = self.authorize(run_id)
        if tool_name not in AGENT_ALLOWLIST:
            raise APIError("PERMISSION_DENIED", "Tool is not available to Director", 403)
        contract = CONTRACTS[tool_name]
        require(scope, contract.required_permission)
        if any(key in arguments for key in ("project_id", "workspace_id", "user_id", "role")):
            raise APIError("PERMISSION_DENIED", "Scope override denied", 403)
        if set(arguments) != set(contract.input_schema):
            raise APIError("INVALID_PARAMETER", "Tool arguments do not match contract", 422)
        digest = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
        with SessionLocal() as db:
            pending = None
            if contract.requires_confirmation:
                pending = db.scalar(select(PendingAction).where(PendingAction.id == pending_action_id, PendingAction.workspace_id == scope.workspace_id, PendingAction.project_id == scope.project_id, PendingAction.agent_run_id == run_id).with_for_update())
                if not pending or pending.action != tool_name or pending.target_id != arguments.get("shot_id"):
                    raise APIError("PENDING_APPROVAL_REQUIRED", "Approved action required", 409)
                if pending.status == "EXECUTED":
                    existing = db.scalar(select(GenerationJob).where(GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id, GenerationJob.idempotency_key == arguments["idempotency_key"], GenerationJob.resource_id == arguments["shot_id"]))
                    if existing:
                        return job_data(existing)
                if pending.status != "APPROVED" or pending.expires_at <= now():
                    raise APIError("PENDING_APPROVAL_REQUIRED", "Approved action required", 409)
            tool_call = ToolCall(workspace_id=scope.workspace_id, project_id=scope.project_id, agent_run_id=run_id, tool_name=tool_name, arguments_hash=digest, resource_ids=[arguments["shot_id"]] if "shot_id" in arguments else [], status="RUNNING", trace_id=run.trace_id)
            db.add(tool_call)
            db.flush()
            if tool_name == "load_project_context":
                project = db.scalar(select(Project).where(Project.id == scope.project_id, Project.workspace_id == scope.workspace_id))
                characters = db.scalars(select(Character).where(Character.workspace_id == scope.workspace_id, Character.project_id == scope.project_id)).all()
                active_ids = [character.active_version_id for character in characters if character.active_version_id]
                active_versions = {
                    version.id: version
                    for version in db.scalars(
                        select(CharacterVersion).where(
                            CharacterVersion.workspace_id == scope.workspace_id,
                            CharacterVersion.project_id == scope.project_id,
                            CharacterVersion.id.in_(active_ids),
                            CharacterVersion.status == "ACTIVE",
                        )
                    ).all()
                } if active_ids else {}
                character_names = {character.id: character.name for character in characters}
                bibles = db.scalars(
                    select(ProjectBible)
                    .where(
                        ProjectBible.workspace_id == scope.workspace_id,
                        ProjectBible.project_id == scope.project_id,
                    )
                    .order_by(ProjectBible.created_at)
                ).all()
                relationships = db.scalars(
                    select(CharacterRelationship)
                    .where(
                        CharacterRelationship.workspace_id == scope.workspace_id,
                        CharacterRelationship.project_id == scope.project_id,
                    )
                    .order_by(CharacterRelationship.created_at)
                ).all()
                result = {
                    "project": {"id": project.id, "name": project.name, "description": project.description, "settings": project.settings_json},
                    "bibles": [
                        {
                            "id": item.id,
                            "title": item.title,
                            "content": item.content,
                            "version": item.version,
                        }
                        for item in bibles
                    ],
                    "relationships": [
                        {
                            "id": item.id,
                            "source_character_id": item.source_character_id,
                            "source_character_name": character_names.get(item.source_character_id, ""),
                            "target_character_id": item.target_character_id,
                            "target_character_name": character_names.get(item.target_character_id, ""),
                            "description": item.description,
                            "version": item.version,
                        }
                        for item in relationships
                    ],
                    "characters": [
                        {
                            "id": character.id,
                            "name": character.name,
                            "active_version_id": character.active_version_id,
                            "background": active_versions[character.active_version_id].background if character.active_version_id in active_versions else "",
                            "dna": active_versions[character.active_version_id].dna if character.active_version_id in active_versions else {},
                        }
                        for character in characters
                    ],
                    "episodes": [{"id": e.id, "title": e.title} for e in db.scalars(select(Episode).where(Episode.workspace_id == scope.workspace_id, Episode.project_id == scope.project_id)).all()],
                    "shot_count": db.scalar(select(func.count()).select_from(Shot).where(Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id)),
                }
            elif tool_name == "retrieve_semantic_context":
                result = retrieve(workspace_id=scope.workspace_id, project_id=scope.project_id, query=arguments["query"])
            elif tool_name == "load_generation_job":
                job = db.scalar(select(GenerationJob).where(GenerationJob.id == arguments["job_id"], GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id))
                if not job:
                    raise APIError("RESOURCE_NOT_FOUND", "Job not found", 404)
                result = job_data(job)
            else:
                result = job_data(request_generation(db, scope, arguments["shot_id"], arguments["kind"], arguments["idempotency_key"], run.trace_id, agent_run_id=run_id, tool_call_id=tool_call.id))
                pending.status = "EXECUTED"
                pending.executed_at = now()
            tool_call.status = "SUCCEEDED"
            tool_call.safe_summary = f"{tool_name} completed"
            record(db, actor_type="AGENT", actor_id=run_id, action="tool.invoke", resource_type="tool_call", resource_id=tool_call.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=run.trace_id, agent_run_id=run_id, safe_summary=tool_name)
            db.commit()
            return result


gateway = ToolGateway()
