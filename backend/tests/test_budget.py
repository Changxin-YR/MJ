from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.main import app
from app.providers.registry import registry


def test_parallel_reservations_do_not_exceed_budget():
    client = TestClient(app)
    _, owner = register(client, "budget")
    prefix, first = make_ready_shot(client, owner)
    code, body = call(client, "POST", f"{prefix}/scenes/{first['scene_id']}/shots", owner, json={"description": "Another rooftop view"})
    assert code == 200, body
    second = body["data"]
    for state in ("PLANNED", "STORYBOARD_READY"):
        _, body = call(client, "POST", f"{prefix}/shots/{second['id']}/transition", owner, json={"expected_version": second["version"], "target": state})
        second = body["data"]
    code, body = call(client, "PATCH", prefix + "/settings", owner, json={"expected_version": 1, "settings": {}, "budget_limit": 1.5})
    assert code == 200, body
    barrier = Barrier(2)
    original = registry.entries["fake"].cost["IMAGE"]
    registry.entries["fake"].cost["IMAGE"] = 1.0
    try:
        def submit(shot_id):
            barrier.wait()
            with TestClient(app) as request_client:
                return call(request_client, "POST", f"{prefix}/shots/{shot_id}/generations", owner, json={"kind": "IMAGE", "idempotency_key": str(uuid4())})

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, [first["id"], second["id"]]))
    finally:
        registry.entries["fake"].cost["IMAGE"] = original
    assert sorted(code for code, _ in results) == [200, 409], results
    assert next(body for code, body in results if code == 409)["error"]["code"] == "BUDGET_EXCEEDED"
    code, body = call(client, "GET", prefix, owner)
    assert code == 200 and body["data"]["budget_reserved"] == 1.0
