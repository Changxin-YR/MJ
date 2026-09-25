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
SENTENCE_SPLIT_RE = re.compile(
    r'(?<=[。！？!?；;])(?=[^”」』"’]|$)|(?<=[”」』"’])(?=[\u4e00-\u9fffA-Za-z“「『"‘])'
)
DIALOGUE_QUERY_CUES = (
    "说", "问", "回答", "答道", "对白", "对话", "谁说", "这句话", "接着", "随后", "然后呢",
    "他说", "她说", "回应", "回了", "接话", "上一句", "下一句", "后一句", "前一句",
)
SCENE_BOUNDARY_RE = re.compile(r"^(?:第[一二三四五六七八九十百千万\d]+[章节卷回]|[-—=*#]{3,})")
SPEAKER_PREFIX_RE = re.compile(
    r"^\s*([\u4e00-\u9fffA-Za-z0-9_·]{1,8}?)(?:又|再|忽然|继续|低声|轻声|沉声|冷声|笑着)?"
    r"(?:问道|问|说道|说|答道|回答|答|喊道|喊|开口道|开口|反问|淡淡道|道(?=[：:“「『]))"
)
SPEAKER_COLON_RE = re.compile(
    r"^\s*([\u4e00-\u9fff]{1,4}|[A-Za-z0-9_·]{1,12})[：:]"
)
SPEAKER_SUFFIX_RE = re.compile(
    r"[”」』\"]\s*([\u4e00-\u9fffA-Za-z0-9_·]{1,8}?)(?:又|再|低声|轻声|沉声|冷声)?"
    r"(?:问道|问|说道|说|答道|答|喊道|喊|道)"
)
SPEAKER_STOP = {"他", "她", "它", "他们", "她们", "对方", "那人", "此人", "少年", "少女", "男人", "女人", "老人"}
QUERY_STOP_TOKENS = set(
    lexical_tokens(
        "对方怎么回答 对方怎么说 为什么 怎么回事 谁说的 这句话 上一句 下一句 "
        "前一句 后一句 然后呢 接着说 随后说 回答了什么 说了什么 问了什么"
    )
)


def collection_name(provider) -> str:
    identity = f"{provider.model}:{getattr(provider, 'profile', 'legacy')}"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    return f"frameforge_knowledge_{provider.dimensions}_{digest}"


def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, timeout=10)


def ensure_collection(q: QdrantClient, name: str, dimensions: int) -> None:
    if not q.collection_exists(name):
        q.create_collection(name, vectors_config=models.VectorParams(size=dimensions, distance=models.Distance.COSINE))
    keyword_fields = (
        "workspace_id", "project_id", "document_id", "version_id", "source_type", "source_id",
        "status", "chunk_kind", "lexical_tokens", "speaker_hints",
    )
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
        if len(line) <= max_unit_chars and _is_dialogue(line):
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

def _speaker_hints(text: str) -> list[str]:
    hints: list[str] = []
    for line in text.splitlines():
        for pattern in (SPEAKER_PREFIX_RE, SPEAKER_SUFFIX_RE, SPEAKER_COLON_RE):
            match = pattern.search(line)
            if not match:
                continue
            speaker = match.group(1).strip()
            if speaker and speaker not in SPEAKER_STOP and speaker not in hints:
                hints.append(speaker)
            break
    return hints[:12]


def _speaker_anchor_lines(units: list[str], start: int, end: int, focus: int, limit: int = 6) -> list[str]:
    candidates: list[tuple[int, int, str]] = []
    for index in range(max(0, start), min(len(units), end)):
        line = units[index]
        if not _speaker_hints(line):
            continue
        candidates.append((abs(index - focus), index, line))
    nearest = sorted(candidates)[:limit]
    return [line for _, _, line in sorted(nearest, key=lambda item: item[1])]


def _window_text(units: list[str], start: int, end: int, target_chars: int) -> tuple[int, int, str]:
    left, right = start, end
    size = sum(len(unit) + 1 for unit in units[left:right])
    while size < target_chars and (left > 0 or right < len(units)):
        expanded = False
        if left > 0 and not SCENE_BOUNDARY_RE.match(units[left - 1]):
            left -= 1
            size += len(units[left]) + 1
            expanded = True
        if size >= target_chars:
            break
        if right < len(units) and not SCENE_BOUNDARY_RE.match(units[right]):
            size += len(units[right]) + 1
            right += 1
            expanded = True
        if not expanded:
            break
    return left, right, "\n".join(units[left:right]).strip()


def _payload_tokens(text: str, limit: int = 512) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    # 3-grams first give Chinese names/phrases more discriminative lexical recall.
    tokens = lexical_tokens(text)
    ordered = sorted(enumerate(tokens), key=lambda item: (-len(item[1]), item[0]))
    for _, token in ordered:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _query_terms(query: str, limit: int = 40) -> list[str]:
    raw_terms = _payload_tokens(query, limit=limit * 2)
    terms = [term for term in raw_terms if term not in QUERY_STOP_TOKENS][:limit]
    # Query entity extraction follows the same conservative explicit-speaker rules
    # as indexed fiction. Generic phrases such as “对方怎么回答” are intent, not names.
    named = _speaker_hints(query)
    for name in reversed(named):
        if name not in terms:
            terms.insert(0, name)
    return terms[:limit]



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
        target_chars=620,
        overlap_units=3,
        chunk_kind="semantic",
    )

    cursor = 0
    while cursor < len(units):
        if not _is_dialogue(units[cursor]):
            cursor += 1
            continue
        dialogue_start = cursor
        run_start = cursor
        if cursor > 0 and not _is_dialogue(units[cursor - 1]) and len(units[cursor - 1]) <= 220 and not SCENE_BOUNDARY_RE.match(units[cursor - 1]):
            run_start = cursor - 1
        run_end = cursor
        dialogue_count = 0
        while run_end < len(units):
            if SCENE_BOUNDARY_RE.match(units[run_end]):
                break
            if _is_dialogue(units[run_end]):
                dialogue_count += 1
                run_end += 1
                continue
            if run_end + 1 < len(units) and _is_dialogue(units[run_end + 1]) and len(units[run_end]) <= 220:
                run_end += 1
                continue
            break
        if dialogue_count >= 2:
            dialogue_records = _pack_units(
                units,
                start=run_start,
                end=run_end,
                target_chars=460,
                overlap_units=5,
                chunk_kind="dialogue",
            )
            for record in dialogue_records:
                record["dialogue_run_start"] = run_start
                record["dialogue_run_end"] = run_end
            records.extend(dialogue_records)
        cursor = max(run_end, dialogue_start + 1)

    deduplicated: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record["chunk_kind"], record["core_text"])
        if key in seen:
            continue
        seen.add(key)
        if record["chunk_kind"] == "dialogue":
            run_start = int(record.get("dialogue_run_start", record["unit_start"]))
            run_end = int(record.get("dialogue_run_end", record["unit_end"] + 1))
            parent_start, parent_end, context_text = _window_text(
                units, record["unit_start"], record["unit_end"] + 1, 1650
            )
            run_text = "\n".join(units[run_start:run_end])
            speaker_hints = _speaker_hints(run_text)
            speaker_anchors = _speaker_anchor_lines(
                units, run_start, run_end, record["unit_start"], limit=6
            )
        else:
            parent_start, parent_end, context_text = _window_text(
                units, record["unit_start"], record["unit_end"] + 1, 1150
            )
            speaker_hints = _speaker_hints(context_text)
            speaker_anchors = _speaker_anchor_lines(
                units, parent_start, parent_end, record["unit_start"], limit=4
            )
        dialogue_units = sum(_is_dialogue(unit) for unit in units[record["unit_start"]:record["unit_end"] + 1])
        total_units = max(1, record["unit_end"] - record["unit_start"] + 1)
        lexical_source = "\n".join([context_text, *speaker_anchors])
        deduplicated.append(
            {
                **record,
                "parent_start": parent_start,
                "parent_end": parent_end - 1,
                "text": context_text,
                "embedding_text": f"{record['core_text']}\n\n上下文：\n{context_text}\n\n明确说话人线索：\n" + "\n".join(speaker_anchors),
                "dialogue_ratio": dialogue_units / total_units,
                "speaker_hints": speaker_hints,
                "speaker_anchors": speaker_anchors,
                "lexical_tokens": _payload_tokens(lexical_source),
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

    vectors: list[list[float]] = []
    batch_size = max(1, int(getattr(provider, "batch_size", 1)))
    for start in range(0, len(records), batch_size):
        batch = records[start:start + batch_size]
        texts = [record["embedding_text"] for record in batch]
        if hasattr(provider, "embed_many"):
            vectors.extend(provider.embed_many(texts, text_type="document"))
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
                "core_text": record["core_text"],
                "text": record["text"],
                "speaker_hints": record["speaker_hints"],
                "speaker_anchors": record["speaker_anchors"],
                "lexical_tokens": record["lexical_tokens"],
                "parent_start": record["parent_start"],
                "parent_end": record["parent_end"],
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


def _lexical_overlap(query_terms: list[str], payload_tokens: list[str]) -> float:
    if not query_terms:
        return 0.0
    payload = set(payload_tokens)
    weights = {term: 1.5 if len(term) >= 3 else 1.0 for term in query_terms}
    matched = sum(weight for term, weight in weights.items() if term in payload)
    total = sum(weights.values()) or 1.0
    return matched / total


def _overlap_ratio(left: dict, right: dict) -> float:
    if left["source_id"] != right["source_id"]:
        return 0.0
    a0, a1 = int(left.get("unit_start") or 0), int(left.get("unit_end") or 0)
    b0, b1 = int(right.get("unit_start") or 0), int(right.get("unit_end") or 0)
    intersection = max(0, min(a1, b1) - max(a0, b0) + 1)
    union = max(a1, b1) - min(a0, b0) + 1
    return intersection / max(1, union)


def retrieve(*, workspace_id: str, project_id: str, query: str, limit: int = 5) -> list[dict]:
    q = client()
    provider = selected_embedding_provider()
    name = collection_name(provider)
    ensure_collection(q, name, provider.dimensions)
    scope_conditions = [
        models.FieldCondition(key="workspace_id", match=models.MatchValue(value=workspace_id)),
        models.FieldCondition(key="project_id", match=models.MatchValue(value=project_id)),
        models.FieldCondition(key="status", match=models.MatchValue(value="ACTIVE")),
    ]
    scoped_filter = models.Filter(must=scope_conditions)
    candidate_limit = min(max(limit * 8, 24), 80)
    dense = q.query_points(
        collection_name=name,
        query=provider.embed(query, text_type="query"),
        query_filter=scoped_filter,
        limit=candidate_limit,
        with_payload=True,
    )

    query_terms = _query_terms(query)
    lexical_points = []
    if query_terms:
        lexical_filter = models.Filter(
            must=[
                *scope_conditions,
                models.FieldCondition(
                    key="lexical_tokens",
                    match=models.MatchAny(any=query_terms),
                ),
            ]
        )
        lexical_points, _ = q.scroll(
            collection_name=name,
            scroll_filter=lexical_filter,
            limit=min(max(limit * 40, 120), 300),
            with_payload=True,
            with_vectors=False,
        )
        # MatchAny is broad; add a few exact-token scans so a rare two/three-character
        # character name or quoted phrase cannot be crowded out by common Chinese n-grams.
        lexical_by_id = {str(point.id): point for point in lexical_points}
        for anchor in query_terms[:6]:
            anchor_filter = models.Filter(
                must=[
                    *scope_conditions,
                    models.FieldCondition(
                        key="lexical_tokens",
                        match=models.MatchValue(value=anchor),
                    ),
                ]
            )
            anchor_points, _ = q.scroll(
                collection_name=name,
                scroll_filter=anchor_filter,
                limit=40,
                with_payload=True,
                with_vectors=False,
            )
            for point in anchor_points:
                lexical_by_id.setdefault(str(point.id), point)
        lexical_points = list(lexical_by_id.values())

    dense_scores = {str(point.id): float(point.score) for point in dense.points}
    candidates: dict[str, object] = {str(point.id): point for point in dense.points}
    for point in lexical_points:
        candidates.setdefault(str(point.id), point)

    dialogue_query = any(cue in query for cue in DIALOGUE_QUERY_CUES)
    query_compact = re.sub(r"\s+", "", query)
    ranked: list[dict] = []
    for point_id, point in candidates.items():
        payload = point.payload or {}
        context_text = str(payload.get("text") or payload.get("core_text") or "")
        if not context_text:
            continue
        lexical = _lexical_overlap(query_terms, list(payload.get("lexical_tokens") or lexical_tokens(context_text)))
        vector_score = dense_scores.get(point_id, 0.0)
        exact_boost = 0.12 if len(query_compact) >= 2 and query_compact in re.sub(r"\s+", "", context_text) else 0.0
        speaker_hints = [str(value) for value in payload.get("speaker_hints") or []]
        speaker_anchors = [str(value) for value in payload.get("speaker_anchors") or []]
        speaker_boost = 0.12 if any(hint in query for hint in speaker_hints) else 0.0
        dialogue_boost = 0.09 if dialogue_query and payload.get("chunk_kind") == "dialogue" else 0.0
        rerank_score = max(vector_score, 0.78 * lexical) + exact_boost + speaker_boost + dialogue_boost
        ranked.append(
            {
                "document_id": payload["document_id"],
                "version_id": payload["version_id"],
                "source_type": payload["source_type"],
                "source_id": payload["source_id"],
                "text": context_text,
                "score": min(1.0, rerank_score),
                "vector_score": vector_score,
                "lexical_score": lexical,
                "chunk_kind": payload.get("chunk_kind", "legacy"),
                "chunk_index": payload.get("chunk_index"),
                "unit_start": payload.get("unit_start"),
                "unit_end": payload.get("unit_end"),
                "speaker_hints": speaker_hints,
                "speaker_anchors": speaker_anchors,
                "retrieval_mode": "dense+lexical" if point_id in dense_scores and lexical > 0 else ("dense" if point_id in dense_scores else "lexical"),
            }
        )
    ranked.sort(key=lambda item: item["score"], reverse=True)

    selected: list[dict] = []
    for item in ranked:
        if any(_overlap_ratio(item, previous) >= 0.72 for previous in selected):
            continue
        selected.append(item)
        if len(selected) >= min(limit, 20):
            break
    return selected

