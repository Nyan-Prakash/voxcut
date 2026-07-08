"""EDL document store (spec §4.4): edl.json + versioned snapshots for undo."""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlmodel import Session

from .config import settings
from .db import session_scope
from .models import Project

UNDO_KEEP = 30


def edl_path(project_id: str):
    return settings().project_dir(project_id) / "edl.json"


def load_edl(project_id: str) -> dict:
    p = edl_path(project_id)
    if not p.exists():
        raise HTTPException(404, "edl not generated yet")
    return json.loads(p.read_text())


def save_edl(project_id: str, edl: dict, *, snapshot: bool = True) -> dict:
    pdir = settings().project_dir(project_id)
    current = edl_path(project_id)

    if snapshot and current.exists():
        old = json.loads(current.read_text())
        v = old.get("version", 0)
        (pdir / f"edl.v{v}.json").write_text(json.dumps(old))
        _prune_snapshots(pdir)

    edl["version"] = edl.get("version", 0) + 1
    current.write_text(json.dumps(edl, indent=2))

    with session_scope() as db:
        proj = db.get(Project, project_id)
        if proj:
            proj.edl_version = edl["version"]
            db.add(proj)
            db.commit()
    return edl


def _prune_snapshots(pdir) -> None:
    snaps = sorted(pdir.glob("edl.v*.json"),
                   key=lambda p: int(p.stem.split("v")[-1]))
    for old in snaps[:-UNDO_KEEP]:
        old.unlink(missing_ok=True)


def list_snapshots(project_id: str) -> list[int]:
    pdir = settings().project_dir(project_id)
    return sorted(int(p.stem.split("v")[-1]) for p in pdir.glob("edl.v*.json"))
