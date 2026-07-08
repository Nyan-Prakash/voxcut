"""Candidate moments + alternate sources for the editor strip (spec §9.7, §11.2)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..edl_store import load_edl, save_edl
from ..jobs.runner import runner

router = APIRouter(prefix="/api/projects", tags=["candidates"])


@router.get("/{project_id}/candidates/{event_id}")
def get_candidates(project_id: str, event_id: str) -> dict:
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    return {
        "event_id": event_id,
        "asset_id": ev.get("asset_id"),
        "chosen_source": ev.get("source"),
        "moment_candidates": ev.get("moment_candidates", []),
        "source_candidates": ev.get("source_candidates", []),
        "flags": ev.get("flags", []),
    }


class PickMoment(BaseModel):
    in_s: float
    out_s: float


@router.post("/{project_id}/candidates/{event_id}/pick_moment")
def pick_moment(project_id: str, event_id: str, body: PickMoment) -> dict:
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    src = ev.get("source") or {}
    src.update({"in_s": body.in_s, "out_s": body.out_s})
    ev["source"] = src
    ev["flags"] = [f for f in ev.get("flags", []) if f != "needs_review"]
    # Invalidate this event's cached proxy segment (§11.2).
    from ..config import settings
    seg = settings().project_dir(project_id) / "segments"
    for ext in (".mp4", ".ass"):
        (seg / f"{event_id}{ext}").unlink(missing_ok=True)
    save_edl(project_id, edl)
    return {"ok": True, "source": ev["source"]}


class ReSource(BaseModel):
    query: str


@router.post("/{project_id}/candidates/{event_id}/research")
async def research(project_id: str, event_id: str, body: ReSource) -> dict:
    """Re-run sourcing for a single event with a new query (Search again)."""
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    ev["queries"] = [body.query] + [q for q in ev.get("queries", []) if q != body.query]
    if ev["kind"] == "caption_card":
        ev["kind"] = "clip_literal"
    ev["asset_id"] = None
    ev["source"] = None
    save_edl(project_id, edl)
    job_id = await runner.submit("source", project_id=project_id,
                                 payload={"only_event": event_id})
    return {"job_id": job_id}
