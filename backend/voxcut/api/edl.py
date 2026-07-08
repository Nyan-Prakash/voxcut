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
    op: str          # set_caption | set_treatment | set_audio | set_kind | delete | reorder | lock
    event_id: str | None = None
    data: dict = {}


class OpsBody(BaseModel):
    base_version: int | None = None
    ops: list[Op]


_ALLOWED = {"set_caption", "set_treatment", "set_audio", "set_kind",
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
        if op.op == "set_caption":
            ev["caption"].update(op.data)
        elif op.op == "set_treatment":
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
