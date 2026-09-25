from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import APIError, ok, trace_id
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
        raise APIError("RESOURCE_NOT_FOUND", "Story not found", 404)
    document = index_story(db, story)
    record(db, actor_type="USER", actor_id=scope.user_id, action="knowledge.index", resource_type="knowledge_document", resource_id=document.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, {"document_id": document.id, "version_id": document.current_version_id})


@router.post("/reindex-stories")
def reindex_stories(
    request: Request,
    scope: ProjectScope = Depends(project_scope),
    db: Session = Depends(get_db),
):
    require(scope, "story.edit")
    story_ids = db.scalars(
        select(StorySource.id)
        .where(
            StorySource.workspace_id == scope.workspace_id,
            StorySource.project_id == scope.project_id,
            StorySource.status == "ACTIVE",
        )
        .order_by(StorySource.created_at)
    ).all()
    indexed: list[str] = []
    failed: list[dict] = []
    for story_id in story_ids:
        try:
            story = db.scalar(
                select(StorySource)
                .where(
                    StorySource.id == story_id,
                    StorySource.workspace_id == scope.workspace_id,
                    StorySource.project_id == scope.project_id,
                )
                .with_for_update()
            )
            if not story:
                continue
            document = index_story(db, story)
            record(
                db,
                actor_type="USER",
                actor_id=scope.user_id,
                action="knowledge.reindex",
                resource_type="knowledge_document",
                resource_id=document.id,
                workspace_id=scope.workspace_id,
                project_id=scope.project_id,
                trace_id=trace_id(request),
                safe_summary=f"story:{story.id}",
            )
            db.commit()
            indexed.append(story.id)
        except Exception as error:
            db.rollback()
            failed.append({"story_id": story_id, "error": type(error).__name__})
    return ok(
        request,
        {
            "total": len(story_ids),
            "indexed": indexed,
            "failed": failed,
            "embedding_profile_rebuild": True,
        },
    )


@router.post("/search")
def search(payload: Query, request: Request, scope: ProjectScope = Depends(project_scope)):
    require(scope, "project.read")
    return ok(request, retrieve(workspace_id=scope.workspace_id, project_id=scope.project_id, query=payload.query, limit=payload.limit))
