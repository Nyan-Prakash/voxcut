"""Voiceover upload + waveform (spec §11.3)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlmodel import Session

from ..config import settings
from ..db import get_session
from ..jobs.runner import runner
from ..models import Project, Setting

router = APIRouter(prefix="/api/projects", tags=["voiceover"])


@router.post("/{project_id}/voiceover")
async def upload_voiceover(project_id: str, file: UploadFile = File(...),
                           db: Session = Depends(get_session)) -> dict:
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")

    pdir = settings().project_dir(project_id)
    suffix = Path(file.filename or "upload").suffix or ".bin"
    raw_path = pdir / f"source_upload{suffix}"
    with raw_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    tier_row = db.get(Setting, "transcription_quality")
    tier = tier_row.value if tier_row else "balanced"

    job_id = await runner.submit("transcribe", project_id=project_id,
                                 payload={"src": str(raw_path), "tier": tier})
    return {"job_id": job_id}


@router.get("/{project_id}/waveform")
def get_waveform(project_id: str) -> JSONResponse:
    path = settings().project_dir(project_id) / "waveform.json"
    if not path.exists():
        raise HTTPException(404, "waveform not ready")
    return JSONResponse(json.loads(path.read_text()))
