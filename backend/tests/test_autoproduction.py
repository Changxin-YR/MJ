from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.agent import automation
from app.api.errors import APIError
from app.db import SessionLocal
from app.main import app
from app.models import AgentRun, GenerationJob, ProjectMember, Shot
from app.workers import tasks


@pytest.mark.parametrize("automatic,blocked", [(False, False), (True, False), (True, True)])
def test_media_continuation_is_scoped_and_preserves_completed_asset(monkeypatch, automatic, blocked):
    client = TestClient(app)
    _, token = register(client, "automatic-media")
    prefix, shot = make_ready_shot(client, token)
    code, body = call(client, "POST", f"{prefix}/shots/{shot['id']}/generations", token, json={"kind": "IMAGE", "idempotency_key": uuid4().hex})
    assert code == 200, body
    job_id = body["data"]["id"]
    with SessionLocal() as db:
        job = db.get(GenerationJob, job_id)
        member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == job.project_id))
        run = AgentRun(workspace_id=job.workspace_id, project_id=job.project_id, user_id=member.user_id, session_id=uuid4().hex, request="test", status="RUNNING", trace_id=uuid4().hex, production_json={"mode": "AUTOPRODUCE", "shot_ids": [shot["id"]]} if automatic else None)
        db.add(run)
        db.flush()
        job.agent_run_id = run.id
        run_id = run.id
        db.commit()

    if blocked:
        def reject(*args, **kwargs):
            raise APIError("BUDGET_EXCEEDED", "Project budget exceeded", 409)
        monkeypatch.setattr(automation, "request_generation", reject)

    tasks.process_generation(job_id)
    tasks.process_generation(job_id)
    automation.advance_production(run_id)
    automation.advance_production(run_id)
    with SessionLocal() as db:
        assert db.get(GenerationJob, job_id).status == "SUCCEEDED"
        assert db.get(Shot, shot["id"]).current_image_asset_id
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.agent_run_id == run_id)).all()
        if not automatic or blocked:
            assert len(jobs) == 1
            return
        video = next(job for job in jobs if job.kind == "VIDEO")
        video_id = video.id
    tasks.process_generation(video_id)
    automation.advance_production(run_id)
    with SessionLocal() as db:
        voice = db.scalar(select(GenerationJob).where(GenerationJob.agent_run_id == run_id, GenerationJob.kind == "VOICE"))
        assert voice
        voice_id = voice.id
    tasks.process_generation(voice_id)
    automation.advance_production(run_id)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        assert row.current_video_asset_id and row.current_audio_asset_id
        assert row.status == "APPROVED"
        run = db.get(AgentRun, run_id)
        assert run.status == "COMPLETED"
        final_id = run.production_json["finals"][0]["asset_id"]
    automation.advance_production(run_id)
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).production_json["finals"][0]["asset_id"] == final_id
    response = client.get(f"{prefix}/assets/{final_id}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and b"ftyp" in response.content[:20]


def test_missing_story_does_not_leave_a_running_run():
    client = TestClient(app)
    _, token = register(client, "missing-story")
    prefix, _ = make_ready_shot(client, token)
    code, _ = call(client, "POST", f"{prefix}/director/runs", token, json={"request": "make a drama", "intent": "AUTOPRODUCE"})
    assert code == 422
    code, body = call(client, "GET", f"{prefix}/director/runs", token)
    assert code == 200 and body["data"] == []
