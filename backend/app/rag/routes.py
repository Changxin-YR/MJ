from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require
from app.db import get_db
from app.models import StorySource
from app.rag.service import index_story, retrieve

router = APIRouter(prefix="/projects/{project_id}/knowledge", tags=["knowledge"])


class Query(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/stories/{story_id}/index")
def index(story_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "story.edit")
    story = db.scalar(
        select(StorySource).where(
            StorySource.id == story_id,
            StorySource.workspace_id == scope.workspace_id,
            StorySource.project_id == scope.project_id,
        ).with_for_update()
    )
    if not story:
        from app.api.errors import APIError

        raise APIError("RESOURCE_NOT_FOUND", "Story not found", 404)
    document = index_story(db, story)
    record(db, actor_type="USER", actor_id=scope.user_id, action="knowledge.index", resource_type="knowledge_document", resource_id=document.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"document_id": document.id, "version_id": document.current_version_id})


@router.post("/search")
def search(payload: Query, request: Request, scope: ProjectScope = Depends(project_scope)):
    require(scope, "project.read")
    return ok(request, retrieve(workspace_id=scope.workspace_id, project_id=scope.project_id, query=payload.query, limit=payload.limit))
