"""Beats read + review edits (spec §6.3, §11.3).

The beat-review overlay (nudge/merge/split/lock) patches beats.json directly.
Timing edits here are word-index based; times are recomputed by snapping.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..config import settings
from ..db import get_session
from ..jobs.runner import runner
from ..models import Word

router = APIRouter(prefix="/api/projects", tags=["beats"])


def _beats_path(project_id: str):
    return settings().project_dir(project_id) / "beats.json"


def _load(project_id: str) -> dict:
    p = _beats_path(project_id)
    if not p.exists():
        raise HTTPException(404, "beats not generated yet")
    return json.loads(p.read_text())


def _save(project_id: str, doc: dict) -> None:
    doc["version"] = doc.get("version", 1) + 1
    _beats_path(project_id).write_text(json.dumps(doc, indent=2))


@router.get("/{project_id}/beats")
def get_beats(project_id: str) -> dict:
    return _load(project_id)


@router.post("/{project_id}/beats/rederive")
async def rederive(project_id: str) -> dict:
    job_id = await runner.submit("beats", project_id=project_id)
    return {"job_id": job_id}


def _recompute_times(project_id: str, beats: list[dict], db: Session) -> None:
    """Recompute start/end times + text for beats after a word-index edit."""
    from ..brain.segment import RawBeat, W, _finalize

    rows = db.exec(
        select(Word).where(Word.project_id == project_id).order_by(Word.idx)
    ).all()
    words = [W(idx=w.idx, text=w.corrected_text or w.text,
              start_s=w.start_s, end_s=w.end_s) for w in rows]
    sil_path = settings().project_dir(project_id) / "silences.json"
    silences = ([tuple(s) for s in json.loads(sil_path.read_text())["silences"]]
                if sil_path.exists() else [])
    duration = words[-1].end_s if words else 0.0

    raws = [RawBeat(start_word=b["word_start_idx"], end_word=b["word_end_idx"],
                    gist=b.get("gist", ""), tone=b.get("tone", "neutral"),
                    emphasis=b.get("emphasis", 0.4),
                    concrete_entities=b.get("concrete_entities", []),
                    visual_affinity=b.get("visual_affinity", "literal"))
            for b in beats]
    recomputed = _finalize(raws, words, silences, duration)
    # Preserve ids and locked flags from the originals.
    for new, old in zip(recomputed, beats):
        new["id"] = old.get("id", new["id"])
        new["locked"] = old.get("locked", False)
    beats[:] = recomputed


class NudgeBody(BaseModel):
    beat_id: str
    boundary: str  # "start" | "end"
    delta_words: int  # +1 / -1


@router.post("/{project_id}/beats/nudge")
def nudge(project_id: str, body: NudgeBody, db: Session = Depends(get_session)) -> dict:
    doc = _load(project_id)
    beats = doc["beats"]
    i = next((k for k, b in enumerate(beats) if b["id"] == body.beat_id), None)
    if i is None:
        raise HTTPException(404, "beat not found")
    b = beats[i]
    if body.boundary == "start":
        b["word_start_idx"] += body.delta_words
        if i > 0:
            beats[i - 1]["word_end_idx"] += body.delta_words
    else:
        b["word_end_idx"] += body.delta_words
        if i < len(beats) - 1:
            beats[i + 1]["word_start_idx"] += body.delta_words
    _recompute_times(project_id, beats, db)
    _save(project_id, doc)
    return doc


class MergeBody(BaseModel):
    beat_id: str  # merge this beat into the next one


@router.post("/{project_id}/beats/merge")
def merge(project_id: str, body: MergeBody, db: Session = Depends(get_session)) -> dict:
    doc = _load(project_id)
    beats = doc["beats"]
    i = next((k for k, b in enumerate(beats) if b["id"] == body.beat_id), None)
    if i is None or i >= len(beats) - 1:
        raise HTTPException(400, "cannot merge (last or missing beat)")
    beats[i]["word_end_idx"] = beats[i + 1]["word_end_idx"]
    beats[i]["emphasis"] = max(beats[i]["emphasis"], beats[i + 1]["emphasis"])
    del beats[i + 1]
    _recompute_times(project_id, beats, db)
    _save(project_id, doc)
    return doc


class SplitBody(BaseModel):
    beat_id: str
    at_word_idx: int  # this word starts the new second beat


@router.post("/{project_id}/beats/split")
def split(project_id: str, body: SplitBody, db: Session = Depends(get_session)) -> dict:
    from ..models import new_id
    doc = _load(project_id)
    beats = doc["beats"]
    i = next((k for k, b in enumerate(beats) if b["id"] == body.beat_id), None)
    if i is None:
        raise HTTPException(404, "beat not found")
    b = beats[i]
    if not (b["word_start_idx"] < body.at_word_idx <= b["word_end_idx"]):
        raise HTTPException(400, "split point outside beat")
    second = dict(b)
    second["id"] = new_id("bt")
    second["word_start_idx"] = body.at_word_idx
    b["word_end_idx"] = body.at_word_idx - 1
    beats.insert(i + 1, second)
    _recompute_times(project_id, beats, db)
    _save(project_id, doc)
    return doc


class LockBody(BaseModel):
    beat_id: str
    locked: bool


@router.post("/{project_id}/beats/lock")
def lock(project_id: str, body: LockBody) -> dict:
    doc = _load(project_id)
    b = next((b for b in doc["beats"] if b["id"] == body.beat_id), None)
    if not b:
        raise HTTPException(404, "beat not found")
    b["locked"] = body.locked
    _save(project_id, doc)
    return doc
