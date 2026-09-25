import io

from fastapi.testclient import TestClient
from PIL import Image
from test_core_flow import call, register

from app.asset import routes as asset_routes
from app.asset.storage import client as minio_client
from app.config import settings
from app.main import app


def test_signed_quarantine_magic_and_project_scope():
    client = TestClient(app)
    _, owner = register(client, "upload-owner")
    _, outsider = register(client, "upload-outsider")
    _, body = call(client, "POST", "/api/v1/workspaces", owner, json={"name": "Uploads"})
    workspace_id = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace_id}/projects", owner, json={"name": "Assets"})
    prefix = f"/api/v1/projects/{body['data']['id']}/assets"

    code, body = call(client, "POST", prefix + "/uploads", owner, json={"mime": "application/x-python", "size": 8})
    assert code == 422 and body["error"]["code"] == "INVALID_PARAMETER"
    code, body = call(client, "POST", prefix + "/uploads", owner, json={"mime": "image/png", "size": 8, "filename": "../../etc/passwd"})
    assert code == 422 and body["error"]["code"] == "INVALID_PARAMETER"

    invalid = b"not-a-png"
    code, body = call(client, "POST", prefix + "/uploads", owner, json={"mime": "image/png", "size": len(invalid)})
    assert code == 200 and body["data"]["upload_url"].startswith("http://localhost:29000/")
    bad_id = body["data"]["asset"]["id"]
    code, body = call(client, "POST", prefix + f"/uploads/{bad_id}/finalize", outsider)
    assert code == 404
    code, body = call(client, "GET", prefix + f"/{bad_id}/content", owner)
    assert code == 409
    storage = minio_client()
    bad_key = f"{workspace_id}/{prefix.split('/')[4]}/quarantine/{bad_id}"
    storage.put_object(settings.minio_bucket, bad_key, io.BytesIO(invalid), len(invalid))
    code, body = call(client, "POST", prefix + f"/uploads/{bad_id}/finalize", owner)
    assert code == 422 and body["error"]["code"] == "INVALID_PARAMETER"

    output = io.BytesIO()
    Image.new("RGB", (64, 64), "#ffcc66").save(output, format="PNG")
    valid = output.getvalue()
    code, body = call(client, "POST", prefix + "/uploads", owner, json={"mime": "image/png", "size": len(valid)})
    assert code == 200
    asset_id = body["data"]["asset"]["id"]
    key = f"{workspace_id}/{prefix.split('/')[4]}/quarantine/{asset_id}"
    storage.put_object(settings.minio_bucket, key, io.BytesIO(valid), len(valid))
    code, body = call(client, "POST", prefix + f"/uploads/{asset_id}/finalize", owner)
    assert code == 200 and body["data"]["status"] == "READY"
    response = client.get(prefix + f"/{asset_id}/content", headers={"Authorization": f"Bearer {owner}"})
    assert response.status_code == 200 and response.content == valid



def test_finalize_stays_successful_when_quarantine_cleanup_fails(monkeypatch):
    client = TestClient(app)
    _, owner = register(client, "upload-cleanup")
    _, body = call(client, "POST", "/api/v1/workspaces", owner, json={"name": "Cleanup Uploads"})
    workspace_id = body["data"]["id"]
    _, body = call(client, "POST", f"/api/v1/workspaces/{workspace_id}/projects", owner, json={"name": "Cleanup Assets"})
    project_id = body["data"]["id"]
    prefix = f"/api/v1/projects/{project_id}/assets"

    output = io.BytesIO()
    Image.new("RGB", (32, 32), "#abcdef").save(output, format="PNG")
    valid = output.getvalue()
    code, body = call(client, "POST", prefix + "/uploads", owner, json={"mime": "image/png", "size": len(valid)})
    assert code == 200, body
    asset_id = body["data"]["asset"]["id"]
    storage = minio_client()
    key = f"{workspace_id}/{project_id}/quarantine/{asset_id}"
    storage.put_object(settings.minio_bucket, key, io.BytesIO(valid), len(valid))

    class CleanupFailure:
        def remove_object(self, *_args, **_kwargs):
            raise RuntimeError("simulated cleanup outage")

    monkeypatch.setattr(asset_routes, "client", lambda: CleanupFailure())
    code, body = call(client, "POST", prefix + f"/uploads/{asset_id}/finalize", owner)
    assert code == 200 and body["data"]["status"] == "READY"
