import hashlib
import io
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.agent import automation
from app.api.errors import APIError
from app.asset import storage
from app.auth.dependencies import ProjectScope
from app.db import Base
from app.generation.service import request_generation
from app.models import (
    AgentRun,
    Asset,
    Character,
    CharacterVersion,
    Episode,
    GenerationJob,
    OutboxEvent,
    Project,
    ProjectMember,
    Scene,
    Shot,
    Timeline,
    User,
    Workspace,
    WorkspaceMember,
    now,
)
from app.providers.base import MediaResult
from app.storyboard import routes as shots_api
from app.timeline import routes as timelines
from app.workers import dispatcher, tasks


def request():
    req = Request({"type": "http", "method": "POST", "path": "/"})
    req.state.trace_id = "review-trace"
    return req


@pytest.fixture
def env(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, expire_on_commit=False)
    for module in (automation, tasks, dispatcher):
        monkeypatch.setattr(module, "SessionLocal", maker)
    monkeypatch.setattr(tasks, "publish", lambda *args: None)
    monkeypatch.setattr(tasks.celery_app, "send_task", lambda *args, **kwargs: None)
    with maker() as db:
        db.add(
            User(
                id="u",
                email="review@example.com",
                password_hash="unused",
                display_name="Review",
            )
        )
        db.add(Workspace(id="w", owner_id="u", name="Review"))
        db.add(Project(id="p", workspace_id="w", name="Review"))
        db.add(WorkspaceMember(workspace_id="w", user_id="u", role="OWNER"))
        db.add(
            ProjectMember(workspace_id="w", project_id="p", user_id="u", role="OWNER")
        )
        db.commit()
    yield SimpleNamespace(
        maker=maker, scope=ProjectScope("u", "session", "w", "p", "OWNER")
    )
    engine.dispose()


def make_shot(db, n=1, **extra):
    db.add(
        Episode(
            id=f"e{n}",
            workspace_id="w",
            project_id="p",
            episode_no=n,
            title=f"Episode {n}",
        )
    )
    db.add(
        Scene(
            id=f"sc{n}",
            workspace_id="w",
            project_id="p",
            episode_id=f"e{n}",
            scene_no=1,
            heading="Scene",
        )
    )
    shot = Shot(
        id=f"s{n}",
        workspace_id="w",
        project_id="p",
        episode_id=f"e{n}",
        scene_id=f"sc{n}",
        shot_no=1,
        description=f"Shot {n}",
        status="STORYBOARD_READY",
        **extra,
    )
    db.add(shot)
    db.commit()
    return shot


def png():
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "green").save(output, format="PNG")
    return output.getvalue()


def test_actual_generation_uses_preview_character_anchor(env):
    with env.maker() as db:
        shot = make_shot(db, character_ids=["c"])
        db.add(
            Character(
                id="c",
                workspace_id="w",
                project_id="p",
                name="Mira",
                active_version_id="cv",
            )
        )
        db.add(
            CharacterVersion(
                id="cv",
                workspace_id="w",
                project_id="p",
                character_id="c",
                version_no=1,
                status="ACTIVE",
                dna={"prompt_anchor": "UNIQUE_RED_SCARF", "face": "freckles"},
                created_by="u",
            )
        )
        db.commit()
        preview = shots_api.built_prompt(shot.id, request(), env.scope, db)["data"][
            "prompt"
        ]
        job = request_generation(
            db, env.scope, shot.id, "IMAGE", "review-anchor", "trace"
        )
        assert "UNIQUE_RED_SCARF" in preview
        assert job.input_json["prompt"] == preview
        assert job.input_json["character_versions"][0]["version_id"] == "cv"


def test_passing_image_preserves_failed_video_review(env, monkeypatch):
    with env.maker() as db:
        shot = make_shot(
            db,
            current_video_asset_id="bad-video",
            inspection_json={"status": "FAIL", "issues": ["broken video"]},
        )
        shot.status = "REVIEW_REQUIRED"
        db.commit()
        job = request_generation(
            db, env.scope, shot.id, "IMAGE", "review-image", "trace"
        )
        db.commit()
        job_id = job.id
    monkeypatch.setattr(
        tasks,
        "store_result",
        lambda **kw: Asset(
            workspace_id="w",
            project_id="p",
            bucket="b",
            object_key="new.png",
            mime="image/png",
            sha256="0" * 64,
            size=1,
        ),
    )
    owner = tasks.claim_job(job_id)
    tasks.complete_job(
        job_id,
        owner,
        MediaResult(content=png(), mime="image/png", cost=0, model="fake"),
        {"status": "PASS"},
    )
    with env.maker() as db:
        shot = db.get(Shot, "s1")
        with pytest.raises(APIError):
            shots_api.transition(
                shot.id,
                shots_api.ShotAction(expected_version=shot.version, target="APPROVED"),
                request(),
                env.scope,
                db,
            )
        assert shot.inspection_json["status"] == "FAIL"
        assert shot.current_video_asset_id == "bad-video"
        assert timelines.reviewed_video(shot, "bad-video") is False


def test_unverified_video_can_be_human_reviewed(env):
    with env.maker() as db:
        shot = make_shot(
            db, current_video_asset_id="video", inspection_json={"status": "UNVERIFIED"}
        )
        shot.status = "REVIEW_REQUIRED"
        db.commit()
        shots_api.transition(
            shot.id,
            shots_api.ShotAction(
                expected_version=shot.version,
                target="APPROVED",
                review_reason="Human checked video",
            ),
            request(),
            env.scope,
            db,
        )
        assert shot.status == "APPROVED"
        assert shot.inspection_json["review_override"]["video_asset_id"] == "video"
        assert timelines.reviewed_video(shot, "video") is True


def test_approved_shot_rejects_content_edits(env):
    with env.maker() as db:
        shot = make_shot(
            db, current_video_asset_id="video", inspection_json={"status": "PASS"}
        )
        shot.status = "APPROVED"
        db.commit()
        editor = ProjectScope("u", "session", "w", "p", "EDITOR")
        with pytest.raises(APIError):
            shots_api.edit_shot(
                shot.id,
                shots_api.ShotEdit(
                    expected_version=shot.version,
                    dialogue="Completely changed dialogue",
                ),
                request(),
                editor,
                db,
            )
        assert shot.dialogue == ""
        assert shot.status == "APPROVED"
        assert timelines.reviewed_video(shot, "video") is True


def test_idempotent_generation_returns_existing_job(env):
    with env.maker() as db:
        shot = make_shot(db)
        first = request_generation(
            db, env.scope, shot.id, "IMAGE", "same-request", "trace"
        )
        db.commit()
        second = request_generation(
            db, env.scope, shot.id, "IMAGE", "same-request", "trace"
        )
        assert first.id == second.id
        assert len(db.scalars(select(GenerationJob)).all()) == 1
        assert float(db.get(Project, "p").budget_reserved) == float(
            first.estimated_cost
        )


def test_comfyui_transport_timeout_retries(env):
    import httpx

    with env.maker() as db:
        shot = make_shot(db)
        job = request_generation(
            db, env.scope, shot.id, "IMAGE", "review-comfy", "trace"
        )
        job.provider = "comfyui"
        db.commit()
        job_id = job.id
    owner = tasks.claim_job(job_id)
    tasks.fail_job(job_id, owner, httpx.ReadTimeout("temporary ComfyUI read timeout"))
    with env.maker() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "RETRYING" and job.retry_count == 1


def test_timeline_recreation_is_idempotent_and_schema_protects_episode(env):
    from sqlalchemy.exc import IntegrityError

    with env.maker() as db:
        make_shot(db)
        first = timelines.create_timeline("e1", request(), env.scope, db)["data"]
        second = timelines.create_timeline("e1", request(), env.scope, db)["data"]
        assert first["id"] == second["id"]
        db.add(Timeline(workspace_id="w", project_id="p", episode_id="e1"))
        with pytest.raises(IntegrityError):
            db.flush()


def test_heartbeat_keeps_long_generation_owned(env, monkeypatch):
    clock = [now()]
    monkeypatch.setattr(tasks, "now", lambda: clock[0])
    with env.maker() as db:
        shot = make_shot(db)
        job = request_generation(
            db, env.scope, shot.id, "IMAGE", "long-generation", "trace"
        )
        db.commit()
        job_id = job.id
    owner = tasks.claim_job(job_id)
    clock[0] += timedelta(seconds=270)
    assert tasks.renew_lease(job_id, owner)
    clock[0] += timedelta(seconds=60)
    assert tasks.claim_job(job_id) is None
    assert not tasks.renew_lease(job_id, "other-worker")


def test_two_episodes_store_distinct_final_assets(env, monkeypatch):
    writes = []

    class MemoryStorage:
        def bucket_exists(self, *args):
            return True

        def put_object(self, bucket, key, stream, length, **kwargs):
            writes.append(key)

    monkeypatch.setattr(storage, "client", MemoryStorage)
    monkeypatch.setattr(storage, "validate", lambda result: (1280, 720, 2.0))
    monkeypatch.setattr(automation, "validate", lambda result: (1280, 720, 2.0))
    monkeypatch.setattr(
        automation,
        "render_timeline",
        lambda clips: MediaResult(content=b"video", mime="video/mp4", duration=2),
    )
    with env.maker() as db:
        for n in (1, 2):
            shot = make_shot(db, n, current_video_asset_id=f"v{n}")
            shot.status = "APPROVED"
            db.add(
                Asset(
                    id=f"v{n}",
                    workspace_id="w",
                    project_id="p",
                    bucket="b",
                    object_key=f"video{n}.mp4",
                    mime="video/mp4",
                    sha256="0" * 64,
                    size=1,
                )
            )
            for kind in ("IMAGE", "VIDEO"):
                db.add(
                    GenerationJob(
                        workspace_id="w",
                        project_id="p",
                        provider="fake",
                        model="fake",
                        resource_type="shot",
                        resource_id=shot.id,
                        kind=kind,
                        input_json={},
                        idempotency_key=f"{n}-{kind}",
                        status="SUCCEEDED",
                        trace_id="trace",
                        agent_run_id="run",
                        inspection_json={"status": "PASS"},
                    )
                )
        db.add(
            AgentRun(
                id="run",
                workspace_id="w",
                project_id="p",
                user_id="u",
                session_id="session",
                request="two episodes",
                status="RUNNING",
                trace_id="trace",
                production_json={"mode": "AUTOPRODUCE", "shot_ids": ["s1", "s2"]},
            )
        )
        db.commit()
    for _ in range(3):
        automation.advance_production("run")
    with env.maker() as db:
        run = db.get(AgentRun, "run")
        assert run.status == "COMPLETED"
        assert len(db.scalars(select(Timeline)).all()) == 2
        assert len(writes) == 2 and len(set(writes)) == 2


def test_quarantine_promotes_exact_validated_bytes(monkeypatch):
    original = png()
    copied = {}

    class Response:
        def read(self, *args):
            return original

        def close(self):
            pass

        def release_conn(self):
            pass

    class Storage:
        def get_object(self, *args):
            return Response()

        def put_object(self, bucket, key, stream, length, **kwargs):
            copied[key] = stream.read()

    monkeypatch.setattr(storage, "client", Storage)
    asset = Asset(
        id="a",
        workspace_id="w",
        project_id="p",
        bucket="b",
        object_key="quarantine/a",
        mime="image/png",
        sha256="",
        size=len(original),
        status="QUARANTINED",
    )
    storage.promote_quarantine(asset, len(original))
    assert asset.status == "READY"
    assert hashlib.sha256(copied[asset.object_key]).hexdigest() == asset.sha256


def test_manual_jobs_recover_after_worker_and_notification_failure(env, monkeypatch):
    with env.maker() as db:
        shot = make_shot(db)
        job = request_generation(
            db, env.scope, shot.id, "IMAGE", "manual-recovery", "trace"
        )
        db.flush()
        job_id = job.id
        db.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == job_id)
        ).published_at = now()
        db.commit()

    def unavailable(*args):
        raise ConnectionError("Redis temporarily unavailable")

    monkeypatch.setattr(tasks, "publish", unavailable)
    assert tasks.claim_job(job_id)
    with env.maker() as db:
        db.get(GenerationJob, job_id).lease_expires_at = now() - timedelta(seconds=1)
        db.get(GenerationJob, job_id).last_heartbeat_at = now() - timedelta(minutes=6)
        db.commit()
    assert dispatcher.recover_expired_jobs() == 1
    assert dispatcher.recover_expired_jobs() == 0
    monkeypatch.setattr(
        tasks,
        "generate",
        lambda job: MediaResult(content=png(), mime="image/png", cost=0, model="fake"),
    )
    monkeypatch.setattr(
        tasks,
        "store_result",
        lambda **kw: Asset(
            workspace_id="w",
            project_id="p",
            bucket="b",
            object_key="manual.png",
            mime="image/png",
            sha256="0" * 64,
            size=1,
        ),
    )
    tasks.process_generation(job_id)
    with env.maker() as db:
        assert db.get(GenerationJob, job_id).status == "SUCCEEDED"


def test_expired_job_redelivery_has_a_cooldown_after_publication(env, monkeypatch):
    clock = [now()]
    monkeypatch.setattr(dispatcher, "now", lambda: clock[0])
    with env.maker() as db:
        shot = make_shot(db)
        job = request_generation(db, env.scope, shot.id, "IMAGE", "recovery-cooldown", "trace")
        db.flush()
        job.status = "RUNNING"
        job.lease_expires_at = clock[0] - timedelta(minutes=1)
        job.last_heartbeat_at = clock[0] - timedelta(minutes=6)
        db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == job.id)).published_at = clock[0]
        db.commit()
    assert dispatcher.recover_expired_jobs() == 1
    clock[0] += timedelta(seconds=1)
    assert dispatcher.dispatch_batch(publisher=lambda event: None) == 1
    clock[0] += timedelta(seconds=15)
    assert dispatcher.recover_expired_jobs() == 0
    clock[0] += timedelta(minutes=5)
    assert dispatcher.recover_expired_jobs() == 1


def test_reindex_failure_preserves_working_index(env, monkeypatch):
    from app.models import KnowledgeDocument, KnowledgeVersion, StorySource
    from app.rag import service

    points = {"existing": "working knowledge"}

    class VectorStore:
        def delete(self, *args, **kwargs):
            assert (
                kwargs["points_selector"].filter.must[0].match.value
                == "existing-document"
            )
            points.clear()

        def upsert(self, *args, **kwargs):
            points.update({"new": "new data"})

    class Provider:
        model = "fake"
        dimensions = 64

        def embed(self, text):
            raise TimeoutError("embedding service temporarily unavailable")

    monkeypatch.setattr(service, "client", VectorStore)
    monkeypatch.setattr(service, "selected_embedding_provider", Provider)
    monkeypatch.setattr(service, "ensure_collection", lambda *args: None)
    with env.maker() as db:
        story = StorySource(
            workspace_id="w",
            project_id="p",
            title="Novel",
            content="Existing published story that should remain searchable.",
            created_by="u",
        )
        db.add(story)
        db.flush()
        db.add(
            KnowledgeDocument(
                id="existing-document",
                workspace_id="w",
                project_id="p",
                source_type="story",
                source_id=story.id,
                current_version_id="existing-version",
                status="ACTIVE",
            )
        )
        db.add(
            KnowledgeVersion(
                id="existing-version",
                workspace_id="w",
                project_id="p",
                document_id="existing-document",
                version_no=1,
                content_hash=hashlib.sha256(story.content.encode()).hexdigest(),
                status="ACTIVE",
            )
        )
        db.commit()
        with pytest.raises(TimeoutError):
            service.index_story(db, story)
        db.rollback()
        assert points == {"existing": "working knowledge"}


def test_manual_timeline_preserves_longer_speech(env, tmp_path):
    import wave

    from app.models import TimelineItem
    from app.timeline.render import normalized_audio

    with env.maker() as db:
        shot = make_shot(
            db,
            duration=2,
            dialogue="Dialogue lasts six seconds",
            current_video_asset_id="video",
            current_audio_asset_id="voice",
            inspection_json={"status": "PASS"},
        )
        shot.status = "APPROVED"
        db.add(
            Asset(
                id="voice",
                workspace_id="w",
                project_id="p",
                bucket="b",
                object_key="voice.wav",
                mime="audio/wav",
                sha256="0" * 64,
                size=264644,
                duration=6,
            )
        )
        db.commit()
        timeline = timelines.create_timeline("e1", request(), env.scope, db)["data"]
        timelines.sync_timeline(
            timeline["id"],
            timelines.VersionPayload(expected_version=timeline["version"]),
            request(),
            env.scope,
            db,
        )
        item = db.scalar(
            select(TimelineItem).where(
                TimelineItem.timeline_id == timeline["id"],
                TimelineItem.asset_id == "voice",
            )
        )
        assert item.duration_seconds == 6
    source = io.BytesIO()
    with wave.open(source, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\x01\x01" * (22050 * 6))
    rendered = normalized_audio(source.getvalue(), tmp_path, 0, item.duration_seconds)
    assert len(rendered) / (22050 * 2) == 6


def test_planning_crash_is_durably_recovered(env, monkeypatch):
    from app.agent import routes
    from app.config import settings

    monkeypatch.setattr(settings, "director_llm_mode", "fake")

    def terminated(*args, **kwargs):
        raise SystemExit("simulated API termination")

    monkeypatch.setattr(routes, "initialize_run", terminated)
    with env.maker() as db:
        with pytest.raises(SystemExit):
            routes.create_run(
                routes.RunCreate(
                    request="produce",
                    intent="AUTOPRODUCE",
                    story_content="Mira waits at the tower for the letter.",
                ),
                request(),
                env.scope,
                db,
            )
    sent = []
    monkeypatch.setattr(
        dispatcher.celery_app, "send_task", lambda *a, **kw: sent.append(a)
    )
    dispatcher.dispatch_productions()
    assert sent
    with env.maker() as db:
        run_id = db.scalar(select(AgentRun.id))
    automation.advance_production(run_id)
    with env.maker() as db:
        run = db.get(AgentRun, run_id)
        assert run.status == "RUNNING"
        assert run.production_json["shot_ids"]
        assert db.scalar(
            select(OutboxEvent).where(OutboxEvent.topic == "knowledge.index")
        )


def test_transient_s3_error_retries(env):
    from minio.error import S3Error

    with env.maker() as db:
        shot = make_shot(db)
        job = request_generation(db, env.scope, shot.id, "IMAGE", "review-s3", "trace")
        db.commit()
        job_id = job.id
    owner = tasks.claim_job(job_id)
    tasks.fail_job(
        job_id,
        owner,
        S3Error(
            response=None,
            code="SlowDown",
            message="Please reduce request rate",
            resource="/bucket/object",
            request_id="request",
            host_id="host",
        ),
    )
    with env.maker() as db:
        job = db.get(GenerationJob, job_id)
        assert job.status == "RETRYING" and job.retry_count == 1


def desktop_module(name, relative):
    import importlib

    return importlib.import_module(
        relative.removeprefix("backend/").removesuffix(".py").replace("/", ".")
    )


def test_fake_planner_never_calls_real_provider(monkeypatch):
    module = desktop_module("desktop_production", "backend/app/agent/production.py")
    called = []

    def model_call(self, prompt):
        called.append(True)
        return {
            "characters": [{"name": "Mira"}],
            "episodes": [
                {
                    "title": "E",
                    "scenes": [
                        {
                            "heading": "S",
                            "shots": [{"description": "Mira", "duration": 2}],
                        }
                    ],
                }
            ],
        }

    monkeypatch.setattr(module.DashScopeLLMProvider, "complete_json", model_call)
    module.plan_story("Mira at the tower.", "Make a film", module.ProductionOptions())
    assert called == []


def test_reviewer_can_reject(env):
    module = desktop_module("desktop_shots", "backend/app/storyboard/routes.py")
    with env.maker() as db:
        shot = make_shot(db)
        shot.status = "REVIEW_REQUIRED"
        db.commit()
        scope = ProjectScope("u", "session", "w", "p", "REVIEWER")
        module.transition(
            shot.id,
            module.ShotAction(expected_version=shot.version, target="REJECTED"),
            request(),
            scope,
            db,
        )
        assert shot.status == "REJECTED"


def test_reorder_preserves_locked_neighbor(env):
    module = desktop_module("desktop_shots", "backend/app/storyboard/routes.py")
    with env.maker() as db:
        first = make_shot(db)
        locked = Shot(
            id="locked",
            workspace_id="w",
            project_id="p",
            episode_id="e1",
            scene_id="sc1",
            shot_no=2,
            status="LOCKED",
        )
        db.add(locked)
        db.commit()
        with pytest.raises(APIError):
            module.reorder(
                first.id,
                module.Reorder(expected_version=first.version, target_no=2),
                request(),
                env.scope,
                db,
            )
        assert locked.status == "LOCKED" and locked.shot_no == 2


def test_resync_invalidates_old_final_asset(env):
    module = desktop_module("desktop_timelines", "backend/app/timeline/routes.py")
    with env.maker() as db:
        shot = make_shot(
            db, current_video_asset_id="new-video", inspection_json={"status": "PASS"}
        )
        shot.status = "APPROVED"
        db.commit()
        row = module.create_timeline("e1", request(), env.scope, db)["data"]
        timeline = db.get(Timeline, row["id"])
        timeline.status = "COMPLETED"
        timeline.final_asset_id = "old-final"
        db.commit()
        result = module.sync_timeline(
            timeline.id,
            module.VersionPayload(expected_version=timeline.version),
            request(),
            env.scope,
            db,
        )["data"]
        assert result["status"] == "READY" and result["final_asset_id"] is None


def test_director_dependency_error_persists_failed_run(env, monkeypatch):
    from app.agent import routes

    def dependency_down(*args):
        raise ConnectionError("Qdrant unavailable")

    monkeypatch.setattr(routes, "execute", dependency_down)
    with env.maker() as db:
        with pytest.raises(APIError):
            routes.create_run(
                routes.RunCreate(request="Analyze the project", intent="ANALYZE"),
                request(),
                env.scope,
                db,
            )
    with env.maker() as db:
        run = db.scalar(select(AgentRun))
        assert run.status == "FAILED" and run.production_json is None
        assert run.status not in {"WAITING_APPROVAL", "BLOCKED_AUTH"}


def test_nullable_inspector_score_keeps_media_unverified(env, monkeypatch):
    from app.agent import inspector
    from app.config import settings

    monkeypatch.setattr(settings, "dashscope_api_key", "review-mocked-key")
    monkeypatch.setattr(
        inspector.httpx,
        "post",
        lambda *a, **kw: SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "choices": [
                    {
                        "message": {
                            "content": '{"status":"PASS","score":null,"issues":[],"checks":{}}'
                        }
                    }
                ]
            },
        ),
    )
    shot = Shot(description="Mira", action="", dialogue="", prompt="", character_ids=[])
    assert (
        inspector.inspect_media(
            shot, MediaResult(content=png(), mime="image/png"), "dashscope"
        )["status"]
        == "UNVERIFIED"
    )


@pytest.mark.asyncio
async def test_sse_stream_uses_async_iterator(env, monkeypatch):
    from collections.abc import AsyncIterable

    from app.generation import routes

    response = routes.events(env.scope)
    assert isinstance(response.body_iterator, AsyncIterable)


def test_failed_video_requires_human_review_before_lock(env):
    with env.maker() as db:
        shot = make_shot(
            db, current_video_asset_id="video", inspection_json={"status": "UNVERIFIED"}
        )
        shot.status = "APPROVED"
        db.commit()
        with pytest.raises(APIError):
            shots_api.transition(
                shot.id,
                shots_api.ShotAction(expected_version=shot.version, target="LOCKED"),
                request(),
                env.scope,
                db,
            )
        shots_api.transition(
            shot.id,
            shots_api.ShotAction(
                expected_version=shot.version,
                target="APPROVED",
                review_reason="Human checked full video",
            ),
            request(),
            env.scope,
            db,
        )
        shots_api.transition(
            shot.id,
            shots_api.ShotAction(expected_version=shot.version, target="LOCKED"),
            request(),
            env.scope,
            db,
        )
        assert timelines.reviewed_video(shot, "video")


def test_editor_cannot_overturn_reviewer_approval(env):
    module = desktop_module("desktop_shots", "backend/app/storyboard/routes.py")
    with env.maker() as db:
        shot = make_shot(db)
        shot.status = "APPROVED"
        db.commit()
        scope = ProjectScope("u", "session", "w", "p", "EDITOR")
        with pytest.raises(APIError):
            module.transition(
                shot.id,
                module.ShotAction(expected_version=shot.version, target="REJECTED"),
                request(),
                scope,
                db,
            )
        assert shot.status == "APPROVED"
