"""Trim step API: review the raw recording, apply cuts (or skip)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..edl_store import edl_path
from ..jobs.runner import runner
from ..trim import (DEFAULT_KEEP_PAUSE_S, DEFAULT_MIN_PAUSE_S, retake_cuts)
from ..trim_store import RAW, pdir, raw_transcript, read_state

router = APIRouter(prefix="/api/projects", tags=["trim"])


@router.get("/{project_id}/trim")
def get_trim(project_id: str) -> dict:
    """Everything the trim screen needs. `state` is none (project predates
    the step), pending, done or skipped; `locked` means an edit exists and
    the trim can no longer change."""
    state = read_state(project_id)
    raw = raw_transcript(project_id)
    d = pdir(project_id)
    if raw is None:
        return {"state": state["state"], "has_video": False, "locked": True,
                "cuts": [], "words": [], "silences": [], "retakes": [],
                "raw_duration_s": 0.0}
    return {
        "state": state["state"],
        "cuts": state.get("cuts", []),
        "raw_duration_s": state.get("raw_duration_s", 0.0),
        "has_video": (d / RAW["facecam"]).exists(),
        "locked": edl_path(project_id).exists(),
        "words": [{"idx": w["idx"], "text": w["text"],
                   "start_s": w["start_s"], "end_s": w["end_s"]}
                  for w in raw["words"]],
        "silences": raw.get("silences", []),
        "retakes": retake_cuts(raw["words"]),
        "defaults": {"min_pause_s": DEFAULT_MIN_PAUSE_S,
                     "keep_pause_s": DEFAULT_KEEP_PAUSE_S},
    }


@router.get("/{project_id}/trim/media")
def trim_media(project_id: str) -> FileResponse:
    """The raw recording for the trim preview: the facecam video when there
    is one (silent — the page plays the raw audio alongside), else nothing."""
    path = pdir(project_id) / RAW["facecam"]
    if not path.exists():
        raise HTTPException(404, "no facecam video")
    return FileResponse(path, media_type="video/mp4")


@router.get("/{project_id}/trim/audio")
def trim_audio(project_id: str) -> FileResponse:
    path = pdir(project_id) / RAW["master"]
    if not path.exists():
        raise HTTPException(404, "no raw audio")
    return FileResponse(path, media_type="audio/mp4")


class TrimBody(BaseModel):
    cuts: list[list[float]] = []   # [] = leave as recorded


@router.post("/{project_id}/trim")
async def apply_trim(project_id: str, body: TrimBody) -> dict:
    if raw_transcript(project_id) is None:
        raise HTTPException(400, "nothing to trim yet")
    if edl_path(project_id).exists():
        raise HTTPException(409, "this project already has an edit")
    job_id = await runner.submit("trim", project_id=project_id,
                                 payload={"cuts": body.cuts})
    return {"job_id": job_id}
