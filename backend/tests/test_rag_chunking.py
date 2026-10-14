from app.rag.service import semantic_chunks


def test_semantic_chunks_keep_paragraphs_together():
    text = "第一段交代人物和地点。\n\n第二段承接冲突，人物开始对话。\n\n第三段完成一个小转折。"
    parts = semantic_chunks(text, size=80, overlap=10)
    assert len(parts) == 1
    assert "第一段" in parts[0] and "第三段" in parts[0]


def test_semantic_chunks_split_at_sentence_boundary_with_context():
    paragraphs = [f"第{i}段描述场景和动作，继续补充细节。" for i in range(12)]
    parts = semantic_chunks("\n\n".join(paragraphs), size=100, overlap=20)
    assert len(parts) > 1
    assert all(len(part) <= 120 for part in parts)
    # Adjacent chunks share a short sentence tail, rather than arbitrary bytes.
    assert any(set(parts[i].split()) & set(parts[i + 1].split()) for i in range(len(parts) - 1))


def test_semantic_chunks_reject_invalid_overlap():
    try:
        semantic_chunks("text", size=10, overlap=10)
    except ValueError as error:
        assert "overlap" in str(error)
    else:
        raise AssertionError("expected invalid overlap to fail")
