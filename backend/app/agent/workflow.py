import json
from typing import TypedDict

from langgraph.checkpoint.redis import RedisSaver
from langgraph.checkpoint.redis.jsonplus_redis import JsonPlusRedisSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agent.gateway import CONTRACTS, gateway
from app.api.errors import APIError
from app.config import settings
from app.providers.llm import DashScopeLLMProvider


class DirectorState(TypedDict, total=False):
    identity: dict
    scope: dict
    request: str
    intent: str
    shot_id: str | None
    project_context: dict
    story_context: list
    character_context: list
    world_context: list
    episode_plan: dict
    scene_plan: dict
    shot_plan: dict
    generation_jobs: list
    inspection_results: dict
    pending_action: dict
    retry_state: dict
    errors: list
    current_stage: str
    context_package: dict
    decision_summary: str
    warnings: list
    canonical_result: str
    memory_status: str


def authenticate_context(state: DirectorState):
    gateway.authorize(state["identity"]["run_id"])
    return {"current_stage": "Authenticate Context"}


def load_project_scope(state: DirectorState):
    _, scope = gateway.authorize(state["identity"]["run_id"])
    return {"scope": {"workspace_id": scope.workspace_id, "project_id": scope.project_id}, "current_stage": "Load Project Scope"}


def understand_intent(state: DirectorState):
    allowed = {"ANALYZE", "GENERATE_IMAGE", "GENERATE_VIDEO", "GENERATE_VOICE"}
    if state["intent"] not in allowed:
        raise APIError("INVALID_PARAMETER", "Unknown Director intent", 422)
    return {"current_stage": "Understand Intent"}


def load_structured_context(state: DirectorState):
    context = gateway.invoke(state["identity"]["run_id"], "load_project_context", {})
    return {"project_context": context, "character_context": context["characters"], "current_stage": "Load Structured Context"}


def retrieve_semantic_context(state: DirectorState):
    sources = gateway.invoke(state["identity"]["run_id"], "retrieve_semantic_context", {"query": state["request"]})
    return {"story_context": sources, "world_context": [], "current_stage": "Retrieve Semantic Context"}


def build_context_package(state: DirectorState):
    return {"context_package": {"structured": state.get("project_context", {}), "semantic_untrusted_data": state.get("story_context", [])}, "current_stage": "Build Context Package"}


def plan(state: DirectorState):
    intent = state["intent"]
    if intent == "ANALYZE":
        if settings.director_llm_mode == "dashscope":
            package = {
                "request": state["request"][:1000],
                "project": state["project_context"]["project"]["name"],
                "shot_count": state["project_context"]["shot_count"],
                "characters": [
                    {
                        "name": character["name"],
                        "background": character.get("background", "")[:500],
                        "dna": character.get("dna", {}),
                    }
                    for character in state["project_context"].get("characters", [])[:30]
                ],
                "retrieved_sources": [
                    {
                        "text": source["text"][:1200],
                        "score": source.get("score"),
                        "chunk_kind": source.get("chunk_kind", "legacy"),
                    }
                    for source in state.get("story_context", [])[:5]
                ],
            }
            summary = DashScopeLLMProvider().complete(json.dumps(package, ensure_ascii=False))
        elif settings.director_llm_mode == "fake":
            summary = f"Project {state['project_context']['project']['name']}: {state['project_context']['shot_count']} shots, {len(state.get('story_context', []))} retrieved sources."
        else:
            raise RuntimeError(f"Unsupported Director LLM mode: {settings.director_llm_mode}")
        return {"shot_plan": {}, "decision_summary": summary, "current_stage": "Plan"}
    if not state.get("shot_id"):
        raise APIError("INVALID_PARAMETER", "Shot is required for generation", 422)
    kind = intent.split("_", 1)[1]
    arguments = {"shot_id": state["shot_id"], "kind": kind, "idempotency_key": f"director:{state['identity']['run_id']}:{kind}:{state['shot_id']}"}
    return {"shot_plan": {"tool": "generate_shot", "arguments": arguments}, "decision_summary": f"Request {kind.lower()} generation for shot {state['shot_id']} after approval.", "current_stage": "Plan"}


def check_approval(state: DirectorState):
    plan_data = state.get("shot_plan", {})
    if not plan_data:
        return {"current_stage": "Check Approval"}
    contract = CONTRACTS[plan_data["tool"]]
    if not contract.requires_confirmation:
        return {"current_stage": "Check Approval"}
    pending = gateway.prepare_action(state["identity"]["run_id"], plan_data["tool"], plan_data["arguments"]["shot_id"], plan_data["arguments"])
    if pending.status == "PENDING":
        interrupt({"pending_action_id": pending.id, "action": pending.action, "target_id": pending.target_id})
    if pending.status not in {"APPROVED", "EXECUTED"}:
        raise APIError("PENDING_APPROVAL_REQUIRED", "Action was not approved", 409)
    return {"pending_action": {"id": pending.id, "status": pending.status}, "current_stage": "Check Approval"}


def execute_tool(state: DirectorState):
    plan_data = state.get("shot_plan", {})
    if not plan_data:
        return {"current_stage": "Execute Tool"}
    result = gateway.invoke(state["identity"]["run_id"], plan_data["tool"], plan_data["arguments"], state["pending_action"]["id"])
    return {"generation_jobs": [result], "current_stage": "Execute Tool"}


def wait_job(state: DirectorState):
    jobs = state.get("generation_jobs", [])
    if not jobs:
        return {"current_stage": "Wait Job"}
    job = gateway.invoke(state["identity"]["run_id"], "load_generation_job", {"job_id": jobs[0]["id"]})
    return {"generation_jobs": [job], "current_stage": "Wait Job"}


def inspect(state: DirectorState):
    jobs = state.get("generation_jobs", [])
    result = {"status": "PENDING" if jobs else "PASS", "score": 0.0 if jobs else 1.0, "issues": []}
    return {"inspection_results": result, "current_stage": "Inspect"}


def diagnose(state: DirectorState):
    warnings = []
    if not state.get("story_context"):
        warnings.append("No scoped knowledge sources were retrieved")
    if state.get("generation_jobs"):
        warnings.append("Media inspection follows worker completion")
    return {"warnings": warnings, "current_stage": "Diagnose"}


def human_review(state: DirectorState):
    return {"current_stage": "Human Review"}


def persist_canonical_result(state: DirectorState):
    return {"canonical_result": "PENDING_REVIEW" if state.get("generation_jobs") else "NO_MUTATION", "current_stage": "Persist Canonical Result"}


def update_memory(state: DirectorState):
    return {"memory_status": "DEFERRED_UNTIL_CANONICAL_APPROVAL", "current_stage": "Update Memory"}


def audit(state: DirectorState):
    gateway.audit(state["identity"]["run_id"], "director.workflow", state["decision_summary"])
    return {"current_stage": "Audit"}


NODES = [
    ("AuthenticateContext", authenticate_context),
    ("LoadProjectScope", load_project_scope),
    ("UnderstandIntent", understand_intent),
    ("LoadStructuredContext", load_structured_context),
    ("RetrieveSemanticContext", retrieve_semantic_context),
    ("BuildContextPackage", build_context_package),
    ("Plan", plan),
    ("CheckApproval", check_approval),
    ("ExecuteTool", execute_tool),
    ("WaitJob", wait_job),
    ("Inspect", inspect),
    ("Diagnose", diagnose),
    ("HumanReview", human_review),
    ("PersistCanonicalResult", persist_canonical_result),
    ("UpdateMemory", update_memory),
    ("Audit", audit),
]


def build_graph(saver):
    builder = StateGraph(DirectorState)
    for name, fn in NODES:
        builder.add_node(name, fn)
    builder.add_edge(START, NODES[0][0])
    for (previous, _), (following, _) in zip(NODES, NODES[1:], strict=False):
        builder.add_edge(previous, following)
    builder.add_edge(NODES[-1][0], END)
    return builder.compile(checkpointer=saver)


def execute(run_id: str, initial: DirectorState | None = None) -> tuple[dict, str]:
    with RedisSaver.from_conn_string(settings.redis_url) as saver:
        saver.serde = JsonPlusRedisSerializer(pickle_fallback=False, allowed_json_modules=[], allowed_msgpack_modules=[])
        saver.setup()
        graph = build_graph(saver)
        config = {"configurable": {"thread_id": run_id}}
        output = graph.invoke(initial if initial is not None else Command(resume=True), config=config)
        snapshot = graph.get_state(config)
        state = dict(snapshot.values)
        if "__interrupt__" in output:
            return state, "WAITING_APPROVAL"
        return state, "COMPLETED"
