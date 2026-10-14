import logging
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select

from app.db import SessionLocal
from app.models import AgentRun, GenerationJob, OutboxEvent, now
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def publish_event(event):
    if event.topic == "generation.dispatch":
        name, args = "generation.process", [event.payload["job_id"]]
    elif event.topic == "knowledge.index":
        name, args = "knowledge.index", [event.payload["run_id"], event.payload["story_id"]]
    else:
        raise ValueError(f"Unsupported outbox topic: {event.topic}")
    return celery_app.send_task(name, args=args, task_id=event.deduplication_key)


def recover_expired_jobs(limit=100):
    cutoff = now() - timedelta(minutes=5)
    with SessionLocal() as db:
        jobs = db.scalars(select(GenerationJob).where(or_(
            and_(GenerationJob.status == "RUNNING", or_(GenerationJob.lease_expires_at.is_(None), GenerationJob.lease_expires_at <= now()), or_(GenerationJob.last_heartbeat_at.is_(None), GenerationJob.last_heartbeat_at <= cutoff)),
            and_(GenerationJob.status.in_(["QUEUED", "RETRYING"]), GenerationJob.created_at <= cutoff, or_(GenerationJob.last_heartbeat_at.is_(None), GenerationJob.last_heartbeat_at <= cutoff)),
        )).order_by(GenerationJob.created_at).limit(limit).with_for_update(skip_locked=True)).all()
        recovered = 0
        for job in jobs:
            pending = db.scalar(select(OutboxEvent.id).where(OutboxEvent.aggregate_id == job.id, OutboxEvent.published_at.is_(None)))
            if pending:
                continue
            job.lease_owner = None
            job.last_heartbeat_at = now()
            db.add(OutboxEvent(workspace_id=job.workspace_id, project_id=job.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:recover:{job.id}:{uuid4()}"))
            recovered += 1
        db.commit()
        return recovered


def dispatch_batch(limit: int = 20, publisher=None) -> int:
    publisher = publisher or publish_event
    processed = 0
    with SessionLocal() as db:
        events = db.scalars(select(OutboxEvent).where(OutboxEvent.published_at.is_(None), OutboxEvent.next_attempt_at <= now()).order_by(OutboxEvent.created_at).limit(limit).with_for_update(skip_locked=True)).all()
        for event in events:
            try:
                publisher(event)
                event.published_at = now()
                processed += 1
            except Exception:
                logger.exception("outbox publish failed", extra={"event_id": event.id})
                event.attempts += 1
                event.next_attempt_at = now() + timedelta(seconds=min(300, 2 ** min(event.attempts, 8)))
        db.commit()
    return processed


def main():
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","message":"%(message)s"}')
    last_production_scan = 0.0
    while True:
        try:
            dispatch_batch()
            if time.monotonic() - last_production_scan >= 15:
                recover_expired_jobs()
                dispatch_productions()
                last_production_scan = time.monotonic()
        except Exception:
            logger.exception("outbox dispatch cycle failed")
        time.sleep(2)


def dispatch_productions():
    # Repeated delivery is intentional: the coordinator locks the run and derives
    # progress from persisted jobs, so broker/worker restarts cannot lose a stage.
    with SessionLocal() as db:
        runs = db.scalars(select(AgentRun).where(AgentRun.status.in_(["PLANNING", "RUNNING"]))).all()
        for run in runs:
            if (run.production_json or {}).get("mode") == "AUTOPRODUCE":
                celery_app.send_task("production.advance", args=[run.id])


if __name__ == "__main__":
    main()
