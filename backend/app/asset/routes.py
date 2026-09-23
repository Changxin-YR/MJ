from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.asset.storage import client, create_quarantine_asset, promote_quarantine
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import Asset

router = APIRouter(prefix="/projects/{project_id}/assets", tags=["assets"])


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


@router.post("/uploads/{asset_id}/finalize")
def finalize_upload(asset_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "content.edit")
    asset = db.scalar(select(Asset).where(Asset.id == asset_id, Asset.workspace_id == scope.workspace_id, Asset.project_id == scope.project_id).with_for_update())
    if not asset:
        raise APIError("RESOURCE_NOT_FOUND", "Upload not found", 404)
    quarantine_key = promote_quarantine(asset, asset.size)
    record(db, actor_type="USER", actor_id=scope.user_id, action="asset.upload.finalize", resource_type="asset", resource_id=asset.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    client().remove_object(asset.bucket, quarantine_key)
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
