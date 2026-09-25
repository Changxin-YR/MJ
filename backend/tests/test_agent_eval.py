import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_core_flow import call, register
from test_generation_flow import make_ready_shot

from app.agent.gateway import gateway
from app.api.errors import APIError
from app.main import app

CASES = json.loads((Path(__file__).parents[1] / "evals" / "cases.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def context():
    client = TestClient(app)
    _, owner = register(client, "eval-owner")
    prefix, shot = make_ready_shot(client, owner)
    code, body = call(client, "GET", prefix, owner)
    assert code == 200, body
    project = body["data"]
    _, body = call(client, "POST", prefix + "/stories", owner, json={"title": "Secret", "content": "The azure cryptogram is stored in Mira's orange messenger bag beside the silent clock tower."})
    story_id = body["data"]["id"]
    code, body = call(client, "POST", prefix + f"/knowledge/stories/{story_id}/index", owner)
    assert code == 200, body
    dialogue_text = """顾七问：“石门后有人吗？”
“现在没有。”
“你怎么知道？”
“脚印是旧的。”
顾七又问：“那枚青铜钥匙呢？”
“第三块青砖后面。”
“谁知道？”
“只有你和我。”"""
    code, body = call(client, "POST", prefix + "/stories", owner, json={"title": "石门对白", "content": dialogue_text})
    assert code == 200, body
    dialogue_story_id = body["data"]["id"]
    code, body = call(client, "POST", prefix + f"/knowledge/stories/{dialogue_story_id}/index", owner)
    assert code == 200, body
    _, body = call(client, "POST", prefix + "/characters", owner, json={"name": "Mira", "dna": {"prompt_anchor": "blue raincoat and orange messenger bag"}})
    character = body["data"]
    version_id = character["versions"][0]["id"]
    code, body = call(client, "POST", prefix + f"/characters/{character['id']}/versions/{version_id}/activate", owner, json={"expected_version": 1})
    assert code == 200, body
    code, body = call(client, "PATCH", prefix + f"/shots/{shot['id']}", owner, json={"expected_version": shot["version"], "character_ids": [character["id"]]})
    assert code == 200, body
    shot = body["data"]
    code, body = call(client, "POST", prefix + "/director/runs", owner, json={"request": "Summarize the azure cryptogram", "intent": "ANALYZE"})
    assert code == 200, body
    run = body["data"]
    viewer_email, viewer = register(client, "eval-viewer")
    code, body = call(client, "POST", f"/api/v1/workspaces/{project['workspace_id']}/members", owner, json={"email": viewer_email, "role": "MEMBER"})
    assert code == 200, body
    code, body = call(client, "POST", prefix + "/members", owner, json={"email": viewer_email, "role": "VIEWER"})
    assert code == 200, body
    return {"client": client, "owner": owner, "viewer": viewer, "prefix": prefix, "project": project, "shot": shot, "story_id": story_id, "dialogue_story_id": dialogue_story_id, "character": character, "run": run}


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_fixed_agent_evaluation_cases(context, case):
    client = context["client"]
    prefix = context["prefix"]
    run = context["run"]
    shot = context["shot"]
    case_id = case["id"]
    if case_id == "tool_correctness":
        assert {item["name"] for item in run["tool_calls"]} == {"load_project_context", "retrieve_semantic_context"}
        assert all(item["status"] == "SUCCEEDED" for item in run["tool_calls"])
    elif case_id == "structured_output":
        assert run["status"] == "COMPLETED"
        assert isinstance(run["decision_summary"], str) and run["decision_summary"]
        assert isinstance(run["retrieved_sources"], list) and isinstance(run["warnings"], list)
        assert run["generation_jobs"] == []
    elif case_id == "context_grounding":
        assert run["retrieved_sources"][0]["source_id"] == context["story_id"]
        assert context["project"]["name"] in run["decision_summary"]
    elif case_id == "character_consistency":
        code, body = call(client, "GET", prefix + f"/shots/{shot['id']}/built-prompt", context["owner"])
        assert code == 200, body
        assert "blue raincoat and orange messenger bag" in body["data"]["prompt"]
    elif case_id == "policy_compliance":
        with pytest.raises(APIError) as error:
            gateway.invoke(run["id"], "execute_sql", {})
        assert error.value.code == "PERMISSION_DENIED"
    elif case_id == "permission_safety":
        with pytest.raises(APIError) as error:
            gateway.invoke(run["id"], "retrieve_semantic_context", {"query": "azure cryptogram", "project_id": "foreign"})
        assert error.value.code == "PERMISSION_DENIED"
        code, body = call(client, "POST", prefix + f"/shots/{shot['id']}/generations", context["viewer"], json={"kind": "IMAGE", "idempotency_key": "eval-viewer-denied"})
        assert code == 403 and body["error"]["code"] == "PERMISSION_DENIED"
    elif case_id == "rag_source_quality":
        code, body = call(client, "POST", prefix + "/knowledge/search", context["owner"], json={"query": "azure cryptogram"})
        assert code == 200 and body["data"], body
        assert body["data"][0]["source_id"] == context["story_id"]
        assert "azure cryptogram" in body["data"][0]["text"]
    elif case_id == "cn_subjectless_dialogue":
        code, body = call(client, "POST", prefix + "/knowledge/search", context["owner"], json={"query": "顾七问石门后有没有人，对方为什么说现在没有？", "limit": 5})
        assert code == 200 and body["data"], body
        assert any(
            item["source_id"] == context["dialogue_story_id"]
            and "顾七" in item["text"]
            and "脚印是旧的" in item["text"]
            for item in body["data"]
        )
    elif case_id == "cn_character_dialogue_retrieval":
        code, body = call(client, "POST", prefix + "/knowledge/search", context["owner"], json={"query": "顾七问青铜钥匙在哪里，对方怎么回答？", "limit": 5})
        assert code == 200 and body["data"], body
        hit = next(item for item in body["data"] if "第三块青砖后面" in item["text"])
        assert hit["source_id"] == context["dialogue_story_id"]
        assert hit.get("chunk_kind") == "dialogue"
    else:
        raise AssertionError(f"Unknown evaluation case: {case_id}")
