"""Candidate moments + alternate sources for the editor strip (spec §9.7, §11.2)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..edl_store import load_edl, save_edl
from ..jobs.runner import runner

router = APIRouter(prefix="/api/projects", tags=["candidates"])


def _drop_render_cache(project_id: str, event_id: str) -> None:
    """The event's footage changed: cached segment renders and the timeline
    thumbnail are stale — delete so they regenerate from the new source."""
    from ..config import settings
    for sub in ("segments", "segments_full"):
        seg_dir = settings().project_dir(project_id) / sub
        for name in (f"{event_id}.mp4", f"{event_id}.ass", f"thumb_{event_id}.jpg"):
            (seg_dir / name).unlink(missing_ok=True)


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
        "finalists": ev.get("finalists", []),
        "flags": ev.get("flags", []),
    }


class PickFinalist(BaseModel):
    asset_id: str
    in_s: float
    out_s: float


@router.post("/{project_id}/candidates/{event_id}/pick_finalist")
def pick_finalist(project_id: str, event_id: str, body: PickFinalist) -> dict:
    """Swap the event to a tournament finalist (already downloaded — instant)."""
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    ev["asset_id"] = body.asset_id
    src = ev.get("source") or {}
    src.update({"in_s": body.in_s, "out_s": body.out_s})
    ev["source"] = src
    ev["flags"] = [f for f in ev.get("flags", [])
                   if f not in ("close_call", "needs_review")]
    _drop_render_cache(project_id, event_id)
    save_edl(project_id, edl)
    return {"ok": True, "asset_id": body.asset_id, "source": ev["source"]}


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
    _drop_render_cache(project_id, event_id)
    save_edl(project_id, edl)
    return {"ok": True, "source": ev["source"]}


class RerollOneBody(BaseModel):
    hint: str | None = None  # optional operator direction for the new clip


class RerollBody(BaseModel):
    event_ids: list[str]
    hint: str | None = None


@router.post("/{project_id}/events/{event_id}/reroll")
async def reroll_one(project_id: str, event_id: str,
                     body: RerollOneBody | None = None) -> dict:
    """Regenerate one clip from scratch: re-plan the beat (fresh angle +
    queries), re-run the tournament, excluding the current footage.
    Optional hint steers the re-plan ("make this the anime version")."""
    edl = load_edl(project_id)
    if not any(e["id"] == event_id for e in edl["events"]):
        raise HTTPException(404, "event not found")
    job_id = await runner.submit("reroll", project_id=project_id,
                                 payload={"only_events": [event_id],
                                          "hint": (body.hint if body else None)})
    return {"job_id": job_id}


@router.post("/{project_id}/events/reroll")
async def reroll_many(project_id: str, body: RerollBody) -> dict:
    """Regenerate several clips in one job (multi-select reroll). An optional
    hint applies to every selected clip."""
    edl = load_edl(project_id)
    known = {e["id"] for e in edl["events"]}
    ids = [i for i in body.event_ids if i in known]
    if not ids:
        raise HTTPException(404, "no matching events")
    job_id = await runner.submit("reroll", project_id=project_id,
                                 payload={"only_events": ids, "hint": body.hint})
    return {"job_id": job_id}


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
    _drop_render_cache(project_id, event_id)
    save_edl(project_id, edl)
    job_id = await runner.submit("source", project_id=project_id,
                                 payload={"only_event": event_id})
    return {"job_id": job_id}
