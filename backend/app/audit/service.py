from sqlalchemy.orm import Session

from app.models import AuditLog


def record(db: Session, *, actor_type: str, actor_id: str | None, action: str, resource_type: str, resource_id: str | None, trace_id: str, workspace_id: str | None = None, project_id: str | None = None, permission_result: str = "ALLOW", result: str = "SUCCESS", agent_run_id: str | None = None, safe_summary: str = "") -> None:
    db.add(AuditLog(actor_type=actor_type, actor_id=actor_id, action=action, resource_type=resource_type, resource_id=resource_id, trace_id=trace_id, workspace_id=workspace_id, project_id=project_id, permission_result=permission_result, result=result, agent_run_id=agent_run_id, safe_summary=safe_summary[:500]))
