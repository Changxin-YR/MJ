from types import SimpleNamespace

import pytest

from app.api.errors import APIError
from app.auth.dependencies import ProjectScope, require
from app.storyboard.state import transition_job, transition_shot


def test_shot_state_machine_guards_lock():
    shot = SimpleNamespace(status="DRAFT", version=1)
    transition_shot(shot, "PLANNED")
    assert (shot.status, shot.version) == ("PLANNED", 2)
    with pytest.raises(APIError) as error:
        transition_shot(shot, "LOCKED")
    assert error.value.code == "RESOURCE_CONFLICT"
    shot.status = "LOCKED"
    with pytest.raises(APIError) as error:
        transition_shot(shot, "PLANNED")
    assert error.value.code == "SHOT_LOCKED"


def test_generation_success_is_terminal():
    job = SimpleNamespace(status="CREATED")
    for status in ("QUEUED", "RUNNING", "SUCCEEDED"):
        transition_job(job, status)
    with pytest.raises(APIError):
        transition_job(job, "RUNNING")


def test_viewer_cannot_generate():
    scope = ProjectScope("u", "s", "w", "p", "VIEWER")
    with pytest.raises(APIError) as error:
        require(scope, "generation.create")
    assert error.value.code == "PERMISSION_DENIED"
