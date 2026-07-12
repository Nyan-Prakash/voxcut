"""EDL read + edit ops + generate + preview serving (spec §4.4, §11.2–11.3).

Every editor action is a JSON-patch-like op against the EDL doc through
POST /edl/ops, which validates, bumps version, and snapshots for undo.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import settings
from ..edl_store import list_snapshots, load_edl, save_edl
from ..jobs.runner import runner

router = APIRouter(prefix="/api/projects", tags=["edl"])


@router.get("/{project_id}/edl")
def get_edl(project_id: str) -> dict:
    return load_edl(project_id)


@router.post("/{project_id}/generate")
async def generate(project_id: str) -> dict:
    job_id = await runner.submit("generate", project_id=project_id)
    return {"job_id": job_id}


class Op(BaseModel):
    op: str          # set_treatment | set_audio | set_kind | delete | lock | set_source | set_asset
    event_id: str | None = None
    data: dict = {}


class OpsBody(BaseModel):
    base_version: int | None = None
    ops: list[Op]


_ALLOWED = {"set_treatment", "set_audio", "set_kind",
            "delete", "lock", "set_source", "set_asset"}


@router.post("/{project_id}/edl/ops")
def apply_ops(project_id: str, body: OpsBody) -> dict:
    edl = load_edl(project_id)
    if body.base_version is not None and body.base_version != edl.get("version"):
        raise HTTPException(409, {"error": "version_conflict",
                                  "current_version": edl.get("version"),
                                  "edl": edl})
    events = {e["id"]: e for e in edl["events"]}
    dirty: list[str] = []

    for op in body.ops:
        if op.op not in _ALLOWED:
            raise HTTPException(400, f"unknown op {op.op!r}")
        if op.op == "delete":
            edl["events"] = [e for e in edl["events"] if e["id"] != op.event_id]
            dirty.append(op.event_id or "")
            continue
        ev = events.get(op.event_id or "")
        if not ev:
            raise HTTPException(404, f"event {op.event_id} not found")
        if op.op == "set_treatment":
            ev["treatment"].update(op.data)
        elif op.op == "set_audio":
            ev["audio"].update(op.data)
        elif op.op == "set_kind":
            ev["kind"] = op.data.get("kind", ev["kind"])
        elif op.op == "set_source":
            ev["source"] = op.data
        elif op.op == "set_asset":
            ev["asset_id"] = op.data.get("asset_id")
            ev["source"] = op.data.get("source", ev.get("source"))
            ev["flags"] = [f for f in ev.get("flags", []) if f != "gap_unfilled"]
        elif op.op == "lock":
            ev["locked"] = bool(op.data.get("locked", True))
        dirty.append(ev["id"])

    edl = save_edl(project_id, edl)
    # Mark dirty segments so the next assemble re-renders only those (§11.2).
    _mark_dirty(project_id, dirty)
    return {"edl": edl, "dirty": dirty}


def _mark_dirty(project_id: str, event_ids: list[str]) -> None:
    seg_dir = settings().project_dir(project_id) / "segments"
    for eid in event_ids:
        for ext in (".mp4", ".ass"):
            (seg_dir / f"{eid}{ext}").unlink(missing_ok=True)
        (seg_dir / f"thumb_{eid}.jpg").unlink(missing_ok=True)


class SplitBody(BaseModel):
    event_id: str
    at_s: float


@router.post("/{project_id}/edl/split")
def split(project_id: str, body: SplitBody) -> dict:
    """Cut an event in two at ~at_s (word-snapped). Both halves keep the same
    footage; the underlying beat splits with it so either half can reroll."""
    from ..timeline_ops import split_event
    return split_event(project_id, body.event_id, body.at_s)


class AddSegmentBody(BaseModel):
    start_s: float
    end_s: float


@router.post("/{project_id}/edl/add_segment")
def add_segment(project_id: str, body: AddSegmentBody) -> dict:
    """Carve out [start_s, end_s] and insert a fresh empty segment there."""
    from ..timeline_ops import add_segment as _add
    return _add(project_id, body.start_s, body.end_s)


@router.post("/{project_id}/edl/undo")
def undo(project_id: str) -> dict:
    snaps = list_snapshots(project_id)
    if not snaps:
        raise HTTPException(400, "nothing to undo")
    pdir = settings().project_dir(project_id)
    last = snaps[-1]
    prev = json.loads((pdir / f"edl.v{last}.json").read_text())
    (pdir / f"edl.v{last}.json").unlink(missing_ok=True)
    prev["version"] = last - 1  # save_edl will bump back to `last`
    save_edl(project_id, prev, snapshot=False)
    return prev


@router.post("/{project_id}/qc")
async def qc(project_id: str) -> dict:
    """Audit the finished edit: flag mediocre-middle clips for review."""
    job_id = await runner.submit("qc", project_id=project_id)
    return {"job_id": job_id}


@router.post("/{project_id}/preview/rebuild")
async def rebuild_preview(project_id: str) -> dict:
    job_id = await runner.submit("assemble", project_id=project_id)
    return {"job_id": job_id}


@router.get("/{project_id}/preview")
def preview(project_id: str) -> FileResponse:
    path = settings().project_dir(project_id) / "preview_proxy.mp4"
    if not path.exists():
        raise HTTPException(404, "preview not ready")
    return FileResponse(path, media_type="video/mp4")


@router.get("/{project_id}/thumb/{event_id}")
def event_thumb(project_id: str, event_id: str) -> FileResponse:
    """Timeline thumbnail: one frame from the event's chosen source moment."""
    import subprocess

    from ..db import session_scope
    from ..media.probe import ffmpeg
    from ..models import Asset

    seg_dir = settings().project_dir(project_id) / "segments"
    seg_dir.mkdir(exist_ok=True)
    cache = seg_dir / f"thumb_{event_id}.jpg"
    if not cache.exists():
        edl = load_edl(project_id)
        ev = next((e for e in edl["events"] if e["id"] == event_id), None)
        if not ev or not ev.get("asset_id"):
            raise HTTPException(404, "no footage for this event")
        with session_scope() as db:
            asset = db.get(Asset, ev["asset_id"])
        if not asset:
            raise HTTPException(404, "asset missing")
        t = float((ev.get("source") or {}).get("in_s", 0.0)) + 0.4
        proc = subprocess.run(
            [ffmpeg(), "-y", "-ss", f"{t:.2f}", "-i", asset.file_path,
             "-frames:v", "1", "-vf", "scale=240:-2", "-q:v", "6", str(cache)],
            capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 or not cache.exists():
            raise HTTPException(404, "thumb extraction failed")
    return FileResponse(cache, media_type="image/jpeg")


class ExportBody(BaseModel):
    resolution: str = "1080p"


@router.post("/{project_id}/export")
async def export(project_id: str, body: ExportBody) -> dict:
    job_id = await runner.submit("export", project_id=project_id,
                                 payload={"resolution": body.resolution})
    return {"job_id": job_id}


@router.get("/{project_id}/export/download")
def export_download(project_id: str) -> FileResponse:
    path = settings().project_dir(project_id) / "export.mp4"
    if not path.exists():
        raise HTTPException(404, "export not ready")
    return FileResponse(path, media_type="video/mp4",
                        filename=f"voxcut_{project_id}.mp4")
