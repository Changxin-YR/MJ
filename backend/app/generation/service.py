import random
import re
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError
from app.auth.dependencies import ProjectScope, require
from app.config import settings
from app.models import Character, CharacterVersion, GenerationJob, OutboxEvent, Project, Scene, Shot
from app.providers.registry import registry
from app.storyboard.state import transition_job, transition_shot

IMAGE_CHINESE_TEXT_RULE = (
    "画面中文字规则：如果出现招牌、海报、标签、屏幕、字幕或其他可读文字，"
    "只能使用简体中文；禁止日文假名、韩文谚文、繁体中文和其他外语文字；"
    "如果文字不是剧情必需，则不要生成任何文字。"
)
FOREIGN_ASIAN_SCRIPT = re.compile(r"[\u3040-\u30ff\u31f0-\u31ff\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]")

IMAGE_NON_CHINESE_TEXT_NEGATIVE = (
    "日文，日语文字，平假名，片假名，韩文，韩语文字，谚文，繁体中文，"
    "英文文字，乱码，伪文字，错误字符"
)


def apply_chinese_image_policy(prompt: str, negative_prompt: str) -> tuple[str, str]:
    base_prompt = prompt.strip()
    constrained_prompt = f"{base_prompt}. {IMAGE_CHINESE_TEXT_RULE}" if base_prompt else IMAGE_CHINESE_TEXT_RULE
    max_negative_length = 500
    reserve = len(IMAGE_NON_CHINESE_TEXT_NEGATIVE) + 2
    user_negative = negative_prompt.strip()[: max(0, max_negative_length - reserve)]
    constrained_negative = (
        f"{user_negative}, {IMAGE_NON_CHINESE_TEXT_NEGATIVE}"
        if user_negative
        else IMAGE_NON_CHINESE_TEXT_NEGATIVE
    )
    return constrained_prompt, constrained_negative


def build_generation_prompt(db: Session, scope: ProjectScope, shot: Shot) -> tuple[str, str]:
    project = db.scalar(
        select(Project).where(
            Project.id == scope.project_id,
            Project.workspace_id == scope.workspace_id,
        )
    )
    if not project:
        raise APIError("RESOURCE_NOT_FOUND", "Project not found", 404)
    project_style = str((project.settings_json or {}).get("style", "")).strip()
    scene = db.scalar(
        select(Scene).where(
            Scene.id == shot.scene_id,
            Scene.workspace_id == scope.workspace_id,
            Scene.project_id == scope.project_id,
        )
    )
    if not scene:
        raise APIError("RESOURCE_NOT_FOUND", "Scene not found", 404)
    scene_context = ". ".join(filter(None, [scene.heading, scene.description]))
    camera_context = "；".join(
        filter(
            None,
            [
                f"景别：{shot.shot_type}" if shot.shot_type else "",
                f"机位：{shot.camera_angle}" if shot.camera_angle else "",
                f"镜头运动：{shot.camera_movement}" if shot.camera_movement else "",
                f"情绪：{shot.emotion}" if shot.emotion else "",
            ],
        )
    )
    anchors: list[str] = []
    negatives: list[str] = []
    for character_id in shot.character_ids:
        character = db.scalar(
            select(Character).where(
                Character.id == character_id,
                Character.workspace_id == scope.workspace_id,
                Character.project_id == scope.project_id,
            )
        )
        if not character:
            raise APIError("RESOURCE_NOT_FOUND", "Character not found", 404)
        if not character.active_version_id:
            continue
        version = db.scalar(
            select(CharacterVersion).where(
                CharacterVersion.id == character.active_version_id,
                CharacterVersion.character_id == character.id,
                CharacterVersion.workspace_id == scope.workspace_id,
                CharacterVersion.project_id == scope.project_id,
                CharacterVersion.status == "ACTIVE",
            )
        )
        if not version:
            raise APIError("RESOURCE_CONFLICT", "Active character version is unavailable", 409)
        dna = version.dna or {}
        anchors.append(
            "; ".join(
                filter(
                    None,
                    [
                        f"{character.name}: {dna.get('prompt_anchor', '')}".strip(),
                        f"face {dna.get('face', '')}".strip() if dna.get("face") else "",
                        f"hair {dna.get('hair', '')}".strip() if dna.get("hair") else "",
                        f"costume {dna.get('costume', '')}".strip() if dna.get("costume") else "",
                        f"style {dna.get('style', '')}".strip() if dna.get("style") else "",
                    ],
                )
            )
        )
        if dna.get("negative_prompt"):
            negatives.append(str(dna["negative_prompt"]).strip())
    prompt = ". ".join(
        filter(
            None,
            [
                f"场景：{scene_context}" if scene_context else "",
                shot.description,
                shot.action,
                f"镜头参数：{camera_context}" if camera_context else "",
                shot.prompt,
                f"项目统一视觉风格：{project_style}" if project_style else "",
                *anchors,
            ],
        )
    )
    negative_prompt = ", ".join(filter(None, [shot.negative_prompt, *negatives]))
    return prompt, negative_prompt


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
    if kind == "VOICE" and FOREIGN_ASIAN_SCRIPT.search(shot.dialogue):
        raise APIError("INVALID_PARAMETER", "Voice dialogue must not contain Japanese or Korean script", 422)
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
    prompt, negative_prompt = build_generation_prompt(db, scope, shot)
    if kind in {"IMAGE", "VIDEO"}:
        prompt, negative_prompt = apply_chinese_image_policy(prompt, negative_prompt)
    job = GenerationJob(workspace_id=scope.workspace_id, project_id=scope.project_id, provider=entry.provider, model=model, resource_type="shot", resource_id=shot.id, kind=kind, input_json={"prompt": prompt, "negative_prompt": negative_prompt, "duration": shot.duration, "dialogue": shot.dialogue, "image_asset_id": shot.current_image_asset_id}, idempotency_key=idempotency_key, estimated_cost=estimate, actual_cost=0, status="CREATED", trace_id=trace_id, agent_run_id=agent_run_id, tool_call_id=tool_call_id)
    db.add(job)
    db.flush()
    transition_job(job, "QUEUED")
    db.add(OutboxEvent(workspace_id=scope.workspace_id, project_id=scope.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:{job.id}:0"))
    return job


def retry_delay(retry_count: int) -> timedelta:
    return timedelta(seconds=min(300, 2 ** retry_count) + random.uniform(0, 1))


def job_data(job: GenerationJob) -> dict:
    return {"id": job.id, "workspace_id": job.workspace_id, "project_id": job.project_id, "resource_type": job.resource_type, "resource_id": job.resource_id, "kind": job.kind, "provider": job.provider, "model": job.model, "status": job.status, "estimated_cost": float(job.estimated_cost), "actual_cost": float(job.actual_cost), "inspection": job.inspection_json, "retry_count": job.retry_count, "error_code": job.error_code, "error_message": job.error_message, "created_at": job.created_at.isoformat(), "started_at": job.started_at.isoformat() if job.started_at else None, "finished_at": job.finished_at.isoformat() if job.finished_at else None, "trace_id": job.trace_id}
