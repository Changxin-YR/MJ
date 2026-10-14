"""Shared approval rules for the current video asset."""

def video_inspection(shot):
    inspection = shot.inspection_json or {}
    if inspection.get("kind", "VIDEO") != "VIDEO" or inspection.get("asset_id", shot.current_video_asset_id) != shot.current_video_asset_id:
        return {"status": "UNVERIFIED"}
    return inspection


def reviewed_video(shot, asset_id):
    if not asset_id or shot.current_video_asset_id != asset_id or shot.status not in {"APPROVED", "LOCKED"}:
        return False
    inspection = video_inspection(shot)
    override = inspection.get("review_override") or {}
    return inspection.get("status") == "PASS" or (override.get("video_asset_id") == asset_id and bool(str(override.get("reason", "")).strip()))
