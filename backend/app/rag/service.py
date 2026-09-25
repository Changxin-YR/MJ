import hashlib
import re
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Character, KnowledgeDocument, KnowledgeVersion, StorySource
from app.providers.embeddings import lexical_tokens, selected_embedding_provider

DIALOGUE_START_RE = re.compile(r'^\s*(?:[“「『"‘]|[\u4e00-\u9fffA-Za-z0-9_·]{1,12}[：:])')
SUBJECTLESS_DIALOGUE_RE = re.compile(r'^\s*[“「『"‘]')
EXPLICIT_SPEAKER_RE = re.compile(
    r'^\s*([\u4e00-\u9fffA-Za-z0-9_·]{1,12}?)(?:低声|沉声|轻声|冷声|笑着|笑|忽然|缓缓)?'
    r'(?:说道|问道|答道|喊道|叫道|应道|回道|开口道|说|问|答|道|开口)[，,:：]?\s*[“「『"‘]'
)
SENTENCE_SPLIT_RE = re.compile(r'(?<=[。！？!?；;])')
DIALOGUE_QUERY_CUES = (
    "说", "问", "回答", "答道", "对白", "对话", "谁说", "谁问", "这句话", "这句",
    "上一句", "下一句", "对方", "回应", "回复", "怎么回", "怎么答",
    "他说", "她说", "他问", "她问",
)
DIALOGUE_CONTEXT_CUES = ("为什么", "接着", "随后", "然后呢")
NON_SPEAKER_HINTS = {"他", "她", "它", "对方", "那人", "有人", "众人", "两人"}
MAX_RETRIEVAL_CONTEXT_CHARS = 1800


def collection_name(provider) -> str:
    digest = hashlib.sha256(provider.model.encode()).hexdigest()[:10]
    return f"frameforge_knowledge_{provider.dimensions}_{digest}"


def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def ensure_collection(q: QdrantClient, name: str, dimensions: int) -> None:
    if not q.collection_exists(name):
        q.create_collection(name, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))
    keyword_fields = ("workspace_id", "project_id", "document_id", "version_id", "source_type", "source_id", "status", "chunk_kind", "dialogue_run_id")
    for field in keyword_fields:
        try:
            q.create_payload_index(name, field, models.PayloadSchemaType.KEYWORD, wait=True)
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise
    for field in ("chunk_index", "unit_start", "unit_end"):
        try:
            q.create_payload_index(name, field, models.PayloadSchemaType.INTEGER, wait=True)
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


def _explicit_speaker(unit: str, known_speakers: list[str] | None = None) -> str | None:
    stripped = unit.strip()
    match = EXPLICIT_SPEAKER_RE.match(stripped)
    if match:
        speaker = match.group(1).strip()
        if speaker not in NON_SPEAKER_HINTS:
            return speaker
    if known_speakers:
        quote_positions = [stripped.find(mark) for mark in ("“", "「", "『", '"', "‘") if stripped.find(mark) >= 0]
        quote_position = min(quote_positions) if quote_positions else -1
        if quote_position > 0:
            prefix = stripped[: quote_position + 1]
            for speaker in sorted({name.strip() for name in known_speakers if name.strip()}, key=len, reverse=True):
                if stripped.startswith(speaker) and ("：" in prefix or ":" in prefix):
                    return speaker
    return None


def _speaker_hints(units: list[str], known_speakers: list[str] | None = None) -> list[str]:
    hints: list[str] = []
    for unit in units:
        speaker = _explicit_speaker(unit, known_speakers)
        if speaker and speaker not in hints:
            hints.append(speaker)
    return hints


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


def build_chunk_records(text: str, known_speakers: list[str] | None = None) -> list[dict]:
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
            dialogue_records = _pack_units(
                units,
                start=run_start,
                end=run_end,
                target_chars=420,
                overlap_units=6,
                chunk_kind="dialogue",
            )
            anchor_end = min(run_end, run_start + 3)
            anchor_text = "\n".join(units[run_start:anchor_end]).strip()
            for record in dialogue_records:
                record["dialogue_anchor_text"] = anchor_text
                record["dialogue_run_start"] = run_start
                record["dialogue_run_end"] = run_end - 1
                record["dialogue_run_id"] = f"{run_start}:{run_end - 1}"
            records.extend(dialogue_records)
        cursor = max(run_end, dialogue_start + 1)

    deduplicated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record["chunk_kind"], record["core_text"])
        if key in seen:
            continue
        seen.add(key)
        context_radius = 3 if record["chunk_kind"] == "dialogue" else 1
        context_start = max(0, record["unit_start"] - context_radius)
        context_end = min(len(units), record["unit_end"] + context_radius + 1)
        context_units = units[context_start:context_end]
        context_text = "\n".join(context_units).strip()
        anchor_text = str(record.get("dialogue_anchor_text") or "").strip()
        if anchor_text and anchor_text not in context_text:
            context_text = f"{anchor_text}\n{context_text}".strip()
        dialogue_units = sum(_is_dialogue(unit) for unit in units[record["unit_start"]:record["unit_end"] + 1])
        total_units = max(1, record["unit_end"] - record["unit_start"] + 1)
        speaker_hints = _speaker_hints(
            units[max(0, context_start - 2):min(len(units), context_end + 2)]
            + (anchor_text.split("\n") if anchor_text else []),
            known_speakers,
        )
        has_subjectless_dialogue = any(
            SUBJECTLESS_DIALOGUE_RE.match(unit.strip())
            for unit in units[record["unit_start"]:record["unit_end"] + 1]
        )
        metadata_lines = []
        if record["chunk_kind"] == "dialogue":
            metadata_lines.append("[中文连续对白上下文]")
        if speaker_hints:
            metadata_lines.append(f"显式说话人线索：{'、'.join(speaker_hints)}")
        if has_subjectless_dialogue:
            metadata_lines.append("包含省略主语的连续对白，需结合前后轮次判断")
        embedding_text = "\n".join([*metadata_lines, context_text]).strip()
        deduplicated.append(
            {
                **record,
                "text": context_text,
                "embedding_text": embedding_text,
                "dialogue_ratio": dialogue_units / total_units,
                "dialogue_turn_count": dialogue_units,
                "speaker_hints": speaker_hints,
                "has_subjectless_dialogue": has_subjectless_dialogue,
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
    known_speakers = list(
        db.scalars(
            select(Character.name).where(
                Character.workspace_id == story.workspace_id,
                Character.project_id == story.project_id,
            )
        ).all()
    )
    records = build_chunk_records(story.content, known_speakers=known_speakers)

    vectors: list[list[float]] = []
    batch_size = max(1, int(getattr(provider, "batch_size", 1)))
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        texts = [record["embedding_text"] for record in batch]
        if hasattr(provider, "embed_many"):
            vectors.extend(provider.embed_many(texts))
        else:
            vectors.extend(provider.embed(text) for text in texts)
    if len(vectors) != len(records):
        raise ValueError("Embedding provider returned an incomplete index batch")
    points = [
        models.PointStruct(
            id=str(uuid5(NAMESPACE_URL, f"{name}:{version.id}:{record['chunk_kind']}:{record['chunk_index']}")),
            vector=vector,
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
                "dialogue_turn_count": record["dialogue_turn_count"],
                "speaker_hints": record["speaker_hints"],
                "has_subjectless_dialogue": record["has_subjectless_dialogue"],
                "dialogue_anchor_text": record.get("dialogue_anchor_text", ""),
                "dialogue_run_start": record.get("dialogue_run_start"),
                "dialogue_run_end": record.get("dialogue_run_end"),
                "dialogue_run_id": record.get("dialogue_run_id", ""),
                "core_text": record["core_text"],
                "text": record["text"],
            },
        )
        for record, vector in zip(records, vectors, strict=True)
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


def _is_dialogue_query(query: str) -> bool:
    if any(cue in query for cue in DIALOGUE_QUERY_CUES):
        return True
    has_quote = any(mark in query for mark in ("“", "”", "「", "」", "『", "』", '"'))
    return has_quote and any(cue in query for cue in DIALOGUE_CONTEXT_CUES)


def _embed_queries(provider, queries: list[str]) -> list[list[float]]:
    if hasattr(provider, "embed_many"):
        return provider.embed_many(queries)
    return [provider.embed(query) for query in queries]


def _center_clip(text: str, core_text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    needle = core_text[: min(len(core_text), 160)]
    position = text.find(needle) if needle else -1
    if position < 0:
        return text[:max_chars]
    center = position + min(len(core_text), max_chars) // 2
    start = max(0, center - max_chars // 2)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    if start > 0:
        newline = text.find("\n", start, min(end, start + 160))
        if newline >= 0:
            start = newline + 1
    if end < len(text):
        newline = text.rfind("\n", max(start, end - 160), end)
        if newline > start:
            end = newline
    return text[start:end].strip()


def _expand_neighbor_context(q: QdrantClient, name: str, payload: dict) -> tuple[str, bool]:
    base_text = str(payload.get("text") or payload.get("core_text") or "")
    if payload.get("unit_start") is None or payload.get("unit_end") is None:
        return base_text, False
    radius = 5 if payload.get("chunk_kind") == "dialogue" else 2
    current_start = int(payload["unit_start"])
    current_end = int(payload["unit_end"])
    filters = [
        models.FieldCondition(key="workspace_id", match=models.MatchValue(value=payload["workspace_id"])),
        models.FieldCondition(key="project_id", match=models.MatchValue(value=payload["project_id"])),
        models.FieldCondition(key="document_id", match=models.MatchValue(value=payload["document_id"])),
        models.FieldCondition(key="version_id", match=models.MatchValue(value=payload["version_id"])),
        models.FieldCondition(key="chunk_kind", match=models.MatchValue(value=payload.get("chunk_kind", "semantic"))),
        models.FieldCondition(key="unit_start", range=models.Range(lte=current_end + radius)),
        models.FieldCondition(key="unit_end", range=models.Range(gte=max(0, current_start - radius))),
    ]
    dialogue_run_id = str(payload.get("dialogue_run_id") or "")
    if payload.get("chunk_kind") == "dialogue" and dialogue_run_id:
        filters.append(
            models.FieldCondition(
                key="dialogue_run_id",
                match=models.MatchValue(value=dialogue_run_id),
            )
        )
    neighbors, _ = q.scroll(
        collection_name=name,
        scroll_filter=models.Filter(must=filters),
        limit=16,
        with_payload=True,
        with_vectors=False,
    )
    if not neighbors:
        return base_text, False

    unit_map: dict[int, str] = {}
    for neighbor in sorted(
        neighbors,
        key=lambda point: (
            int((point.payload or {}).get("unit_start", 0)),
            int((point.payload or {}).get("unit_end", 0)),
        ),
    ):
        neighbor_payload = neighbor.payload or {}
        start = neighbor_payload.get("unit_start")
        end = neighbor_payload.get("unit_end")
        core = str(neighbor_payload.get("core_text") or "")
        if start is None or end is None or not core:
            continue
        lines = core.split("\n")
        expected = int(end) - int(start) + 1
        if len(lines) != expected:
            continue
        for offset, line in enumerate(lines):
            unit_map.setdefault(int(start) + offset, line)

    merged_core = "\n".join(unit_map[index] for index in sorted(unit_map)).strip()
    if not merged_core:
        return base_text, False

    anchor = str(payload.get("dialogue_anchor_text") or "").strip()
    if anchor and anchor not in merged_core:
        available = max(400, MAX_RETRIEVAL_CONTEXT_CHARS - len(anchor) - 1)
        merged_core = _center_clip(merged_core, str(payload.get("core_text") or ""), available)
        expanded = f"{anchor}\n{merged_core}".strip()
    else:
        expanded = _center_clip(
            merged_core,
            str(payload.get("core_text") or ""),
            MAX_RETRIEVAL_CONTEXT_CHARS,
        )
    return expanded, len(neighbors) > 1 or expanded != base_text


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
    dialogue_query = _is_dialogue_query(query)
    query_texts = [query]
    if dialogue_query:
        query_texts.append(f"{query}\n中文连续对白 说话人 回答 回应 前后文 省略主语")
    query_vectors = _embed_queries(provider, query_texts)
    candidate_limit = min(max(limit * (6 if dialogue_query else 4), 12), 48)

    candidates: dict[str, object] = {}
    for vector in query_vectors:
        result = q.query_points(
            collection_name=name,
            query=vector,
            query_filter=scoped_filter,
            limit=candidate_limit,
            with_payload=True,
        )
        for point in result.points:
            key = str(point.id)
            existing = candidates.get(key)
            if existing is None or float(point.score) > float(existing.score):
                candidates[key] = point

    query_compact = re.sub(r"\s+", "", query)
    ranked: list[dict] = []
    for point in candidates.values():
        payload = point.payload or {}
        context_text = str(payload.get("text") or payload.get("core_text") or "")
        core_text = str(payload.get("core_text") or context_text)
        if not context_text:
            continue
        lexical = _lexical_overlap(query, context_text)
        exact_boost = 0.12 if len(query_compact) >= 2 and query_compact in re.sub(r"\s+", "", context_text) else 0.0
        dialogue_boost = 0.08 if dialogue_query and payload.get("chunk_kind") == "dialogue" else 0.0
        subjectless_boost = 0.03 if dialogue_query and payload.get("has_subjectless_dialogue") else 0.0
        speaker_hints = [str(item) for item in (payload.get("speaker_hints") or [])]
        speaker_boost = 0.08 if dialogue_query and any(speaker and speaker in query for speaker in speaker_hints) else 0.0
        rerank_score = float(point.score) + 0.12 * lexical + exact_boost + dialogue_boost + subjectless_boost + speaker_boost
        ranked.append(
            {
                "document_id": payload["document_id"],
                "version_id": payload["version_id"],
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "score": min(1.0, rerank_score),
                "vector_score": float(point.score),
                "chunk_kind": payload.get("chunk_kind", "legacy"),
                "chunk_index": payload.get("chunk_index"),
                "speaker_hints": speaker_hints,
                "has_subjectless_dialogue": bool(payload.get("has_subjectless_dialogue")),
                "_payload": payload,
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)

    selected: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in ranked:
        payload = item.pop("_payload")
        context_text, context_expanded = _expand_neighbor_context(q, name, payload)
        key = (str(item["source_id"]), context_text)
        if key in seen:
            continue
        seen.add(key)
        item["text"] = context_text
        item["context_expanded"] = context_expanded
        selected.append(item)
        if len(selected) >= min(limit, 20):
            break
    return selected
