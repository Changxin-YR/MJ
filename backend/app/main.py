from uuid import uuid4

import redis
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from minio import Minio
from qdrant_client import QdrantClient
from sqlalchemy import text

from app.agent.routes import router as agent_router
from app.api.errors import APIError, api_error_handler, ok, validation_error_handler
from app.asset.routes import router as asset_router
from app.audit.routes import router as audit_router
from app.auth.routes import router as auth_router
from app.character.routes import router as character_router
from app.config import settings
from app.db import engine
from app.generation.routes import router as generation_router
from app.rag.routes import router as rag_router
from app.story.routes import router as story_router
from app.storyboard.routes import router as storyboard_router
from app.timeline.routes import router as timeline_router
from app.workspace.routes import router as workspace_router

app = FastAPI(title="FrameForge AI", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)


@app.middleware("http")
async def add_trace(request: Request, call_next):
    request.state.trace_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


@app.get("/health/live")
def live(request: Request):
    return ok(request, {"status": "live"})


@app.get("/health/ready")
def ready(request: Request):
    checks = {}
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        checks["mysql"] = True
    except Exception:
        checks["mysql"] = False
    try:
        checks["redis"] = bool(redis.from_url(settings.redis_url).ping())
    except Exception:
        checks["redis"] = False
    try:
        checks["qdrant"] = QdrantClient(url=settings.qdrant_url, timeout=2).get_collections() is not None
    except Exception:
        checks["qdrant"] = False
    try:
        checks["minio"] = Minio(settings.minio_endpoint, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=False).list_buckets() is not None
    except Exception:
        checks["minio"] = False
    if not all(checks.values()):
        raise APIError("DEPENDENCY_UNAVAILABLE", "Core dependency unavailable", 503, checks)
    return ok(request, checks)


app.include_router(auth_router, prefix="/api/v1")
app.include_router(workspace_router, prefix="/api/v1")
app.include_router(story_router, prefix="/api/v1")
app.include_router(character_router, prefix="/api/v1")
app.include_router(storyboard_router, prefix="/api/v1")
app.include_router(rag_router, prefix="/api/v1")
app.include_router(generation_router, prefix="/api/v1")
app.include_router(asset_router, prefix="/api/v1")
app.include_router(agent_router, prefix="/api/v1")
app.include_router(timeline_router, prefix="/api/v1")
app.include_router(audit_router, prefix="/api/v1")
