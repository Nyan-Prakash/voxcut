"""TikTok highlights: scout the finished edit for clip-worthy moments,
then export chosen ones as 9:16 verticals (see jobs/steps/highlights.py)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..jobs.runner import runner
from ..jobs.steps.highlights import clip_path, highlights_path

router = APIRouter(prefix="/api/projects", tags=["highlights"])


@router.get("/{project_id}/highlights")
def get_highlights(project_id: str) -> dict:
    path = highlights_path(project_id)
    if not path.exists():
        return {"analyzed": False, "clips": []}
    doc = json.loads(path.read_text())
    for c in doc.get("clips", []):
        c["exported"] = clip_path(project_id, c["index"]).exists()
    return {"analyzed": True, "clips": doc.get("clips", [])}


@router.post("/{project_id}/highlights/analyze")
async def analyze(project_id: str) -> dict:
    job_id = await runner.submit("highlights", project_id=project_id)
    return {"job_id": job_id}


@router.post("/{project_id}/highlights/{index}/export")
async def export_clip(project_id: str, index: int) -> dict:
    job_id = await runner.submit("export_clip", project_id=project_id,
                                 payload={"index": index})
    return {"job_id": job_id}


@router.get("/{project_id}/highlights/{index}/download")
def download_clip(project_id: str, index: int) -> FileResponse:
    path = clip_path(project_id, index)
    if not path.exists():
        raise HTTPException(404, "clip not exported yet")
    return FileResponse(path, media_type="video/mp4",
                        filename=f"voxcut_tiktok_{index + 1}.mp4")
