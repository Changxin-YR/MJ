import logging
import subprocess
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from threading import Event, Thread
from uuid import uuid4

import httpx
from minio.error import S3Error
from sqlalchemy import select, update

from app.agent.inspector import inspect_media
from app.asset.storage import client as minio_client
from app.asset.storage import store_result, validate
from app.audit.service import record
from app.db import SessionLocal
from app.generation.events import publish
from app.generation.service import retry_delay, settle
from app.models import (
    AgentRun,
    Asset,
    GenerationJob,
    GenerationOutput,
    OutboxEvent,
    Project,
    Shot,
    StorySource,
    UsageRecord,
    now,
)
from app.providers.registry import registry
from app.storyboard.state import transition_job, transition_shot
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
HEARTBEAT_SECONDS = 30


def notify(project_id, name, data):
    try:
        publish(project_id, name, data)
    except Exception:
        logger.exception("generation notification failed", extra={"project_id": project_id, "event": name})


def renew_lease(job_id, owner):
    with SessionLocal() as db:
        renewed = db.execute(update(GenerationJob).where(GenerationJob.id == job_id, GenerationJob.status == "RUNNING", GenerationJob.lease_owner == owner).values(last_heartbeat_at=now(), lease_expires_at=now() + timedelta(minutes=5)))
        db.commit()
        return bool(renewed.rowcount)


@contextmanager
def lease_heartbeat(job_id, owner):
    stopped = Event()

    def heartbeat():
        while not stopped.wait(HEARTBEAT_SECONDS):
            try:
                if not renew_lease(job_id, owner):
                    return
            except Exception:
                logger.exception("generation lease renewal failed", extra={"job_id": job_id})

    thread = Thread(target=heartbeat, name=f"job-lease-{job_id}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stopped.set()
        thread.join(timeout=2)


def claim_job(job_id: str) -> str | None:
    owner = str(uuid4())
    with SessionLocal() as db:
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if not job or job.status in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            return None
        if job.status == "RUNNING" and job.lease_expires_at and job.lease_expires_at > now():
            return None
        if job.status not in {"QUEUED", "RETRYING", "RUNNING"}:
            return None
        if job.status == "RETRYING":
            transition_job(job, "QUEUED")
        if job.status == "QUEUED":
            transition_job(job, "RUNNING")
        job.lease_owner = owner
        job.lease_expires_at = now() + timedelta(minutes=5)
        job.last_heartbeat_at = now()
        job.started_at = job.started_at or now()
        db.commit()
        project_id = job.project_id
    notify(project_id, "generation.started", {"job_id": job_id})
    return owner


def get_image_bytes(job: GenerationJob) -> bytes:
    with SessionLocal() as db:
        asset = db.scalar(select(Asset).where(Asset.id == job.input_json["image_asset_id"], Asset.workspace_id == job.workspace_id, Asset.project_id == job.project_id))
        if not asset:
            raise ValueError("source image missing")
        response = minio_client().get_object(asset.bucket, asset.object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()


def generate(job: GenerationJob):
    args = job.input_json
    adapter = registry.adapter(job.provider, job.kind)
    if job.kind == "IMAGE":
        return adapter.generate(args["prompt"], args.get("negative_prompt", ""))
    if job.kind == "VIDEO":
        remote_id = job.remote_job_id
        if not remote_id:
            remote_id = adapter.submit(get_image_bytes(job), float(args["duration"]), args["prompt"])
            with SessionLocal() as db:
                current = db.scalar(select(GenerationJob).where(GenerationJob.id == job.id).with_for_update())
                if current and not current.remote_job_id:
                    current.remote_job_id = remote_id
                    db.commit()
        status = adapter.get_status(remote_id)
        if status in {"PENDING", "RUNNING"}:
            return None
        if status != "SUCCEEDED":
            raise ValueError(f"Video provider task ended with {status}")
        return adapter.fetch_result(remote_id)
    if job.kind == "VOICE":
        return adapter.synthesize(args["dialogue"], float(args["duration"]))
    raise ValueError("invalid generation kind")


def defer_poll(job_id: str, owner: str) -> None:
    with SessionLocal() as db:
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if not job or job.status != "RUNNING" or job.lease_owner != owner:
            return
        job.lease_owner = None
        job.lease_expires_at = now()
        job.last_heartbeat_at = now()
        db.add(OutboxEvent(workspace_id=job.workspace_id, project_id=job.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:poll:{job.id}:{uuid4()}", next_attempt_at=now() + timedelta(seconds=8)))
        db.commit()


def complete_job(job_id: str, owner: str, result, inspection: dict) -> None:
    with SessionLocal() as db:
        project_id = db.scalar(select(GenerationJob.project_id).where(GenerationJob.id == job_id))
        if not project_id:
            return
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if not job or job.status != "RUNNING" or job.lease_owner != owner:
            return
        shot = db.scalar(select(Shot).where(Shot.id == job.resource_id, Shot.workspace_id == job.workspace_id, Shot.project_id == job.project_id).with_for_update())
        asset = store_result(workspace_id=job.workspace_id, project_id=job.project_id, job_id=job.id, result=result, source_job_id=job.id)
        db.add(asset)
        db.flush()
        db.add(GenerationOutput(workspace_id=job.workspace_id, project_id=job.project_id, job_id=job.id, asset_id=asset.id, kind=job.kind))
        if job.kind == "IMAGE":
            shot.current_image_asset_id = asset.id
        elif job.kind == "VIDEO":
            shot.current_video_asset_id = asset.id
        else:
            shot.current_audio_asset_id = asset.id
        inspection = {**inspection, "asset_id": asset.id, "kind": job.kind}
        # Current video review remains authoritative when a different medium is regenerated.
        if job.kind == "VIDEO" or (not shot.current_video_asset_id and (job.kind == "IMAGE" or not shot.inspection_json)):
            shot.inspection_json = inspection
        job.inspection_json = inspection
        transition_job(job, "SUCCEEDED")
        job.finished_at = now()
        # Model Studio media responses do not include a bill amount. Keep the
        # reservation charged until a billing reconciliation can replace it.
        job.actual_cost = max(Decimal(str(result.cost)), Decimal(job.estimated_cost)) if job.provider != "fake" else Decimal(str(result.cost))
        job.lease_owner = None
        job.lease_expires_at = None
        transition_shot(shot, "GENERATED")
        transition_shot(shot, "INSPECTING")
        transition_shot(shot, "REVIEW_REQUIRED")
        settle(project, Decimal(job.estimated_cost), Decimal(job.actual_cost))
        db.add(UsageRecord(workspace_id=job.workspace_id, project_id=job.project_id, job_id=job.id, provider=job.provider, model=result.model, image_count=1 if job.kind == "IMAGE" else 0, video_seconds=float(result.duration or 0) if job.kind == "VIDEO" else 0, estimated_cost=job.estimated_cost, actual_cost=job.actual_cost))
        record(db, actor_type="WORKER", actor_id=owner, action="generation.complete", resource_type="generation_job", resource_id=job.id, workspace_id=job.workspace_id, project_id=job.project_id, trace_id=job.trace_id, agent_run_id=job.agent_run_id, safe_summary=job.kind)
        db.commit()
        project_id = job.project_id
        asset_id = asset.id
    registry.success(job.provider)
    notify(project_id, "generation.completed", {"job_id": job_id, "asset_id": asset_id})
    if job.agent_run_id:
        celery_app.send_task("production.advance", args=[job.agent_run_id], task_id=f"production-advance:{job.id}")


def fail_job(job_id: str, owner: str, error: Exception) -> None:
    retryable = isinstance(error, (TimeoutError, ConnectionError, OSError, subprocess.TimeoutExpired, httpx.TransportError)) or (
        isinstance(error, S3Error) and error.code in {"SlowDown", "InternalError", "ServiceUnavailable", "RequestTimeout", "RequestTimeTooSkewed"}
    )
    with SessionLocal() as db:
        project_id = db.scalar(select(GenerationJob.project_id).where(GenerationJob.id == job_id))
        if not project_id:
            return
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == job_id).with_for_update())
        if not job or job.status != "RUNNING" or job.lease_owner != owner:
            return
        job.error_code = "TEMPORARY_UNAVAILABLE" if retryable else "GENERATION_FAILED"
        job.error_message = str(error)[:500]
        job.lease_owner = None
        job.lease_expires_at = None
        if retryable and job.retry_count < job.max_retries:
            job.retry_count += 1
            transition_job(job, "RETRYING")
            db.add(OutboxEvent(workspace_id=job.workspace_id, project_id=job.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:{job.id}:{job.retry_count}", next_attempt_at=now() + retry_delay(job.retry_count)))
        else:
            transition_job(job, "FAILED")
            job.finished_at = now()
            settle(project, Decimal(job.estimated_cost), Decimal(0))
            shot = db.scalar(select(Shot).where(Shot.id == job.resource_id, Shot.workspace_id == job.workspace_id, Shot.project_id == job.project_id).with_for_update())
            if shot and shot.status == "GENERATING":
                transition_shot(shot, "FAILED")
        record(db, actor_type="WORKER", actor_id=owner, action="generation.fail", resource_type="generation_job", resource_id=job.id, workspace_id=job.workspace_id, project_id=job.project_id, trace_id=job.trace_id, agent_run_id=job.agent_run_id, result="FAILED", safe_summary=job.error_code)
        db.commit()
        project_id, provider = job.project_id, job.provider
    registry.failure(provider)
    notify(project_id, "generation.failed", {"job_id": job_id, "retrying": retryable})


@celery_app.task(name="generation.process", acks_late=True)
def process_generation(job_id: str):
    owner = claim_job(job_id)
    if not owner:
        return
    with lease_heartbeat(job_id, owner):
        try:
            with SessionLocal() as db:
                job = db.get(GenerationJob, job_id)
                db.expunge(job)
            result = generate(job)
            if result is None:
                defer_poll(job_id, owner)
            else:
                validate(result)
                with SessionLocal() as db:
                    shot = db.get(Shot, job.resource_id)
                    db.expunge(shot)
                inspection = inspect_media(shot, result, job.provider)
                complete_job(job_id, owner, result, inspection)
        except Exception as error:
            fail_job(job_id, owner, error)


@celery_app.task(name="production.advance", acks_late=True)
def process_production(run_id: str):
    from app.agent.automation import advance_production

    advance_production(run_id)


@celery_app.task(bind=True, name="knowledge.index", acks_late=True, max_retries=3)
def index_production_story(self, run_id, story_id):
    from app.rag.service import index_story

    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        if not run or (run.production_json or {}).get("story_id") != story_id:
            return
        if run.production_json.get("knowledge_index_status") == "COMPLETED":
            return
        story = db.scalar(select(StorySource).where(StorySource.id == story_id, StorySource.workspace_id == run.workspace_id, StorySource.project_id == run.project_id))
        if not story:
            return
        try:
            index_story(db, story)
            run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True))
            run.production_json = {**run.production_json, "knowledge_index_status": "COMPLETED"}
            db.commit()
        except Exception as error:
            db.rollback()
            run = db.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True))
            retrying = self.request.retries < self.max_retries
            run.production_json = {**run.production_json, "knowledge_index_status": "RETRYING" if retrying else "FAILED"}
            warning = f"知识索引{'重试中' if retrying else '失败'}：{type(error).__name__}"
            run.warnings = list(dict.fromkeys([*(run.warnings or []), warning]))[-50:]
            db.commit()
            if retrying:
                raise self.retry(exc=error, countdown=2 ** (self.request.retries + 1)) from error
