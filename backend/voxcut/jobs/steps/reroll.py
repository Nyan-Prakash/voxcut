"""Reroll job: regenerate individual clips from scratch.

Unlike "Search again" (which reuses a hand-typed query), reroll re-plans the
beat with the LLM — fresh comedic angle, fresh queries — then re-runs the full
tournament (source → moment → assemble) for just those events. The footage
being replaced is excluded, so a reroll always lands on something new.
"""
from __future__ import annotations

import json

from ...brain.client import BrainError, is_available
from ...db import session_scope
from ...edl_store import load_edl, save_edl
from ...models import Asset, Project
from ..runner import JobContext, register
from .source import _only_ids, run_source

RESET_KEYS = ("moment_candidates", "finalists", "source_candidates",
              "finalist_asset_ids")


@register("reroll")
async def run_reroll(ctx: JobContext) -> None:
    project_id = ctx.project_id
    only = _only_ids(ctx.payload)
    if not only:
        raise RuntimeError("reroll needs only_event(s)")

    edl = load_edl(project_id)
    events = [e for e in edl["events"] if e["id"] in only and not e.get("locked")]
    step = ctx.add_step("reroll")
    if not events:
        await ctx.finish_step(step, "nothing to reroll (locked or missing)")
        return

    from ...config import settings
    beats_path = settings().project_dir(project_id) / "beats.json"
    beats = ({b["id"]: b for b in json.loads(beats_path.read_text())["beats"]}
             if beats_path.exists() else {})
    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}

    # Collect the footage being replaced: excluded from the new tournament
    # and named in the planner's avoid list.
    avoid_ids: list[str] = list(ctx.payload.get("avoid_source_ids") or [])
    avoid_titles: list[str] = []
    for ev in events:
        if not ev.get("asset_id"):
            continue
        with session_scope() as db:
            a = db.get(Asset, ev["asset_id"])
        if a:
            avoid_ids.append(a.source_id)
            if a.title:
                avoid_titles.append(a.title)

    hint = (ctx.payload.get("hint") or "").strip() or None

    # Interject clips reroll through their own audio-forward pipeline: the
    # gap length is FIXED (no cascading re-shifts), the replacement moment is
    # cut to fit, and a failed hunt keeps the current clip.
    from ...timeline_ops import INTERJECT_FLAG
    interject_evs = [e for e in events if INTERJECT_FLAG in (e.get("flags") or [])]
    events = [e for e in events if e not in interject_evs]
    swapped = 0
    if interject_evs:
        import asyncio

        from .interject import source_interject_clip
        await ctx.report(step, 0.05,
                         f"Re-hunting {len(interject_evs)} interjection(s)")
        for ev in interject_evs:
            try:
                result = await asyncio.to_thread(
                    source_interject_clip, project_id, ev, hint,
                    set(avoid_ids), ev["end_s"] - ev["start_s"])
            except Exception:  # noqa: BLE001 — keep the current clip
                result = None
            if not result:
                continue
            ev["asset_id"] = result["asset_id"]
            ev["source"] = {"in_s": result["in_s"], "out_s": result["out_s"],
                            "chosen_rank": 1, "visual": result["vision"]}
            ev["interject"] = {"intent": result["intent"],
                               "vision": result["vision"]}
            ev.pop("qc", None)
            swapped += 1
            for sub in ("segments", "segments_full"):
                seg_dir = settings().project_dir(project_id) / sub
                (seg_dir / f"{ev['id']}.mp4").unlink(missing_ok=True)
                (seg_dir / f"thumb_{ev['id']}.jpg").unlink(missing_ok=True)
        if not events:
            save_edl(project_id, edl)
            await ctx.finish_step(
                step, f"{swapped}/{len(interject_evs)} interjection(s) swapped")
            from .assemble import run_assemble
            await run_assemble(ctx)
            return

    await ctx.report(step, 0.1, f"Re-planning {len(events)} beats"
                     + (" (with your direction)" if hint else ""))
    replanned = 0
    for ev in events:
        beat = beats.get(ev.get("beat_id"))
        if beat and is_available():
            try:
                from ...brain.plan import plan_one
                fresh = plan_one(beat, brief, avoid_extra=avoid_titles, hint=hint)
                for key in ("kind", "queries", "joke_queries", "audio"):
                    ev[key] = fresh[key]
                replanned += 1
            except BrainError:
                if hint:  # no LLM: the direction becomes the search itself
                    ev["queries"] = [hint]
                    ev["joke_queries"] = []
        elif hint:
            ev["queries"] = [hint]
            ev["joke_queries"] = []
        ev["asset_id"] = None
        ev["source"] = None
        for key in RESET_KEYS:
            ev.pop(key, None)
        ev.pop("qc", None)  # old verdict is about the old footage
        ev["flags"] = [f for f in ev.get("flags", [])
                       if f not in ("needs_review", "close_call",
                                    "gap_unfilled", "qc_middle",
                                    "cold_open_weak")]
        # The footage is changing: drop the cached segment + timeline
        # thumbnail so both regenerate from the new clip.
        for sub in ("segments", "segments_full"):
            seg_dir = settings().project_dir(project_id) / sub
            (seg_dir / f"{ev['id']}.mp4").unlink(missing_ok=True)
            (seg_dir / f"thumb_{ev['id']}.jpg").unlink(missing_ok=True)
    save_edl(project_id, edl)
    await ctx.finish_step(step, f"{replanned}/{len(events)} beats re-planned")

    # source → moment → assemble, limited to these events (source chains
    # them). Interject events already swapped above — keep the visual moment
    # pipeline away from them.
    ctx.payload["only_events"] = sorted(e["id"] for e in events)
    ctx.payload.pop("only_event", None)
    ctx.payload["avoid_source_ids"] = sorted(set(avoid_ids))
    await run_source(ctx)
