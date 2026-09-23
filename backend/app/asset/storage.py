import hashlib
import io
import json
import subprocess
import tempfile
import wave
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error
from PIL import Image, UnidentifiedImageError

from app.api.errors import APIError
from app.config import settings
from app.models import Asset
from app.providers.base import MediaResult

MAX_SIZE = 50 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 25_000_000


def client() -> Minio:
    return Minio(settings.minio_endpoint, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=False)


def public_client() -> Minio:
    return Minio(settings.minio_public_endpoint, access_key=settings.minio_access_key, secret_key=settings.minio_secret_key, secure=False, region="us-east-1")


def validate(result: MediaResult) -> tuple[int | None, int | None, float | None]:
    data = result.content
    if not data or len(data) > MAX_SIZE:
        raise APIError("INVALID_PARAMETER", "Invalid asset size", 422)
    if result.mime == "image/png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise APIError("INVALID_PARAMETER", "Invalid PNG magic", 422)
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.verify()
            with Image.open(io.BytesIO(data)) as img:
                if img.width * img.height > Image.MAX_IMAGE_PIXELS:
                    raise APIError("INVALID_PARAMETER", "Image dimensions too large", 422)
                return img.width, img.height, None
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
            raise APIError("INVALID_PARAMETER", "Invalid image", 422) from error
    if result.mime == "video/mp4":
        if data[4:8] != b"ftyp":
            raise APIError("INVALID_PARAMETER", "Invalid MP4 magic", 422)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "asset.mp4"
            path.write_bytes(data)
            proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=width,height", "-of", "json", str(path)], capture_output=True, text=True, timeout=15)
            if proc.returncode:
                raise APIError("INVALID_PARAMETER", "Invalid MP4", 422)
            metadata = json.loads(proc.stdout)
            duration = float(metadata["format"]["duration"])
            stream = next((stream for stream in metadata.get("streams", []) if "width" in stream), {})
            if duration <= 0 or duration > 1800 or stream.get("width", 0) * stream.get("height", 0) > 25_000_000:
                raise APIError("INVALID_PARAMETER", "Invalid video dimensions or duration", 422)
            return stream.get("width"), stream.get("height"), duration
    if result.mime == "audio/wav":
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise APIError("INVALID_PARAMETER", "Invalid WAV magic", 422)
        with wave.open(io.BytesIO(data)) as audio:
            return None, None, audio.getnframes() / audio.getframerate()
    raise APIError("INVALID_PARAMETER", "Unsupported asset MIME", 422)


def create_quarantine_asset(*, workspace_id: str, project_id: str, mime: str, declared_size: int) -> tuple[Asset, str]:
    if mime not in {"image/png", "video/mp4", "audio/wav"} or declared_size < 1 or declared_size > MAX_SIZE:
        raise APIError("INVALID_PARAMETER", "Unsupported upload type or size", 422)
    asset_id = str(uuid4())
    key = f"{workspace_id}/{project_id}/quarantine/{asset_id}"
    storage = client()
    if not storage.bucket_exists(settings.minio_bucket):
        storage.make_bucket(settings.minio_bucket)
    url = public_client().presigned_put_object(settings.minio_bucket, key, expires=timedelta(minutes=10))
    return Asset(id=asset_id, workspace_id=workspace_id, project_id=project_id, bucket=settings.minio_bucket, object_key=key, mime=mime, sha256="", size=declared_size, status="QUARANTINED"), url


def promote_quarantine(asset: Asset, declared_size: int) -> str:
    if asset.status != "QUARANTINED":
        raise APIError("RESOURCE_CONFLICT", "Upload already finalized", 409)
    storage = client()
    try:
        response = storage.get_object(asset.bucket, asset.object_key)
    except S3Error as error:
        raise APIError("RESOURCE_CONFLICT", "Upload object is missing", 409) from error
    try:
        data = response.read(MAX_SIZE + 1)
    finally:
        response.close()
        response.release_conn()
    if len(data) != declared_size:
        raise APIError("INVALID_PARAMETER", "Uploaded size differs from reservation", 422)
    width, height, duration = validate(MediaResult(content=data, mime=asset.mime))
    suffix = {"image/png": "png", "video/mp4": "mp4", "audio/wav": "wav"}[asset.mime]
    ready_key = f"{asset.workspace_id}/{asset.project_id}/uploads/{asset.id}.{suffix}"
    storage.copy_object(asset.bucket, ready_key, CopySource(asset.bucket, asset.object_key))
    quarantine_key = asset.object_key
    asset.object_key = ready_key
    asset.sha256 = hashlib.sha256(data).hexdigest()
    asset.size = len(data)
    asset.width, asset.height, asset.duration = width, height, duration
    asset.status = "READY"
    return quarantine_key


def store_result(*, workspace_id: str, project_id: str, job_id: str, result: MediaResult, source_job_id: str | None = None) -> Asset:
    width, height, duration = validate(result)
    suffix = {"image/png": "png", "video/mp4": "mp4", "audio/wav": "wav"}[result.mime]
    object_key = f"{workspace_id}/{project_id}/{job_id}.{suffix}"
    storage = client()
    if not storage.bucket_exists(settings.minio_bucket):
        storage.make_bucket(settings.minio_bucket)
    storage.put_object(settings.minio_bucket, object_key, io.BytesIO(result.content), len(result.content), content_type=result.mime)
    return Asset(workspace_id=workspace_id, project_id=project_id, bucket=settings.minio_bucket, object_key=object_key, mime=result.mime, sha256=hashlib.sha256(result.content).hexdigest(), size=len(result.content), width=width, height=height, duration=duration, status="READY", source_job_id=source_job_id or job_id)
