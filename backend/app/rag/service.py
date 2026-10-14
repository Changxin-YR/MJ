import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
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


def _paragraphs(text: str) -> list[str]:
    """Normalize pasted prose while retaining paragraph boundaries."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n\s*\n+", normalized) if part.strip()]


def _split_long_paragraph(paragraph: str, size: int) -> list[str]:
    if len(paragraph) <= size:
        return [paragraph]
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", paragraph) if item.strip()]
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > size:
            pieces.append(current)
            current = ""
        current += sentence
    if current:
        pieces.append(current)
    # A paragraph without sentence punctuation still needs a bounded fallback.
    if not pieces or any(len(piece) > size for piece in pieces):
        pieces = [paragraph[i:i + size] for i in range(0, len(paragraph), size)]
    return pieces


def semantic_chunks(text: str, size: int = 1400, overlap: int = 180) -> list[str]:
    """Pack whole paragraphs first, then whole sentences as a last resort.

    The old implementation cut every N characters, often separating a setup
    paragraph from its dialogue. This keeps narrative units intact and uses a
    small sentence overlap only at a hard size boundary.
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("size must be positive and overlap must be smaller than size")
    units = [piece for paragraph in _paragraphs(text) for piece in _split_long_paragraph(paragraph, size)]
    result: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if current and len(candidate) > size:
            result.append(current)
            tail_sentences = [item.strip() for item in re.split(r"(?<=[。！？!?；;])", current) if item.strip()]
            tail = ""
            for sentence in reversed(tail_sentences):
                if len(sentence) + len(tail) > overlap:
                    break
                tail = f"{sentence} {tail}".strip()
            current = f"{tail}\n\n{unit}" if tail else unit
        else:
            current = candidate
    if current:
        result.append(current)
    return result


# Kept as a compatibility alias for callers and scripts using the old name.
chunks = semantic_chunks


def index_story(db: Session, story: StorySource) -> KnowledgeDocument:
    # Serialize reindexing even when the KnowledgeDocument does not exist yet.
    db.scalar(select(StorySource).where(StorySource.id == story.id).with_for_update())
    document = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.workspace_id == story.workspace_id, KnowledgeDocument.project_id == story.project_id, KnowledgeDocument.source_type == "story", KnowledgeDocument.source_id == story.id).with_for_update())
    if document is None:
        document = KnowledgeDocument(workspace_id=story.workspace_id, project_id=story.project_id, source_type="story", source_id=story.id, status="ACTIVE")
        db.add(document)
        db.flush()
    previous = db.get(KnowledgeVersion, document.current_version_id) if document.current_version_id else None
    version_no = (db.scalar(select(func.max(KnowledgeVersion.version_no)).where(KnowledgeVersion.document_id == document.id)) or 0) + 1
    version = KnowledgeVersion(workspace_id=story.workspace_id, project_id=story.project_id, document_id=document.id, version_no=version_no, content_hash=hashlib.sha256(story.content.encode()).hexdigest(), status="ACTIVE")
    db.add(version)
    db.flush()
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    parts = semantic_chunks(story.content)
    points = [models.PointStruct(id=str(uuid5(NAMESPACE_URL, f"{name}:{version.id}:{index}")), vector=provider.embed(part), payload={"workspace_id": story.workspace_id, "project_id": story.project_id, "document_id": document.id, "version_id": version.id, "source_type": "story", "source_id": story.id, "status": "ACTIVE", "chunk_index": index, "chunk_count": len(parts), "text": part}) for index, part in enumerate(parts)]
    q.upsert(name, points=points, wait=True)
    # The caller's SQL commit is the visibility switch. Previous points stay
    # available on any embedding, upload or transaction failure.
    if previous:
        previous.status = "SUPERSEDED"
    document.current_version_id = version.id
    return document


def retrieve(*, workspace_id: str, project_id: str, query: str, limit: int = 5) -> list[dict]:
    with SessionLocal() as db:
        versions = list(db.scalars(select(KnowledgeDocument.current_version_id).where(KnowledgeDocument.workspace_id == workspace_id, KnowledgeDocument.project_id == project_id, KnowledgeDocument.status == "ACTIVE", KnowledgeDocument.current_version_id.is_not(None))))
    if not versions:
        return []
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    scoped_filter = models.Filter(must=[models.FieldCondition(key="version_id", match=models.MatchAny(any=versions)), models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)), models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)), models.FieldCondition(key="status", match=models.MatchValue(value="ACTIVE"))])
    result = q.query_points(collection_name=name, query=provider.embed(query), query_filter=scoped_filter, limit=min(limit, 20), with_payload=True)
    return [{"document_id": p.payload["document_id"], "version_id": p.payload["version_id"], "source_type": p.payload["source_type"], "source_id": p.payload["source_id"], "text": p.payload["text"], "score": p.score} for p in result.points]
