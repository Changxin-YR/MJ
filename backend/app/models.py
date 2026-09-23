"""Canonical business records. Every project-owned record carries SQL scope columns."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class ScopeMixin:
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)


class User(IdMixin, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ServerSession(IdMixin, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    used_refresh_hashes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class Workspace(IdMixin, Base):
    __tablename__ = "workspaces"
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)


class WorkspaceMember(IdMixin, Base):
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class Project(IdMixin, Base):
    __tablename__ = "projects"
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    budget_limit: Mapped[float] = mapped_column(Numeric(12, 4), default=100, nullable=False)
    budget_used: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    budget_reserved: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)


class ProjectMember(IdMixin, Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)
    workspace_id: Mapped[str] = mapped_column(String(36), ForeignKey("workspaces.id"), nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)


class StorySource(IdMixin, ScopeMixin, Base):
    __tablename__ = "story_sources"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)


class ProjectBible(IdMixin, ScopeMixin, Base):
    __tablename__ = "project_bibles"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Character(IdMixin, ScopeMixin, Base):
    __tablename__ = "characters"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active_version_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CharacterVersion(IdMixin, ScopeMixin, Base):
    __tablename__ = "character_versions"
    character_id: Mapped[str] = mapped_column(String(36), ForeignKey("characters.id"), nullable=False, index=True)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False)
    dna: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    background: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    __table_args__ = (UniqueConstraint("character_id", "version_no"),)


class CharacterRelationship(IdMixin, ScopeMixin, Base):
    __tablename__ = "character_relationships"
    source_character_id: Mapped[str] = mapped_column(String(36), nullable=False)
    target_character_id: Mapped[str] = mapped_column(String(36), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)


class Episode(IdMixin, ScopeMixin, Base):
    __tablename__ = "episodes"
    episode_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    synopsis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "episode_no"),)


class Scene(IdMixin, ScopeMixin, Base):
    __tablename__ = "scenes"
    episode_id: Mapped[str] = mapped_column(String(36), ForeignKey("episodes.id"), nullable=False, index=True)
    scene_no: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (UniqueConstraint("episode_id", "scene_no"),)


class Shot(IdMixin, ScopeMixin, Base):
    __tablename__ = "shots"
    episode_id: Mapped[str] = mapped_column(String(36), ForeignKey("episodes.id"), nullable=False, index=True)
    scene_id: Mapped[str] = mapped_column(String(36), ForeignKey("scenes.id"), nullable=False, index=True)
    shot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_type: Mapped[str] = mapped_column(String(60), default="medium", nullable=False)
    camera_angle: Mapped[str] = mapped_column(String(60), default="eye-level", nullable=False)
    camera_movement: Mapped[str] = mapped_column(String(60), default="static", nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=6, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    action: Mapped[str] = mapped_column(Text, default="", nullable=False)
    dialogue: Mapped[str] = mapped_column(Text, default="", nullable=False)
    emotion: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    negative_prompt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_image_asset_id: Mapped[str | None] = mapped_column(String(36))
    current_video_asset_id: Mapped[str | None] = mapped_column(String(36))
    current_audio_asset_id: Mapped[str | None] = mapped_column(String(36))
    inspection_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __table_args__ = (UniqueConstraint("scene_id", "shot_no"),)


class Asset(IdMixin, ScopeMixin, Base):
    __tablename__ = "assets"
    bucket: Mapped[str] = mapped_column(String(120), nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="READY", nullable=False)
    source_job_id: Mapped[str | None] = mapped_column(String(36))


class GenerationJob(IdMixin, ScopeMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key"), Index("ix_generation_status_lease", "status", "lease_expires_at"))
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    input_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    inspection_json: Mapped[dict | None] = mapped_column(JSON)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    actual_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    remote_job_id: Mapped[str | None] = mapped_column(String(160))
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)
    callback_received_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="CREATED", nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    tool_call_id: Mapped[str | None] = mapped_column(String(36))
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class ProviderCallbackReceipt(IdMixin, ScopeMixin, Base):
    __tablename__ = "provider_callback_receipts"
    __table_args__ = (UniqueConstraint("provider", "nonce", name="uq_provider_callback_nonce"),)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    generation_job_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    remote_job_id: Mapped[str] = mapped_column(String(160), nullable=False)
    received_status: Mapped[str] = mapped_column(String(30), nullable=False)


class GenerationOutput(IdMixin, ScopeMixin, Base):
    __tablename__ = "generation_outputs"
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)


class AgentRun(IdMixin, ScopeMixin, Base):
    __tablename__ = "agent_runs"
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="CREATED", nullable=False)
    current_node: Mapped[str] = mapped_column(String(80), default="START", nullable=False)
    decision_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    retrieved_sources: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)


class ToolCall(IdMixin, ScopeMixin, Base):
    __tablename__ = "tool_calls"
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)


class PendingAction(IdMixin, ScopeMixin, Base):
    __tablename__ = "pending_actions"
    __table_args__ = (UniqueConstraint("agent_run_id", "action", "target_id", name="uq_pending_action_run_action_target"),)
    agent_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("agent_runs.id"), nullable=False)
    requester_id: Mapped[str] = mapped_column(String(36), nullable=False)
    approver_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    arguments_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime)


class KnowledgeDocument(IdMixin, ScopeMixin, Base):
    __tablename__ = "knowledge_documents"
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)
    current_version_id: Mapped[str | None] = mapped_column(String(36))


class KnowledgeVersion(IdMixin, ScopeMixin, Base):
    __tablename__ = "knowledge_versions"
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("knowledge_documents.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", nullable=False)


class Timeline(IdMixin, ScopeMixin, Base):
    __tablename__ = "timelines"
    episode_id: Mapped[str] = mapped_column(String(36), ForeignKey("episodes.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    final_asset_id: Mapped[str | None] = mapped_column(String(36))


class TimelineTrack(IdMixin, ScopeMixin, Base):
    __tablename__ = "timeline_tracks"
    timeline_id: Mapped[str] = mapped_column(String(36), ForeignKey("timelines.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)


class TimelineItem(IdMixin, ScopeMixin, Base):
    __tablename__ = "timeline_items"
    timeline_id: Mapped[str] = mapped_column(String(36), ForeignKey("timelines.id"), nullable=False)
    track_id: Mapped[str] = mapped_column(String(36), ForeignKey("timeline_tracks.id"), nullable=False)
    shot_id: Mapped[str | None] = mapped_column(String(36))
    asset_id: Mapped[str | None] = mapped_column(String(36))
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)


class UsageRecord(IdMixin, ScopeMixin, Base):
    __tablename__ = "usage_records"
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("generation_jobs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    token_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    video_seconds: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    estimated_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)
    actual_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0, nullable=False)


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    workspace_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36))
    resource_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    permission_result: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[str] = mapped_column(String(30), nullable=False)
    agent_run_id: Mapped[str | None] = mapped_column(String(36))
    trace_id: Mapped[str] = mapped_column(String(36), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)


class OutboxEvent(IdMixin, ScopeMixin, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("deduplication_key"),)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(160), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
