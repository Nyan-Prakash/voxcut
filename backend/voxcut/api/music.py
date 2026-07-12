"""Music library + per-project music regions (operator tracks only)."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import settings
from ..db import session_scope
from ..models import Project
from ..music import (AUDIO_EXTS, MOODS, list_tracks, music_dir, set_mood,
                     suggest_regions, track_path)

router = APIRouter(prefix="/api", tags=["music"])


@router.get("/music")
def tracks() -> dict:
    return {"tracks": list_tracks(), "moods": MOODS}


@router.post("/music/upload")
async def upload(file: UploadFile) -> dict:
    name = re.sub(r"[^\w.\- ]", "_", file.filename or "track")
    if not any(name.lower().endswith(ext) for ext in AUDIO_EXTS):
        raise HTTPException(400, f"unsupported type (want {', '.join(sorted(AUDIO_EXTS))})")
    dest = music_dir() / name
    dest.write_bytes(await file.read())
    return {"ok": True, "name": name, "tracks": list_tracks()}


@router.delete("/music/{name}")
def delete(name: str) -> dict:
    p = track_path(name)
    if not p:
        raise HTTPException(404, "track not found")
    p.unlink()
    return {"ok": True, "tracks": list_tracks()}


class MoodBody(BaseModel):
    mood: str | None


@router.post("/music/{name}/mood")
def mood(name: str, body: MoodBody) -> dict:
    if body.mood is not None and body.mood not in MOODS:
        raise HTTPException(400, f"mood must be one of {MOODS}")
    if not track_path(name):
        raise HTTPException(404, "track not found")
    set_mood(name, body.mood)
    return {"ok": True, "tracks": list_tracks()}


@router.post("/projects/{project_id}/music/suggest")
def suggest(project_id: str) -> dict:
    """Fill the music lane from the video's beat tones + the operator's
    mood-tagged tracks. Explicit action only — never runs on its own."""
    with session_scope() as db:
        p = db.get(Project, project_id)
        if not p:
            raise HTTPException(404, "project not found")
        proj_settings = json.loads(p.settings or "{}")
        duration = p.duration_s or 0.0

    beats_path = settings().project_dir(project_id) / "beats.json"
    if not beats_path.exists():
        raise HTTPException(400, "no beats yet — generate the edit first")
    beats = json.loads(beats_path.read_text())["beats"]

    all_tracks = list_tracks()
    if not any(t.get("mood") for t in all_tracks):
        raise HTTPException(400, "tag at least one track with a mood first "
                                 "(Library → Music)")
    regions = suggest_regions(beats, all_tracks, duration)
    if not regions:
        raise HTTPException(400, "could not build any music regions")

    music = proj_settings.get("music") or {}
    music.setdefault("enabled", True)
    music.setdefault("volume_db", -25.0)
    music.setdefault("duck_db", 0.0)  # solid level by default
    music["regions"] = regions
    proj_settings["music"] = music
    with session_scope() as db:
        p = db.get(Project, project_id)
        p.settings = json.dumps(proj_settings)
        db.add(p)
        db.commit()
    return {"music": music}
