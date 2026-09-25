from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import ok, trace_id
from app.audit.service import record
from app.auth.dependencies import ProjectScope, project_scope, require, scoped_get
from app.db import get_db
from app.models import StorySource

router = APIRouter(prefix="/projects/{project_id}/stories", tags=["stories"])


class StoryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=20, max_length=2_000_000)


def data(story: StorySource) -> dict:
    return {"id": story.id, "title": story.title, "content": story.content, "status": story.status, "created_at": story.created_at.isoformat()}


@router.post("")
def create_story(payload: StoryCreate, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "story.edit")
    story = StorySource(workspace_id=scope.workspace_id, project_id=scope.project_id, title=payload.title, content=payload.content, created_by=scope.user_id)
    db.add(story)
    db.flush()
    record(db, actor_type="USER", actor_id=scope.user_id, action="story.create", resource_type="story", resource_id=story.id, workspace_id=scope.workspace_id, project_id=scope.project_id, trace_id=trace_id(request))
    db.commit()
    return ok(request, data(story))


@router.get("")
def list_stories(request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    stories = db.scalars(select(StorySource).where(StorySource.workspace_id == scope.workspace_id, StorySource.project_id == scope.project_id).order_by(StorySource.created_at.desc())).all()
    return ok(request, [data(s) for s in stories])


@router.get("/{story_id}")
def get_story(story_id: str, request: Request, scope: ProjectScope = Depends(project_scope), db: Session = Depends(get_db)):
    require(scope, "project.read")
    return ok(request, data(scoped_get(db, StorySource, story_id, scope)))
