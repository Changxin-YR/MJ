import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import OutboxEvent, now
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def dispatch_batch(limit: int = 20, publisher=None) -> int:
    publisher = publisher or (lambda event: celery_app.send_task("generation.process", args=[event.payload["job_id"]], task_id=event.deduplication_key))
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
    while True:
        try:
            dispatch_batch()
        except Exception:
            logger.exception("outbox dispatch cycle failed")
        time.sleep(2)


if __name__ == "__main__":
    main()
