from datetime import timedelta

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
from app.audit.service import record
from app.auth.dependencies import Identity, current_identity
from app.auth.security import (
    access_token,
    hash_password,
    new_refresh_token,
    token_hash,
    verify_password,
)
from app.config import settings
from app.db import get_db
from app.models import ServerSession, User, now

router = APIRouter(prefix="/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)


class Register(Credentials):
    display_name: str = Field(min_length=1, max_length=120)


def set_refresh(response: Response, session_id: str, token: str) -> None:
    response.set_cookie("refresh_token", f"{session_id}.{token}", httponly=True, secure=settings.refresh_cookie_secure, samesite="lax", max_age=30 * 86400, path="/api/v1/auth")


def issue(db: Session, response: Response, user: User) -> dict:
    refresh = new_refresh_token()
    session = ServerSession(user_id=user.id, refresh_hash=token_hash(refresh), used_refresh_hashes=[], expires_at=now() + timedelta(days=30))
    db.add(session)
    db.flush()
    set_refresh(response, session.id, refresh)
    return {"access_token": access_token(user.id, session.id), "user": {"id": user.id, "email": user.email, "display_name": user.display_name}}


@router.post("/register")
def register(payload: Register, request: Request, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise APIError("RESOURCE_CONFLICT", "Email already registered", 409)
    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password), display_name=payload.display_name)
    db.add(user)
    db.flush()
    data = issue(db, response, user)
    record(db, actor_type="USER", actor_id=user.id, action="auth.register", resource_type="user", resource_id=user.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, data)


@router.post("/login")
def login(payload: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or user.disabled or not verify_password(payload.password, user.password_hash):
        raise APIError("AUTH_REQUIRED", "Invalid credentials", 401)
    data = issue(db, response, user)
    record(db, actor_type="USER", actor_id=user.id, action="auth.login", resource_type="session", resource_id=None, trace_id=trace_id(request))
    db.commit()
    return ok(request, data)


@router.post("/refresh")
def refresh(request: Request, response: Response, refresh_token: str | None = Cookie(default=None), db: Session = Depends(get_db)):
    if not refresh_token or "." not in refresh_token:
        raise APIError("AUTH_REQUIRED", "Refresh token required", 401)
    session_id, raw = refresh_token.split(".", 1)
    session = db.scalar(select(ServerSession).where(ServerSession.id == session_id).with_for_update())
    if not session or session.revoked_at or session.expires_at <= now():
        raise APIError("AUTH_REQUIRED", "Session expired", 401)
    incoming = token_hash(raw)
    if incoming != session.refresh_hash:
        if incoming in session.used_refresh_hashes:
            session.revoked_at = now()
            db.commit()
        raise APIError("AUTH_REQUIRED", "Refresh token invalid", 401)
    user = db.get(User, session.user_id)
    if not user or user.disabled:
        raise APIError("AUTH_REQUIRED", "Session expired", 401)
    session.used_refresh_hashes = [*session.used_refresh_hashes[-9:], session.refresh_hash]
    fresh = new_refresh_token()
    session.refresh_hash = token_hash(fresh)
    set_refresh(response, session.id, fresh)
    record(db, actor_type="USER", actor_id=user.id, action="auth.refresh", resource_type="session", resource_id=session.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"access_token": access_token(user.id, session.id), "user": {"id": user.id, "email": user.email, "display_name": user.display_name}})


@router.post("/logout")
def logout(request: Request, response: Response, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    session = db.get(ServerSession, identity.session_id)
    session.revoked_at = now()
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    record(db, actor_type="USER", actor_id=identity.user_id, action="auth.logout", resource_type="session", resource_id=session.id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"revoked": True})


@router.get("/me")
def me(request: Request, identity: Identity = Depends(current_identity), db: Session = Depends(get_db)):
    user = db.get(User, identity.user_id)
    return ok(request, {"id": user.id, "email": user.email, "display_name": user.display_name})
