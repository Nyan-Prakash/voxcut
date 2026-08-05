"""Structural snapshots for ripple edits (Interject).

A ripple edit (insert_time/remove_time) mutates far more than edl.json: the
VO master, beats.json, Word times, silences.json, waveform.json, music
regions, and Project.duration_s all move together. Undo must restore them
atomically, so alongside each edl.v{n}.json snapshot a ripple edit writes a
struct.v{n}.json capturing everything else. VO masters are written to new
per-version files (media/vo_edit.py), so restoring one is a pointer flip.
"""
from __future__ import annotations

import json

from sqlmodel import select

from .config import settings
from .db import session_scope
from .models import Project, Word

# The ingest-canonical master is never GC'd, whatever the pointer says.
CANONICAL_MASTER = "voiceover_master.m4a"


def struct_path(project_id: str, version: int):
    return settings().project_dir(project_id) / f"struct.v{version}.json"


def vo_version_path(project_id: str, version: int):
    return settings().project_dir(project_id) / f"voiceover_master.v{version}.m4a"


def _read_json(path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def capture_struct(project_id: str, dirty_event_ids: list[str] | None = None) -> dict:
    """Snapshot everything a ripple edit is about to move."""
    pdir = settings().project_dir(project_id)
    with session_scope() as db:
        p = db.get(Project, project_id)
        words = db.exec(select(Word).where(Word.project_id == project_id)
                        .order_by(Word.idx)).all()
        proj_settings = json.loads(p.settings or "{}") if p else {}
    return {
        "duration_s": p.duration_s if p else 0.0,
        "voiceover_path": p.voiceover_path if p else None,
        "music": proj_settings.get("music"),
        "beats": _read_json(pdir / "beats.json"),
        "silences": _read_json(pdir / "silences.json"),
        "waveform": _read_json(pdir / "waveform.json"),
        "words": [[w.idx, w.start_s, w.end_s] for w in words],
        # Events whose cached segments the edit invalidates — re-dirtied on
        # restore so undo never concats a stale-duration segment.
        "dirty": dirty_event_ids or [],
    }


def write_struct_snapshot(project_id: str, version: int, doc: dict) -> None:
    struct_path(project_id, version).write_text(json.dumps(doc))


def restore_struct(project_id: str, version: int) -> bool:
    """Restore the struct state saved at `version` (if any). Returns whether a
    snapshot existed. Consumes the snapshot file, mirroring edl undo."""
    path = struct_path(project_id, version)
    if not path.exists():
        return False
    doc = json.loads(path.read_text())
    pdir = settings().project_dir(project_id)

    for name, key in (("beats.json", "beats"), ("silences.json", "silences"),
                      ("waveform.json", "waveform")):
        if doc.get(key) is not None:
            (pdir / name).write_text(json.dumps(doc[key]))

    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.duration_s = float(doc.get("duration_s") or 0.0)
            if doc.get("voiceover_path"):
                p.voiceover_path = doc["voiceover_path"]
            s = json.loads(p.settings or "{}")
            if doc.get("music") is not None:
                s["music"] = doc["music"]
            else:
                s.pop("music", None)
            p.settings = json.dumps(s)
            db.add(p)
            db.commit()
        by_idx = {w[0]: w for w in doc.get("words", [])}
        rows = db.exec(select(Word).where(Word.project_id == project_id)).all()
        for row in rows:
            saved = by_idx.get(row.idx)
            if saved:
                row.start_s, row.end_s = float(saved[1]), float(saved[2])
                db.add(row)
        db.commit()

    for eid in doc.get("dirty", []):
        for sub in ("segments", "segments_full"):
            seg_dir = pdir / sub
            (seg_dir / f"{eid}.mp4").unlink(missing_ok=True)
            (seg_dir / f"thumb_{eid}.jpg").unlink(missing_ok=True)
    # Stale beat-range artifacts — times moved, let the next scout re-run.
    (pdir / "highlights.json").unlink(missing_ok=True)

    path.unlink(missing_ok=True)
    gc_vo_versions(project_id)
    return True


def gc_vo_versions(project_id: str) -> None:
    """Delete voiceover_master.v*.m4a files no snapshot or pointer references."""
    pdir = settings().project_dir(project_id)
    referenced: set[str] = set()
    with session_scope() as db:
        p = db.get(Project, project_id)
        if p and p.voiceover_path:
            referenced.add(p.voiceover_path)
    for snap in pdir.glob("struct.v*.json"):
        try:
            vp = json.loads(snap.read_text()).get("voiceover_path")
            if vp:
                referenced.add(vp)
        except Exception:  # noqa: BLE001 — a bad snapshot must not block GC
            continue
    for f in pdir.glob("voiceover_master.v*.m4a"):
        if str(f) not in referenced:
            f.unlink(missing_ok=True)


def prune_struct_snapshots(project_id: str, keep_versions: set[int]) -> None:
    """Drop struct snapshots whose edl snapshot was pruned, then GC VO files."""
    pdir = settings().project_dir(project_id)
    removed = False
    for snap in pdir.glob("struct.v*.json"):
        try:
            v = int(snap.stem.split("v")[-1])
        except ValueError:
            continue
        if v not in keep_versions:
            snap.unlink(missing_ok=True)
            removed = True
    if removed:
        gc_vo_versions(project_id)
