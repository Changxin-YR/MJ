import json

import redis

from app.config import settings


def publish(project_id: str, name: str, data: dict) -> None:
    redis.from_url(settings.redis_url).publish(f"project:{project_id}:events", json.dumps({"event": name, "data": data}))
