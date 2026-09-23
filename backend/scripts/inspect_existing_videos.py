"""Persist model inspection on already generated demo video jobs."""

import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.agent.inspector import inspect_media
from app.audit.service import record
from app.db import SessionLocal
from app.models import Asset, GenerationJob, GenerationOutput, Shot
from app.providers.base import MediaResult
from app.timeline.render import download


def main() -> None:
    project_id = sys.argv[1]
    root = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/inspection")
    root.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        jobs = db.scalars(select(GenerationJob).where(GenerationJob.project_id == project_id, GenerationJob.kind == "VIDEO", GenerationJob.status == "SUCCEEDED").order_by(GenerationJob.created_at)).all()
        targets = []
        for job in jobs:
            output = db.scalar(select(GenerationOutput).where(GenerationOutput.job_id == job.id))
            asset = db.get(Asset, output.asset_id)
            shot = db.get(Shot, job.resource_id)
            if shot.current_video_asset_id == asset.id:
                targets.append((job.id, job.trace_id, job.workspace_id, shot, asset))
        for _, _, _, shot, asset in targets:
            db.expunge(shot)
            db.expunge(asset)
    results = []
    for job_id, trace_id, workspace_id, shot, asset in targets:
        inspection = inspect_media(shot, MediaResult(content=download(asset), mime=asset.mime), "dashscope")
        with SessionLocal() as db:
            job = db.get(GenerationJob, job_id)
            current_shot = db.get(Shot, shot.id)
            job.inspection_json = inspection
            current_shot.inspection_json = inspection
            record(db, actor_type="WORKER", actor_id="inspector-recheck", action="inspector.recheck", resource_type="generation_job", resource_id=job_id, workspace_id=workspace_id, project_id=project_id, trace_id=trace_id, safe_summary=inspection["status"])
            db.commit()
        results.append({"job_id": job_id, "shot_id": shot.id, "inspection": inspection})
        print(f"video inspection {len(results)}/{len(targets)}: {inspection['status']}", flush=True)
    (root / "report.json").write_text(json.dumps({"project_id": project_id, "count": len(results), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
