"""Trim step: files and state on disk.

Raw (as-uploaded, normalized) media lives beside the working copies:

    raw_master.m4a / raw_asr.wav / raw_facecam.mp4 / raw_transcript.json

The working copies (voiceover_master.m4a, voiceover_asr.wav, facecam.mp4,
transcript.json, Word rows) are what the rest of the pipeline reads; the trim
job rebuilds them from raw + a list of cuts. trim.json holds the state:

    pending  — uploaded and transcribed; generate is blocked until the
               operator trims or explicitly skips
    done     — cuts applied
    skipped  — left as recorded

Projects from before this step have no trim.json and are never blocked.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from sqlmodel import delete, select

from .config import settings
from .db import session_scope
from .models import Project, Word

RAW = {"master": "raw_master.m4a", "asr": "raw_asr.wav",
       "facecam": "raw_facecam.mp4", "transcript": "raw_transcript.json"}
WORK = {"master": "voiceover_master.m4a", "asr": "voiceover_asr.wav",
        "facecam": "facecam.mp4", "transcript": "transcript.json"}


def pdir(project_id: str) -> Path:
    return settings().project_dir(project_id)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)        # same disk: free, instant
    except OSError:
        shutil.copy2(src, dst)


def read_state(project_id: str) -> dict:
    path = pdir(project_id) / "trim.json"
    if not path.exists():
        return {"state": "none", "cuts": []}
    return json.loads(path.read_text())


def write_state(project_id: str, state: str, cuts: list, raw_duration_s: float) -> dict:
    doc = {"state": state, "cuts": [list(c) for c in cuts],
           "raw_duration_s": raw_duration_s}
    (pdir(project_id) / "trim.json").write_text(json.dumps(doc))
    return doc


def has_facecam(project_id: str) -> bool:
    return (pdir(project_id) / WORK["facecam"]).exists()


def stash_raw(project_id: str, transcript_doc: dict, duration_s: float) -> None:
    """Called once, right after transcription: keep the as-uploaded media and
    transcript so trims are always cut from the original, and open the trim
    step."""
    d = pdir(project_id)
    for key in ("master", "asr", "facecam"):
        if (d / WORK[key]).exists():
            _link_or_copy(d / WORK[key], d / RAW[key])
    (d / RAW["transcript"]).write_text(json.dumps(transcript_doc))
    write_state(project_id, "pending", [], duration_s)
    write_word_base(project_id)


def raw_transcript(project_id: str) -> dict | None:
    path = pdir(project_id) / RAW["transcript"]
    return json.loads(path.read_text()) if path.exists() else None


# ------------------------------------------------------ facecam time base

def write_word_base(project_id: str) -> None:
    """Remember when each word is spoken in the facecam video. Ripple edits
    (interjections) later shift Word times but never touch the facecam file,
    so `current word time − base word time` is how far the timeline has
    moved away from the footage at that point. See facecam_clock."""
    with session_scope() as db:
        rows = db.exec(select(Word).where(Word.project_id == project_id)
                       .order_by(Word.idx)).all()
    (pdir(project_id) / "facecam_base.json").write_text(
        json.dumps({"words": [[w.idx, w.start_s] for w in rows]}))


def facecam_clock(project_id: str):
    """Returns f(timeline_s) → seconds into facecam.mp4."""
    path = pdir(project_id) / "facecam_base.json"
    base = dict(json.loads(path.read_text())["words"]) if path.exists() else {}
    with session_scope() as db:
        rows = db.exec(select(Word).where(Word.project_id == project_id)
                       .order_by(Word.idx)).all()
    # (timeline start, shift) for every word that still has a base time.
    marks = sorted((w.start_s, round(w.start_s - base[w.idx], 3))
                   for w in rows if w.idx in base)

    def at(t: float) -> float:
        shift = 0.0
        for start, sh in marks:          # shifts only ever change at inserts
            if start > t + 1e-6:
                break
            shift = sh
        return max(0.0, round(t - shift, 3))
    return at


# ------------------------------------------------------------ persistence

def save_words(project_id: str, words: list[dict], language: str,
               silences: list) -> None:
    """Replace the working transcript (DB rows + transcript.json + silences)."""
    d = pdir(project_id)
    with session_scope() as db:
        db.exec(delete(Word).where(Word.project_id == project_id))
        for w in words:
            db.add(Word(project_id=project_id, idx=w["idx"], text=w["text"],
                        start_s=w["start_s"], end_s=w["end_s"],
                        confidence=w.get("confidence", 1.0)))
        db.commit()
    (d / WORK["transcript"]).write_text(json.dumps(
        {"language": language, "words": words}))
    (d / "silences.json").write_text(json.dumps({"silences": silences}))


def set_project(project_id: str, **fields) -> None:
    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            for k, v in fields.items():
                setattr(p, k, v)
            db.add(p)
            db.commit()
