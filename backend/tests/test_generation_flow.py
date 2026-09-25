from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_core_flow import call, register

from app.db import SessionLocal
from app.main import app
from app.models import GenerationJob, OutboxEvent, Shot
from app.providers.registry import registry
from app.workers.dispatcher import dispatch_batch
from app.workers.tasks import claim_job, fail_job, process_generation


def make_ready_shot(client, token):
    _, body = call(client, "POST", "/api/v1/workspaces", token, json={"name": "Generation Studio"})
    workspace = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace}/projects", token, json={"name": "Generation"})
    project = body["data"]["id"]
    prefix = f"/api/v1/projects/{project}"
    _, body = call(client, "POST", f"{prefix}/episodes", token, json={"title": "Pilot"})
    episode = body["data"]["id"]
    _, body = call(client, "POST", f"{prefix}/episodes/{episode}/scenes", token, json={"heading": "EXT. ROOFTOP"})
    scene = body["data"]["id"]
    _, body = call(client, "POST", f"{prefix}/scenes/{scene}/shots", token, json={"description": "A courier runs across the rooftop", "dialogue": "The city is waking up.", "duration": 2})
    shot = body["data"]
    for target in ("PLANNED", "STORYBOARD_READY"):
        code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": target})
        assert code == 200, body
        shot = body["data"]
    return prefix, shot


def test_job_outbox_idempotency_and_fake_image():
    client = TestClient(app)
    _, token = register(client, "producer")
    prefix, shot = make_ready_shot(client, token)
    key = str(uuid4())
    request = {"kind": "IMAGE", "idempotency_key": key}
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json=request)
    assert code == 200, body
    job = body["data"]
    assert job["status"] == "QUEUED"
    code, duplicate = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json=request)
    assert code == 200 and duplicate["data"]["id"] == job["id"]

    def fail_broker(_event):
        raise ConnectionError("temporary broker outage")

    assert dispatch_batch(publisher=fail_broker) == 0
    code, body = call(client, "GET", f"{prefix}/generation-jobs/{job['id']}", token)
    assert code == 200 and body["data"]["status"] == "QUEUED"
    process_generation(job["id"])
    process_generation(job["id"])
    code, body = call(client, "GET", f"{prefix}/generation-jobs/{job['id']}", token)
    assert code == 200 and body["data"]["status"] == "SUCCEEDED", body
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
    assert code == 200 and body["data"]["status"] == "REVIEW_REQUIRED"
    assert body["data"]["current_image_asset_id"]
    response = client.get(f"{prefix}/assets/{body['data']['current_image_asset_id']}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG")


def test_worker_retries_transient_provider_failure_then_stops_on_permanent_failure():
    client = TestClient(app)
    _, token = register(client, "provider-fault")
    _, shot = make_ready_shot(client, token)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        job = GenerationJob(
            workspace_id=row.workspace_id,
            project_id=row.project_id,
            provider="fake",
            model="fake-image",
            resource_type="shot",
            resource_id=row.id,
            kind="IMAGE",
            input_json={},
            idempotency_key=uuid4().hex,
            status="QUEUED",
            trace_id=str(uuid4()),
        )
        db.add(job)
        db.commit()
        job_id = job.id

    owner = claim_job(job_id)
    assert owner
    fail_job(job_id, owner, TimeoutError("temporary provider timeout"))
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "RETRYING" and job.retry_count == 1
        assert db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id, OutboxEvent.deduplication_key == f"generation:{job_id}:1"))

    owner = claim_job(job_id)
    assert owner
    fail_job(job_id, owner, ValueError("permanent provider response"))
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "FAILED" and job.finished_at is not None
        assert job.error_code == "GENERATION_FAILED"
    registry.success("fake")


def test_voice_check_preserves_visual_failure_and_review_requires_reason():
    client = TestClient(app)
    _, token = register(client, "visual-review")
    prefix, shot = make_ready_shot(client, token)
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": "IMAGE", "idempotency_key": str(uuid4())})
    assert code == 200, body
    process_generation(body["data"]["id"])
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        row.inspection_json = {"status": "FAIL", "score": 0.2, "issues": ["Two characters"], "checks": {"character_count": "FAIL"}, "method": "QWEN_VL"}
        db.commit()
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": "VOICE", "idempotency_key": str(uuid4())})
    assert code == 200, body
    process_generation(body["data"]["id"])
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
    assert code == 200 and body["data"]["inspection_json"]["status"] == "FAIL"
    shot = body["data"]
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": "APPROVED"})
    assert code == 409 and body["error"]["code"] == "INSPECTION_FAILED"
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": "APPROVED", "review_reason": "I inspected the full video and confirmed one character."})
    assert code == 200 and body["data"]["status"] == "APPROVED"
    assert body["data"]["inspection_json"]["review_override"]["reason"] == "I inspected the full video and confirmed one character."



def test_generation_job_uses_active_character_dna_in_real_prompt():
    client = TestClient(app)
    _, token = register(client, "character-prompt")
    prefix, shot = make_ready_shot(client, token)
    code, body = call(client, "PATCH", prefix + "/settings", token, json={
        "expected_version": 1,
        "settings": {"style": "水墨电影感国漫"},
    })
    assert code == 200, body
    code, body = call(client, "POST", prefix + "/characters", token, json={
        "name": "林舟",
        "background": "城市信使",
        "dna": {
            "face": "清晰的东方青年面孔",
            "hair": "短黑发",
            "costume": "深蓝风衣",
            "style": "电影感国漫",
            "prompt_anchor": "林舟始终穿深蓝风衣，短黑发",
            "negative_prompt": "金色长发，日式校服",
        },
    })
    assert code == 200, body
    character = body["data"]
    version_id = character["versions"][0]["id"]
    code, body = call(client, "POST", f"{prefix}/characters/{character['id']}/versions/{version_id}/activate", token, json={"expected_version": character["version"]})
    assert code == 200, body

    code, body = call(client, "PATCH", f"{prefix}/shots/{shot['id']}", token, json={"expected_version": shot["version"], "character_ids": [character["id"]]})
    assert code == 200, body
    shot = body["data"]
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": "IMAGE", "idempotency_key": str(uuid4())})
    assert code == 200, body
    with SessionLocal() as db:
        job = db.get(GenerationJob, body["data"]["id"])
        assert "林舟始终穿深蓝风衣，短黑发" in job.input_json["prompt"]
        assert "深蓝风衣" in job.input_json["prompt"]
        assert "项目统一视觉风格：水墨电影感国漫" in job.input_json["prompt"]
        assert "金色长发，日式校服" in job.input_json["negative_prompt"]


def test_editing_approved_shot_invalidates_derived_media_and_review():
    client = TestClient(app)
    _, token = register(client, "stale-media")
    prefix, shot = make_ready_shot(client, token)
    for kind in ("IMAGE", "VIDEO", "VOICE"):
        code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": kind, "idempotency_key": str(uuid4())})
        assert code == 200, body
        process_generation(body["data"]["id"])
        code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
        assert code == 200, body
        shot = body["data"]

    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={"expected_version": shot["version"], "target": "APPROVED"})
    assert code == 200, body
    approved = body["data"]
    assert approved["current_image_asset_id"] and approved["current_video_asset_id"] and approved["current_audio_asset_id"]

    code, body = call(client, "PATCH", f"{prefix}/shots/{shot['id']}", token, json={
        "expected_version": approved["version"],
        "description": "完全不同的新画面",
        "dialogue": "这是修改后的全新对白。",
    })
    assert code == 200, body
    edited = body["data"]
    assert edited["status"] == "PLANNED"
    assert edited["current_image_asset_id"] is None
    assert edited["current_video_asset_id"] is None
    assert edited["current_audio_asset_id"] is None
    assert edited["inspection_json"] is None


def test_shot_cannot_be_edited_while_generation_is_running():
    client = TestClient(app)
    _, token = register(client, "edit-during-generation")
    prefix, shot = make_ready_shot(client, token)
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": "IMAGE", "idempotency_key": str(uuid4())})
    assert code == 200, body
    code, body = call(client, "GET", f"{prefix}/shots/{shot['id']}", token)
    assert code == 200 and body["data"]["status"] == "GENERATING"
    generating = body["data"]
    code, body = call(client, "PATCH", f"{prefix}/shots/{shot['id']}", token, json={"expected_version": generating["version"], "description": "不应在生成中被修改"})
    assert code == 409 and body["error"]["code"] == "RESOURCE_CONFLICT"



def test_non_chinese_visible_text_cannot_be_overridden():
    client = TestClient(app)
    _, token = register(client, "language-policy")
    prefix, shot = make_ready_shot(client, token)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        row.status = "REVIEW_REQUIRED"
        row.inspection_json = {
            "status": "FAIL",
            "score": 0.1,
            "issues": ["Korean text is visible on a sign"],
            "checks": {"visible_text_language": "FAIL"},
            "method": "QWEN_VL",
        }
        db.commit()
        version = row.version
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/transition", token, json={
        "expected_version": version,
        "target": "APPROVED",
        "review_reason": "人工确认后仍想强制通过",
    })
    assert code == 409 and body["error"]["code"] == "LANGUAGE_POLICY_FAILED"
