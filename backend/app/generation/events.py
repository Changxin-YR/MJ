import json
import logging

import redis

from app.config import settings


def publish(project_id: str, name: str, data: dict) -> None:
    try:
        redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2).publish(f"project:{project_id}:events", json.dumps({"event": name, "data": data}))
    except redis.RedisError:
        logging.getLogger(__name__).exception("project notification unavailable", extra={"project_id": project_id})
