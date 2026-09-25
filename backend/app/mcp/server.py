"""Local stdio MCP adapter. Every request is bound to a live user session."""

import json
import os

import jwt
from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

from app.agent.gateway import gateway
from app.api.errors import APIError
from app.auth.security import decode_access
from app.db import SessionLocal
from app.models import AgentRun, ServerSession, User, now

mcp = FastMCP("frameforge_mcp")


def invoke_bound(run_id: str, tool_name: str, arguments: dict):
    token = os.environ.get("FRAMEFORGE_MCP_ACCESS_TOKEN", "")
    if not token:
        raise APIError("AUTH_REQUIRED", "MCP access token is required", 401)
    try:
        claims = decode_access(token)
    except jwt.PyJWTError:
        raise APIError("AUTH_REQUIRED", "Invalid MCP access token", 401) from None
    with SessionLocal() as db:
        run = db.scalar(select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == claims["sub"], AgentRun.session_id == claims["sid"]))
        session = db.get(ServerSession, claims["sid"])
        user = db.get(User, claims["sub"])
        if not run or not session or session.user_id != claims["sub"] or session.revoked_at or session.expires_at <= now() or not user or user.disabled:
            raise APIError("PERMISSION_DENIED", "MCP run is not bound to this session", 403)
    return gateway.invoke(run_id, tool_name, arguments)


@mcp.tool(name="frameforge_load_project_context", annotations={"readOnlyHint": True, "idempotentHint": True})
def load_project_context(run_id: str) -> str:
    """Read the structured context of the Director run's project."""
    return json.dumps(invoke_bound(run_id, "load_project_context", {}), ensure_ascii=False)


@mcp.tool(name="frameforge_retrieve_semantic_context", annotations={"readOnlyHint": True, "idempotentHint": True})
def retrieve_semantic_context(run_id: str, query: str) -> str:
    """Search active knowledge scoped to the Director run's project."""
    if not 1 <= len(query) <= 1000:
        raise ValueError("Query must contain 1-1000 characters")
    return json.dumps(invoke_bound(run_id, "retrieve_semantic_context", {"query": query}), ensure_ascii=False)


@mcp.tool(name="frameforge_load_generation_job", annotations={"readOnlyHint": True, "idempotentHint": True})
def load_generation_job(run_id: str, job_id: str) -> str:
    """Read one generation job scoped to the Director run's project."""
    return json.dumps(invoke_bound(run_id, "load_generation_job", {"job_id": job_id}), ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
