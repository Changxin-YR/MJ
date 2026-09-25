import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.asset.storage import (
    MAX_SIZE,
    client,
    create_quarantine_asset,
    promote_quarantine,
    store_result,
)
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import Asset
from app.providers.base import MediaResult

router = APIRouter(prefix="/projects/{project_id}/assets", tags=["assets"])
logger = logging.getLogger(__name__)


class UploadIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mime: str
    size: int = Field(gt=0, le=50 * 1024 * 1024)


def asset_data(asset: Asset) -> dict:
    return {"id": asset.id, "mime": asset.mime, "size": asset.size, "width": asset.width, "height": asset.height, "duration": asset.duration, "status": asset.status, "source_job_id": asset.source_job_id}


@router.post("/uploads")
def create_upload(payload: UploadIntent, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    asset, url = create_quarantine_asset(workspace_id=scope.workspace_id, project_id=scope.project_id, mime=payload.mime, declared_size=payload.size)
    db.add(asset)
    record(db, actor_type="USER", actor_id=scope.user_id, action="asset.upload.reserve", resource_type="asset", resource_id=asset.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"asset": asset_data(asset), "upload_url": url, "expires_in_seconds": 600})


@router.post("/uploads/direct")
async def direct_upload(
    request: Request,
    file: UploadFile = File(...),
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "content.edit")
    mime = file.content_type or ""
    if mime not in {"image/png", "video/mp4", "audio/wav", "audio/x-wav"}:
        raise APIError("INVALID_PARAMETER", "Unsupported upload type", 422)
    data = await file.read(MAX_SIZE + 1)
    await file.close()
    if not data or len(data) > MAX_SIZE:
        raise APIError("INVALID_PARAMETER", "Invalid asset size", 422)
    normalized_mime = "audio/wav" if mime == "audio/x-wav" else mime
    asset = store_result(
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        job_id=f"upload-{uuid4()}",
        result=MediaResult(content=data, mime=normalized_mime),
        source_job_id=None,
    )
    asset.source_job_id = None
    db.add(asset)
    db.flush()
    record(
        db,
        actor_type="USER",
        actor_id=scope.user_id,
        action="asset.upload.direct",
        resource_type="asset",
        resource_id=asset.id,
        workspace_id=scope.workspace_id,
        project_id=scope.project_id,
        trace_id=trace_id(request),
        safe_summary=normalized_mime,
    )
    db.commit()
    return ok(request, asset_data(asset))


@router.post("/uploads/{asset_id}/finalize")
def finalize_upload(asset_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == scope.workspace_id, Asset.project_id == scope.project_id).with_for_update())
    if not asset:
        raise APIError("RESOURCE_NOT_FOUND", "Upload not found", 404)
    quarantine_key = promote_quarantine(asset, asset.size)
    record(db, actor_type="USER", actor_id=scope.user_id, action="asset.upload.finalize", resource_type="asset", resource_id=asset.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    try:
        client().remove_object(asset.bucket, quarantine_key)
    except Exception:
        logger.warning("quarantine cleanup failed after successful finalize", exc_info=True)
    return ok(request, asset_data(asset))


@router.get("")
def list_assets(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    rows = db.scalars(select(Asset).where(Asset.workspace_id == scope.workspace_id, Asset.project_id == scope.project_id).order_by(Asset.created_at.desc()).limit(100)).all()
    return ok(request, [asset_data(a) for a in rows])


@router.get("/{asset_id}/content")
def content(asset_id: str, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    asset = scoped_get(db, Asset, asset_id, scope)
    if asset.status != "READY":
        raise APIError("RESOURCE_CONFLICT", "Asset is not ready", 409)
    response = client().get_object(asset.bucket, asset.object_key)
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    return Response(content=data, media_type=asset.mime, headers={"Content-Disposition": "inline", "Cache-Control": "private, max-age=60"})
