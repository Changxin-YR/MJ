import asyncio
from types import SimpleNamespace

import anyio.to_thread
import httpx
import pytest
from sqlalchemy import select
from test_review_regressions import env as env

from app.auth.dependencies import project_scope
from app.generation import routes
from app.main import app
from app.models import KnowledgeDocument, KnowledgeVersion, StorySource
from app.rag import service


@pytest.mark.asyncio
async def test_waiting_sse_clients_leave_threads_for_regular_endpoints(env, monkeypatch):
    release = asyncio.Event()
    started = []

    class Subscriber:
        async def subscribe(self, channel):
            pass

        async def get_message(self, **kwargs):
            started.append(True)
            await release.wait()
            raise RuntimeError("End the test stream")

        async def aclose(self):
            pass

    class Connection:
        def pubsub(self):
            return Subscriber()

        async def aclose(self):
            pass

    monkeypatch.setattr(routes.redis, "from_url", lambda *args: Connection())
    app.dependency_overrides[project_scope] = lambda: env.scope
    limiter = anyio.to_thread.current_default_thread_limiter()
    original = limiter.total_tokens
    limiter.total_tokens = 4
    streams = []
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            streams = [asyncio.create_task(client.get("/api/v1/projects/p/events")) for _ in range(4)]
            async with asyncio.timeout(3):
                while len(started) < 4:
                    await asyncio.sleep(0.01)
            response = await asyncio.wait_for(client.get("/health/live"), timeout=1)
            assert response.status_code == 200
            release.set()
            await asyncio.gather(*streams, return_exceptions=True)
    finally:
        release.set()
        await asyncio.gather(*streams, return_exceptions=True)
        limiter.total_tokens = original
        app.dependency_overrides.pop(project_scope, None)


def test_partial_vector_upload_never_replaces_the_searchable_version(env, monkeypatch):
    vectors = {}

    class Provider:
        model = "fake"
        dimensions = 64

        def embed(self, text):
            return [0.0] * 64

    class VectorStore:
        fail = False

        def upsert(self, name, points, **kwargs):
            for point in points:
                vectors[point.id] = point.payload
                if self.fail:
                    raise ConnectionError("Upload failed after one point")

        def query_points(self, **kwargs):
            conditions = kwargs["query_filter"].must
            versions = next(c.match.any for c in conditions if c.key == "version_id")
            return SimpleNamespace(points=[SimpleNamespace(payload=p, score=1.0) for p in vectors.values() if p["version_id"] in versions])

    q = VectorStore()
    monkeypatch.setattr(service, "client", lambda: q)
    monkeypatch.setattr(service, "SessionLocal", env.maker)
    monkeypatch.setattr(service, "selected_embedding_provider", Provider)
    monkeypatch.setattr(service, "ensure_collection", lambda *args: None)
    with env.maker() as db:
        story = StorySource(workspace_id="w", project_id="p", title="Story", content="Original searchable chapter", created_by="u")
        db.add(story)
        db.commit()
        document = service.index_story(db, story)
        db.commit()
        old_version = document.current_version_id
        story.content = "Changed chapter with a partial vector upload"
        db.commit()
        q.fail = True
        with pytest.raises(ConnectionError):
            service.index_story(db, story)
        db.rollback()
        assert db.scalar(select(KnowledgeDocument.current_version_id)) == old_version
        assert db.get(KnowledgeVersion, old_version).status == "ACTIVE"
        results = service.retrieve(workspace_id="w", project_id="p", query="chapter")
        assert len(results) == 1 and results[0]["text"] == "Original searchable chapter"
        q.fail = False
        service.index_story(db, story)
        db.commit()
        results = service.retrieve(workspace_id="w", project_id="p", query="chapter")
        assert len(results) == 1 and results[0]["text"] == story.content
        assert results[0]["version_id"] != old_version
