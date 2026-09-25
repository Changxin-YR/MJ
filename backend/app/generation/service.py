import random
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.auth.dependencies import ProjectScope, require
from app.config import settings
from app.models import GenerationJob, OutboxEvent, Project, Shot
from app.providers.registry import registry
from app.storyboard.state import transition_job, transition_shot


CHINESE_VISUAL_RULE = (
    "硬性语言约束：画面中如果出现任何可读文字、招牌、标识、字幕或拟声词，"
    "只能使用简体中文（允许阿拉伯数字和常规标点）；禁止日文假名、韩文、英文单词和乱码。"
    "如果剧情没有明确要求出现文字，则不要生成任何可读文字。"
)
CHINESE_VISUAL_NEGATIVE = "日文文字, 日语假名, 韩文, 韩语谚文, 英文单词, 拉丁字母招牌, 乱码, 伪文字, 错别字字幕"


def chinese_visual_prompt(prompt: str) -> str:
    return f"{prompt.strip()}\n{CHINESE_VISUAL_RULE}" if prompt.strip() else CHINESE_VISUAL_RULE


def chinese_visual_negative_prompt(negative_prompt: str) -> str:
    parts = [negative_prompt.strip(), CHINESE_VISUAL_NEGATIVE]
    return ", ".join(part for part in parts if part)


def reserve(db: Session, project: Project, amount: Decimal) -> None:
    if Decimal(project.budget_used) + Decimal(project.budget_reserved) + amount > Decimal(project.budget_limit):
        raise APIError("BUDGET_EXCEEDED", "Project budget exceeded", 409)
    project.budget_reserved = Decimal(project.budget_reserved) + amount


def settle(project: Project, reserved: Decimal, actual: Decimal) -> None:
    project.budget_reserved = Decimal(project.budget_reserved) - reserved
    project.budget_used = Decimal(project.budget_used) + actual


def request_generation(db: Session, scope: ProjectScope, shot_id: str, kind: str, idempotency_key: str, trace_id: str, agent_run_id: str | None = None, tool_call_id: str | None = None) -> GenerationJob:
    require(scope, "generation.create")
    if kind not in {"IMAGE", "VIDEO", "VOICE"}:
        raise APIError("INVALID_PARAMETER", "Unknown generation kind", 422)
    existing = db.scalar(select(GenerationJob).where(GenerationJob.workspace_id == scope.workspace_id, GenerationJob.project_id == scope.project_id, GenerationJob.idempotency_key == idempotency_key))
    if existing:
        if existing.resource_id != shot_id or existing.kind != kind:
            raise APIError("RESOURCE_CONFLICT", "Idempotency key already used", 409)
        return existing
    project = db.scalar(select(Project).where(Project.id == scope.project_id, Project.workspace_id == scope.workspace_id).with_for_update())
    shot = db.scalar(select(Shot).where(Shot.id == shot_id, Shot.workspace_id == scope.workspace_id, Shot.project_id == scope.project_id).with_for_update())
    if not shot:
        raise APIError("RESOURCE_NOT_FOUND", "Shot not found", 404)
    if shot.status not in {"STORYBOARD_READY", "REVIEW_REQUIRED"}:
        raise APIError("SHOT_LOCKED" if shot.status == "LOCKED" else "RESOURCE_CONFLICT", "Shot cannot generate in current state", 409)
    if kind == "VIDEO" and not shot.current_image_asset_id:
        raise APIError("RESOURCE_CONFLICT", "Generate image first", 409)
    if kind == "VOICE" and not shot.dialogue.strip():
        raise APIError("INVALID_PARAMETER", "Dialogue required for voice", 422)
    entry = registry.route(kind)
    estimate = Decimal(str(entry.cost[kind])) * (Decimal(str(shot.duration)) if kind == "VIDEO" else Decimal(1))
    reserve(db, project, estimate)
    transition_shot(shot, "GENERATING")
    if entry.provider == "dashscope":
        model = {"IMAGE": settings.dashscope_image_model, "VIDEO": settings.dashscope_video_model, "VOICE": settings.dashscope_tts_model}[kind]
    elif entry.provider == "comfyui":
        model = settings.comfyui_checkpoint
    else:
        model = f"fake-{kind.lower()}-v1"
    base_prompt = ". ".join(filter(None, [shot.description, shot.action, shot.prompt]))
    job = GenerationJob(workspace_id=scope.workspace_id, project_id=scope.project_id, provider=entry.provider, model=model, resource_type="shot", resource_id=shot.id, kind=kind, input_json={"prompt": chinese_visual_prompt(base_prompt), "negative_prompt": chinese_visual_negative_prompt(shot.negative_prompt), "duration": shot.duration, "dialogue": shot.dialogue, "image_asset_id": shot.current_image_asset_id}, idempotency_key=idempotency_key, estimated_cost=estimate, actual_cost=0, status="CREATED", trace_id=trace_id, agent_run_id=agent_run_id, tool_call_id=tool_call_id)
    db.add(job)
    db.flush()
    transition_job(job, "QUEUED")
    db.add(OutboxEvent(workspace_id=scope.workspace_id, project_id=scope.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:{job.id}:0"))
    return job


def retry_delay(retry_count: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** retry_count) + random.uniform(0, 1))


def job_data(job: GenerationJob) -> dict:
    return {"id": job.id, "workspace_id": job.workspace_id, "project_id": job.project_id, "resource_type": job.resource_type, "resource_id": job.resource_id, "kind": job.kind, "provider": job.provider, "model": job.model, "status": job.status, "estimated_cost": float(job.estimated_cost), "actual_cost": float(job.actual_cost), "inspection": job.inspection_json, "retry_count": job.retry_count, "error_code": job.error_code, "error_message": job.error_message, "created_at": job.created_at.isoformat(), "started_at": job.started_at.isoformat() if job.started_at else None, "finished_at": job.finished_at.isoformat() if job.finished_at else None, "trace_id": job.trace_id}
