from app.api.errors import APIError

SHOT_TRANSITIONS = {
    "DRAFT": {"PLANNED"},
    "PLANNED": {"STORYBOARD_READY", "REJECTED"},
    "STORYBOARD_READY": {"GENERATING", "REJECTED"},
    "GENERATING": {"GENERATED", "FAILED"},
    "GENERATED": {"INSPECTING", "FAILED"},
    "INSPECTING": {"REVIEW_REQUIRED", "FAILED"},
    "REVIEW_REQUIRED": {"APPROVED", "REJECTED", "GENERATING"},
    "APPROVED": {"LOCKED", "REJECTED"},
    "REJECTED": {"PLANNED"},
    "FAILED": {"STORYBOARD_READY"},
    "LOCKED": set(),
}

JOB_TRANSITIONS = {
    "CREATED": {"QUEUED", "CANCELLED"},
    "QUEUED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"SUCCEEDED", "FAILED", "RETRYING", "CANCELLED"},
    "FAILED": {"RETRYING", "CANCELLED"},
    "RETRYING": {"QUEUED", "FAILED", "CANCELLED"},
    "SUCCEEDED": set(),
    "CANCELLED": set(),
}


def transition_shot(shot, target: str) -> None:
    if target not in SHOT_TRANSITIONS.get(shot.status, set()):
        raise APIError("SHOT_LOCKED" if shot.status == "LOCKED" else "RESOURCE_CONFLICT", f"Invalid shot transition {shot.status} → {target}", 409)
    shot.status = target
    shot.version += 1


def transition_job(job, target: str) -> None:
    if target not in JOB_TRANSITIONS.get(job.status, set()):
        raise APIError("RESOURCE_CONFLICT", f"Invalid generation transition {job.status} → {target}", 409)
    job.status = target
