from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from starlette.requests import Request
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.auth.dependencies import ProjectScope
from app.db import SessionLocal, engine
from app.generation.service import request_generation
from app.main import app
from app.models import (
    GenerationJob,
    Project,
    ProjectMember,
    Shot,
    StorySource,
    Timeline,
)
from app.timeline.routes import create_timeline

pytestmark = pytest.mark.skipif(engine.dialect.name != "mysql", reason="Exercises MySQL current reads and row locks")


def setup_project():
    client = TestClient(app)
    _, token = register(client, "review-mysql")
    prefix, shot = make_ready_shot(client, token)
    with SessionLocal() as db:
        row = db.get(Shot, shot["id"])
        member = db.scalar(select(ProjectMember).where(ProjectMember.project_id == row.project_id))
        scope = ProjectScope(member.user_id, uuid4().hex, row.workspace_id, row.project_id, "OWNER")
    return client, token, prefix, shot, scope


def test_concurrent_generation_requests_share_one_reservation():
    _, _, _, shot, scope = setup_project()
    key = uuid4().hex
    barrier = Barrier(2)

    def submit():
        with SessionLocal() as db:
            # Establish a repeatable-read snapshot before either request commits.
            assert db.scalar(select(GenerationJob).where(GenerationJob.project_id == scope.project_id)) is None
            barrier.wait(timeout=10)
            job = request_generation(db, scope, shot["id"], "IMAGE", key, "review")
            db.commit()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.project_id == scope.project_id)) == 1
        assert db.get(Project, scope.project_id).budget_reserved == db.get(GenerationJob, ids[0]).estimated_cost


def test_concurrent_timeline_creation_returns_one_timeline():
    _, _, _, shot, scope = setup_project()
    barrier = Barrier(2)

    def submit():
        with SessionLocal() as db:
            assert db.scalar(select(Timeline).where(Timeline.episode_id == shot["episode_id"])) is None
            barrier.wait(timeout=10)
            request = Request({"type": "http", "method": "POST", "path": "/"})
            request.state.trace_id = "review"
            return create_timeline(shot["episode_id"], request, scope, db)["data"]["id"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: submit(), range(2)))
    assert ids[0] == ids[1]


def test_chinese_story_exceeding_text_capacity_round_trips():
    client, token, prefix, _, scope = setup_project()
    content = "雨夜的信使走进旧城。" * 10000
    code, body = call(client, "POST", f"{prefix}/stories", token, json={"title": "Long story", "content": content})
    assert code == 200, body
    with SessionLocal() as db:
        row = db.get(StorySource, body["data"]["id"])
        assert row.project_id == scope.project_id and row.content == content
