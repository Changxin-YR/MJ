import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeDocument, KnowledgeVersion, StorySource
from app.providers.embeddings import lexical_tokens, selected_embedding_provider

DIALOGUE_START_RE = re.compile(r'^\s*(?:[“「『"‘]|[\u4e00-\u9fffA-Za-z0-9_·]{1,12}[：:])')
SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？!?；;])')
DIALOGUE_QUERY_CUES = ("说", "问", "回答", "答道", "对白", "对话", "谁说", "这句话", "接着", "随后", "然后呢")


def collection_name(provider) -> str:
    digest = hashlib.sha256(provider.model.encode()).hexdigest()[:10]
    return f"frameforge_knowledge_{provider.dimensions}_{digest}"


def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def ensure_collection(q: QdrantClient, name: str, dimensions: int) -> None:
    if not q.collection_exists(name):
        q.create_collection(name, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))
    keyword_fields = ("workspace_id", "project_id", "document_id", "version_id", "source_type", "source_id", "status", "chunk_kind")
    for field in keyword_fields:
        try:
            q.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD, wait=True)
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise
    try:
        q.create_payload_index(name, "chunk_index", models.PayloadSchemaType.INTEGER, wait=True)
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise


def _split_cn_units(text: str, max_unit_chars: int = 360) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    units: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if len(line) <= max_unit_chars and DIALOGUE_START_RE.match(line):
            units.append(line)
            continue
        parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(line) if part.strip()] or [line]
        for part in parts:
            if len(part) <= max_unit_chars:
                units.append(part)
                continue
            for start in range(0, len(part), max_unit_chars):
                fragment = part[start:start + max_unit_chars].strip()
                if fragment:
                    units.append(fragment)
    return units


def _is_dialogue(unit: str) -> bool:
    stripped = unit.strip()
    return bool(
        DIALOGUE_START_RE.match(stripped)
        or ("“" in stripped and "”" in stripped)
        or ("「" in stripped and "」" in stripped)
        or ("『" in stripped and "』" in stripped)
    )


def _pack_units(
    units: list[str],
    *,
    start: int,
    end: int,
    target_chars: int,
    overlap_units: int,
    chunk_kind: str,
) -> list[dict]:
    records: list[dict] = []
    cursor = start
    while cursor < end:
        chunk_start = cursor
        chunk_end = cursor
        size = 0
        while chunk_end < end:
            addition = len(units[chunk_end]) + (1 if chunk_end > chunk_start else 0)
            if chunk_end > chunk_start and size + addition > target_chars:
                break
            size += addition
            chunk_end += 1
        if chunk_end == chunk_start:
            chunk_end += 1
        core_text = "\n".join(units[chunk_start:chunk_end]).strip()
        if core_text:
            records.append(
                {
                    "chunk_kind": chunk_kind,
                    "unit_start": chunk_start,
                    "unit_end": chunk_end - 1,
                    "core_text": core_text,
                }
            )
        if chunk_end >= end:
            break
        cursor = max(chunk_start + 1, chunk_end - overlap_units)
    return records


def build_chunk_records(text: str) -> list[dict]:
    units = _split_cn_units(text)
    if not units:
        return []

    records = _pack_units(
        units,
        start=0,
        end=len(units),
        target_chars=680,
        overlap_units=2,
        chunk_kind="semantic",
    )

    cursor = 0
    while cursor < len(units):
        if not _is_dialogue(units[cursor]):
            cursor += 1
            continue
        dialogue_start = cursor
        run_start = cursor
        if cursor > 0 and not _is_dialogue(units[cursor - 1]) and len(units[cursor - 1]) <= 180:
            run_start = cursor - 1
        run_end = cursor
        dialogue_count = 0
        while run_end < len(units):
            if _is_dialogue(units[run_end]):
                dialogue_count += 1
                run_end += 1
                continue
            if run_end + 1 < len(units) and _is_dialogue(units[run_end + 1]) and len(units[run_end]) <= 180:
                run_end += 1
                continue
            break
        if dialogue_count >= 2:
            records.extend(
                _pack_units(
                    units,
                    start=run_start,
                    end=run_end,
                    target_chars=520,
                    overlap_units=3,
                    chunk_kind="dialogue",
                )
            )
        cursor = max(run_end, dialogue_start + 1)

    deduplicated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record["chunk_kind"], record["core_text"])
        if key in seen:
            continue
        seen.add(key)
        context_radius = 2 if record["chunk_kind"] == "dialogue" else 1
        context_start = max(0, record["unit_start"] - context_radius)
        context_end = min(len(units), record["unit_end"] + context_radius + 1)
        context_text = "\n".join(units[context_start:context_end]).strip()
        dialogue_units = sum(_is_dialogue(unit) for unit in units[record["unit_start"]:record["unit_end"] + 1])
        total_units = max(1, record["unit_end"] - record["unit_start"] + 1)
        deduplicated.append(
            {
                **record,
                "text": context_text,
                "embedding_text": context_text,
                "dialogue_ratio": dialogue_units / total_units,
            }
        )
    for index, record in enumerate(deduplicated):
        record["chunk_index"] = index
    return deduplicated


def chunks(text: str, size: int = 680, overlap: int = 2) -> list[str]:
    # Backwards-compatible helper; production indexing uses build_chunk_records.
    units = _split_cn_units(text)
    return [
        record["core_text"]
        for record in _pack_units(
            units,
            start=0,
            end=len(units),
            target_chars=size,
            overlap_units=max(0, overlap),
            chunk_kind="semantic",
        )
    ]


def _version_filter(version_id: str) -> models.Filter:
    return models.Filter(
        must=[models.FieldCondition(key="version_id", match=models.MatchValue(value=version_id))]
    )


def index_story(db: Session, story: StorySource) -> KnowledgeDocument:
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.workspace_id == story.workspace_id,
            KnowledgeDocument.project_id == story.project_id,
            KnowledgeDocument.source_type == "story",
            KnowledgeDocument.source_id == story.id,
        )
    )
    if document is None:
        document = KnowledgeDocument(
            workspace_id=story.workspace_id,
            project_id=story.project_id,
            source_type="story",
            source_id=story.id,
            status="ACTIVE",
        )
        db.add(document)
        db.flush()
        version = KnowledgeVersion(
            workspace_id=story.workspace_id,
            project_id=story.project_id,
            document_id=document.id,
            version_no=1,
            content_hash=hashlib.sha256(story.content.encode()).hexdigest(),
            status="ACTIVE",
        )
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
    records = build_chunk_records(story.content)

    points = [
        models.PointStruct(
            id=str(uuid5(NAMESPACE_URL, f"{name}:{version.id}:{record['chunk_kind']}:{record['chunk_index']}")),
            vector=provider.embed(record["embedding_text"]),
            payload={
                "workspace_id": story.workspace_id,
                "project_id": story.project_id,
                "document_id": document.id,
                "version_id": version.id,
                "source_type": "story",
                "source_id": story.id,
                "status": "ACTIVE",
                "chunk_kind": record["chunk_kind"],
                "chunk_index": record["chunk_index"],
                "unit_start": record["unit_start"],
                "unit_end": record["unit_end"],
                "dialogue_ratio": record["dialogue_ratio"],
                "core_text": record["core_text"],
                "text": record["text"],
            },
        )
        for record in records
    ]
    existing, _ = q.scroll(
        collection_name=name,
        scroll_filter=_version_filter(version.id),
        limit=10_000,
        with_payload=False,
        with_vectors=False,
    )
    existing_ids = {point.id for point in existing}
    new_ids = {point.id for point in points}
    if points:
        q.upsert(name, points=points, wait=True)
    stale_ids = list(existing_ids - new_ids)
    if stale_ids:
        q.delete(
            collection_name=name,
            points_selector=models.PointIdsList(points=stale_ids),
            wait=True,
        )
    return document


def _lexical_overlap(query: str, text: str) -> float:
    query_tokens = set(lexical_tokens(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(lexical_tokens(text))
    return len(query_tokens & text_tokens) / len(query_tokens)


def retrieve(*, workspace_id: str, project_id: str, query: str, limit: int = 5) -> list[dict]:
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    scoped_filter = models.Filter(
        must=[
            models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)),
            models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)),
            models.FieldCondition(key="status", match=models.MatchValue(value="ACTIVE")),
        ]
    )
    candidate_limit = min(max(limit * 4, 12), 40)
    result = q.query_points(
        collection_name=name,
        query=provider.embed(query),
        query_filter=scoped_filter,
        limit=candidate_limit,
        with_payload=True,
    )
    dialogue_query = any(cue in query for cue in DIALOGUE_QUERY_CUES)
    query_compact = re.sub(r"\s+", "", query)

    ranked: list[dict] = []
    for point in result.points:
        payload = point.payload or {}
        context_text = str(payload.get("text") or payload.get("core_text") or "")
        core_text = str(payload.get("core_text") or context_text)
        source_id = str(payload.get("source_id") or "")
        key = (source_id, core_text)
        if not context_text:
            continue
        lexical = _lexical_overlap(query, context_text)
        exact_boost = 0.12 if len(query_compact) >= 2 and query_compact in re.sub(r"\s+", "", context_text) else 0.0
        dialogue_boost = 0.06 if dialogue_query and payload.get("chunk_kind") == "dialogue" else 0.0
        rerank_score = float(point.score) + 0.10 * lexical + exact_boost + dialogue_boost
        ranked.append(
            {
                "document_id": payload["document_id"],
                "version_id": payload["version_id"],
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "text": context_text,
                "score": min(1.0, rerank_score),
                "vector_score": float(point.score),
                "chunk_kind": payload.get("chunk_kind", "legacy"),
                "chunk_index": payload.get("chunk_index"),
                "_dedupe_key": key,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)
    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in ranked:
        key = item.pop("_dedupe_key")
        if key in seen:
            continue
        seen.add(key)
        selected.append(item)
        if len(selected) >= min(limit, 20):
            break
    return selected
