"""Authenticated provider relay callbacks; the worker remains the source of completion."""

import hashlib
import hmac
import json
import re
import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok
from app.audit.service import record
from app.config import settings
from app.db import get_db
from app.models import GenerationJob, OutboxEvent, ProviderCallbackReceipt, now

router = APIRouter()
MAX_BODY_BYTES = 16 * 1024
NONCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{16,128}\Z")
SIGNATURE_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class CallbackPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_job_id: UUID
    remote_job_id: str = Field(min_length=1, max_length=160)
    status: Literal["SUCCEEDED", "FAILED"]


def _single_header(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1:
        raise APIError("CALLBACK_SIGNATURE_INVALID", "Invalid callback authentication", 401)
    return values[0]


def _verify(request: Request, provider: str, body: bytes) -> str:
    if len(settings.provider_callback_secret) < 32:
        raise APIError("DEPENDENCY_UNAVAILABLE", "Provider callback is not configured", 503)
    if provider != "dashscope" or len(body) > MAX_BODY_BYTES:
        raise APIError("CALLBACK_SIGNATURE_INVALID", "Invalid callback authentication", 401)
    timestamp = _single_header(request, "X-FrameForge-Timestamp")
    nonce = _single_header(request, "X-FrameForge-Nonce")
    signature = _single_header(request, "X-FrameForge-Signature")
    if not timestamp.isascii() or not timestamp.isdecimal() or not 10 <= len(timestamp) <= 12 or not NONCE_PATTERN.fullmatch(nonce) or not SIGNATURE_PATTERN.fullmatch(signature):
        raise APIError("CALLBACK_SIGNATURE_INVALID", "Invalid callback authentication", 401)
    if abs(time.time() - int(timestamp)) > settings.provider_callback_max_skew_seconds:
        raise APIError("CALLBACK_EXPIRED", "Callback timestamp is outside the allowed window", 401)
    signed = provider.encode() + b"\n" + timestamp.encode() + b"\n" + nonce.encode() + b"\n" + body
    expected = hmac.new(settings.provider_callback_secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise APIError("CALLBACK_SIGNATURE_INVALID", "Invalid callback authentication", 401)
    return nonce


@router.post("/provider-callbacks/{provider}")
async def receive_provider_callback(provider: str, request: Request, db: Session = Depends(get_db)):
    parts = []
    size = 0
    async for part in request.stream():
        size += len(part)
        if size > MAX_BODY_BYTES:
            raise APIError("INVALID_PARAMETER", "Callback payload exceeds size limit", 413)
        parts.append(part)
    raw_body = b"".join(parts)
    nonce = _verify(request, provider, raw_body)
    try:
        payload = CallbackPayload.model_validate(json.loads(raw_body))
    except (ValueError, ValidationError) as error:
        raise APIError("INVALID_PARAMETER", "Invalid callback payload", 422) from error

    if db.scalar(select(ProviderCallbackReceipt.id).where(ProviderCallbackReceipt.provider == provider, ProviderCallbackReceipt.nonce == nonce)):
        raise APIError("CALLBACK_REPLAY", "Callback nonce was already used", 409)

    job = db.scalar(select(GenerationJob).where(GenerationJob.id == str(payload.provider_job_id)).with_for_update())
    if not job:
        raise APIError("RESOURCE_NOT_FOUND", "Generation job not found", 404)
    if job.provider != provider or job.kind != "VIDEO" or job.remote_job_id != payload.remote_job_id or job.status != "RUNNING":
        raise APIError("CALLBACK_JOB_MISMATCH", "Callback does not match a running provider job", 409)

    job.callback_received_at = now()
    db.add(ProviderCallbackReceipt(workspace_id=job.workspace_id, project_id=job.project_id, provider=provider, nonce=nonce, generation_job_id=job.id, remote_job_id=payload.remote_job_id, received_status=payload.status))
    db.add(OutboxEvent(workspace_id=job.workspace_id, project_id=job.project_id, topic="generation.dispatch", aggregate_id=job.id, payload={"job_id": job.id}, deduplication_key=f"generation:callback:{job.id}:{hashlib.sha256(nonce.encode()).hexdigest()}", next_attempt_at=now()))
    record(db, actor_type="PROVIDER", actor_id=provider, action="generation.callback", resource_type="generation_job", resource_id=job.id, workspace_id=job.workspace_id, project_id=job.project_id, trace_id=request.state.trace_id, safe_summary=payload.status)
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise APIError("CALLBACK_REPLAY", "Callback nonce was already used", 409) from error
    return ok(request, {"accepted": True, "job_id": job.id})
