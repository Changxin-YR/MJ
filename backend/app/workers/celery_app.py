from celery import Celery

from app.config import settings

celery_app = Celery("frameforge", broker=settings.redis_url, backend=settings.redis_url, include=["app.workers.tasks"])
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_reject_on_worker_lost=True, task_serializer="json", accept_content=["json"], result_serializer="json")
