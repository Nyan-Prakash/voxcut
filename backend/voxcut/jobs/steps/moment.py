"""Moment-selection job (M5): pick the exact seconds within each sourced asset."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl, save_edl
from ...models import Asset
from ...moments.select import intent_for, select_moments
from ..runner import JobContext, register


@register("moment")
async def run_moment(ctx: JobContext) -> None:
    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    edl = load_edl(project_id)

    beats_path = pdir / "beats.json"
    beats = ({b["id"]: b for b in json.loads(beats_path.read_text())["beats"]}
             if beats_path.exists() else {})

    only = ctx.payload.get("only_event")
    events = [e for e in edl["events"] if e.get("asset_id") and not e.get("locked")
              and (only is None or e["id"] == only)]
    step = ctx.add_step("moment")
    await ctx.report(step, 0.02, f"Selecting moments for {len(events)} clips")
    if not events:
        await ctx.finish_step(step, "no clips to place")
        return

    done = {"n": 0}
    reviewed = 0
    for ev in events:
        try:
            await asyncio.to_thread(_place_one, ev, beats)
        except Exception as exc:  # noqa: BLE001
            ev.setdefault("flags", []).append(f"moment_error:{type(exc).__name__}")
        if "needs_review" in ev.get("flags", []):
            reviewed += 1
        done["n"] += 1
        await ctx.report(step, done["n"] / len(events),
                         f"Placed {done['n']}/{len(events)}")

    save_edl(project_id, edl)
    await ctx.finish_step(step, f"{len(events)} placed, {reviewed} need review")


def _place_one(ev: dict, beats: dict) -> None:
    with session_scope() as db:
        asset = db.get(Asset, ev["asset_id"])
    if not asset or not Path(asset.file_path).exists():
        return

    beat = beats.get(ev.get("beat_id"), {})
    query = " ".join(filter(None, [
        beat.get("gist", ""), beat.get("text", ""),
        " ".join(ev.get("queries", [])),
        (ev.get("caption") or {}).get("text", ""),
    ])).strip() or (ev.get("queries") or ["clip"])[0]
    entities = beat.get("concrete_entities", [])

    cache_dir = settings().library_dir / asset.source_id
    beat_dur = ev["end_s"] - ev["start_s"]
    moments, conf = select_moments(
        video=Path(asset.file_path), cache_dir=cache_dir,
        duration=asset.duration_s or 0.0, beat_query=query, entities=entities,
        intent=intent_for(ev["kind"]), beat_dur=beat_dur,
        subs_path=Path(asset.subs_path) if asset.subs_path else None,
        heatmap_path=Path(asset.heatmap_path) if asset.heatmap_path else None,
    )
    best = moments[0]
    ev["source"] = {"in_s": best.in_s, "out_s": best.out_s,
                    "chosen_rank": 1, "confidence": conf}
    ev["moment_candidates"] = [m.to_dict() for m in moments]
    flags = [f for f in ev.get("flags", []) if f != "needs_review"]
    from ...moments.select import CONF_THRESHOLD
    if conf < CONF_THRESHOLD:
        flags.append("needs_review")
    ev["flags"] = flags
