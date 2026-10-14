from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.agent import automation, routes
from app.agent.production import ProductionPlan
from app.db import SessionLocal
from app.main import app
from app.models import (
    AgentRun,
    GenerationJob,
    OutboxEvent,
    ProjectMember,
    Timeline,
    now,
)
from app.workers import tasks


def start_production(monkeypatch, retries=1):
    client = TestClient(app)
    _, token = register(client, "story-to-film")
    prefix, _ = make_ready_shot(client, token)
    plan = ProductionPlan.model_validate({"characters": [{"name": "Mira"}], "episodes": [{"title": "Signal", "scenes": [{"heading": "Tower", "shots": [{"description": "Mira lights the tower", "character_names": ["Mira"], "dialogue": "The city is safe.", "duration": 2}]}]}]})
    monkeypatch.setattr(routes, "plan_story", lambda *args: plan)
    code, body = call(client, "POST", f"{prefix}/director/runs", token, json={"intent": "AUTOPRODUCE", "request": "Make a complete film", "story_content": "Mira carries a signal to the tower and lights the sleeping city.", "production": {"max_shots": 1, "max_quality_retries": retries}})
    assert code == 200, body
    assert body["data"]["status"] == "RUNNING"
    return client, token, prefix, body["data"]["id"]


def queued_jobs(run_id):
    with SessionLocal() as db:
        return db.scalars(select(GenerationJob.id).where(GenerationJob.agent_run_id == run_id, GenerationJob.status == "QUEUED")).all()


def tick(run_id):
    for job_id in queued_jobs(run_id):
        tasks.process_generation(job_id)
    automation.advance_production(run_id)


def test_story_to_final_video_without_approval(monkeypatch):
    client, token, prefix, run_id = start_production(monkeypatch)
    for _ in range(4):
        tick(run_id)
    code, body = call(client, "GET", f"/api/v1/director/runs/{run_id}", token)
    run = body["data"]
    assert code == 200 and run["status"] == "COMPLETED"
    assert run["pending_actions"] == []
    assert {j["kind"] for j in run["generation_jobs"]} == {"IMAGE", "VIDEO", "VOICE"}
    final = run["production"]["finals"][0]
    with SessionLocal() as db:
        assert db.get(Timeline, final["timeline_id"]).status == "COMPLETED"
    response = client.get(f"{prefix}/assets/{final['asset_id']}/content", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and b"ftyp" in response.content[:20]


def test_failed_inspection_retries_then_stops_at_limit(monkeypatch):
    _, _, _, run_id = start_production(monkeypatch)
    monkeypatch.setattr(tasks, "inspect_media", lambda *args: {"status": "FAIL", "issues": ["visual defect"]})
    for _ in range(4):
        tick(run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run.status == "FAILED"
        assert any("QUALITY_RETRIES_EXHAUSTED" in w for w in run.warnings)
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.agent_run_id == run_id)).all()
        assert len(jobs) == 2 and all(j.kind == "IMAGE" for j in jobs)
        assert not run.production_json.get("finals")


def test_expired_generation_lease_is_recovered(monkeypatch):
    _, _, _, run_id = start_production(monkeypatch)
    job_id = queued_jobs(run_id)[0]
    assert tasks.claim_job(job_id)
    with SessionLocal() as db:
        db.get(GenerationJob, job_id).lease_expires_at = now() - timedelta(minutes=1)
        db.commit()
    automation.advance_production(run_id)
    automation.advance_production(run_id)
    with SessionLocal() as db:
        events = db.scalars(select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id, OutboxEvent.deduplication_key.like("production:recover:%"))).all()
        assert len(events) == 1
    tasks.process_generation(job_id)
    automation.advance_production(run_id)
    for _ in range(3):
        tick(run_id)
    with SessionLocal() as db:
        assert db.get(AgentRun, run_id).status == "COMPLETED"


def test_render_retries_automatically(monkeypatch):
    _, _, _, run_id = start_production(monkeypatch)
    original = automation.render_timeline
    failures = []
    def intermittent(clips):
        if not failures:
            failures.append(True)
            raise OSError("temporary storage error")
        return original(clips)
    monkeypatch.setattr(automation, "render_timeline", intermittent)
    for _ in range(5):
        tick(run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run.status == "COMPLETED"
        assert run.production_json["automatic_errors"] == 1


@pytest.mark.parametrize("role", ["VIEWER", "REVIEWER"])
def test_revoked_production_permission_stops_automation(monkeypatch, role):
    _, _, _, run_id = start_production(monkeypatch)
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == run.project_id, ProjectMember.user_id == run.user_id))
        member.role = role
        db.commit()
    automation.advance_production(run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        assert run.status == "FAILED"
        assert any("PERMISSION_DENIED" in w for w in run.warnings)


def test_unverified_inspection_does_not_exhaust_media_retries(monkeypatch):
    client, token, prefix, run_id = start_production(monkeypatch)
    monkeypatch.setattr(tasks, "inspect_media", lambda *args: {"status": "UNVERIFIED", "issues": ["inspector unavailable"]})
    for _ in range(6):
        tick(run_id)
    code, body = call(client, "GET", f"/api/v1/director/runs/{run_id}", token)
    assert code == 200 and body["data"]["status"] == "COMPLETED"
    assert any("inspector unavailable" in warning for warning in body["data"]["warnings"])
