import hashlib
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeDocument, KnowledgeVersion, StorySource
from app.providers.embeddings import selected_embedding_provider


def collection_name(provider) -> str:
    digest = hashlib.sha256(provider.model.encode()).hexdigest()[:10]
    return f"frameforge_knowledge_{provider.dimensions}_{digest}"


def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def ensure_collection(q: QdrantClient, name: str, dimensions: int) -> None:
    if not q.collection_exists(name):
        q.create_collection(name, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))
    for field in ("workspace_id", "project_id", "document_id", "version_id", "source_type", "source_id", "status"):
        try:
            q.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD, wait=True)
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise


def chunks(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size - overlap) if text[i:i + size].strip()]


def index_story(db: Session, story: StorySource) -> KnowledgeDocument:
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == story.workspace_id, KnowledgeDocument.project_id == story.project_id, KnowledgeDocument.source_type == "story", KnowledgeDocument.source_id == story.id))
    if document is None:
        document = KnowledgeDocument(workspace_id=story.workspace_id, project_id=story.project_id, source_type="story", source_id=story.id, status="ACTIVE")
        db.add(document)
        db.flush()
        version = KnowledgeVersion(workspace_id=story.workspace_id, project_id=story.project_id, document_id=document.id, version_no=1, content_hash=hashlib.sha256(story.content.encode()).hexdigest(), status="ACTIVE")
        db.add(version)
        db.flush()
        document.current_version_id = version.id
    else:
        version = db.get(KnowledgeVersion, document.current_version_id)
        if version is None or version.content_hash != hashlib.sha256(story.content.encode()).hexdigest():
            raise ValueError("Story content differs from its active knowledge version")
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    q.upsert(name, points=[models.PointStruct(id=str(uuid5(NAMESPACE_URL, f"{name}:{version.id}:{index}")), vector=provider.embed(part), payload={"workspace_id": story.workspace_id, "project_id": story.project_id, "document_id": document.id, "version_id": version.id, "source_type": "story", "source_id": story.id, "status": "ACTIVE", "text": part}) for index, part in enumerate(chunks(story.content))], wait=True)
    return document


def retrieve(*, workspace_id: str, project_id: str, query: str, limit: int = 5) -> list[dict]:
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    scoped_filter = models.Filter(must=[models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)), models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)), models.FieldCondition(key="status", match=models.MatchValue(value="ACTIVE"))])
    result = q.query_points(collection_name=name, query=provider.embed(query), query_filter=scoped_filter, limit=min(limit, 20), with_payload=True)
    return [{"document_id": p.payload["document_id"], "version_id": p.payload["version_id"], "source_type": p.payload["source_type"], "source_id": p.payload["source_id"], "text": p.payload["text"], "score": p.score} for p in result.points]
