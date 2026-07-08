"""Sourcing job (M4): fill EDL events with real footage from providers.

Per sourcing event: reuse a library asset if we already have one, else search →
rank → download the top candidate. Naive in/out here (start of clip); moment
selection (M5) refines the exact seconds. Failures degrade to gap_unfilled (NFR5).
"""
from __future__ import annotations

import asyncio
import json

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl, save_edl
from ...library.index import find_asset, record_asset, touch
from ...models import Project
from ...sourcing.base import Filters
from ...sourcing.youtube import YouTubeProvider
from ..runner import JobContext, register

SOURCING_KINDS = {"clip_literal", "clip_reaction", "meme_image", "broll"}
DOWNLOAD_CONCURRENCY = 3
SEARCH_N = 8


@register("source")
async def run_source(ctx: JobContext) -> None:
    project_id = ctx.project_id
    edl = load_edl(project_id)
    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
    avoid = brief.get("avoid", [])

    only = ctx.payload.get("only_event")
    events = [e for e in edl["events"]
              if e["kind"] in SOURCING_KINDS and e.get("queries") and not e.get("locked")
              and (only is None or e["id"] == only)]
    step = ctx.add_step("source")
    await ctx.report(step, 0.02, f"Sourcing {len(events)} clips")
    if not events:
        await ctx.finish_step(step, "nothing to source")
        return

    provider = YouTubeProvider()
    sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
    done = {"n": 0}

    async def handle(ev: dict) -> None:
        reaction = ev["kind"] == "clip_reaction"
        filters = Filters(avoid=avoid, reaction_intent=reaction)
        try:
            async with sem:
                asset_id, source, candidates = await asyncio.to_thread(
                    _source_one, project_id, ev, provider, filters)
            if asset_id:
                ev["asset_id"] = asset_id
                ev["source"] = source
                ev["source_candidates"] = candidates
                ev["flags"] = [f for f in ev.get("flags", []) if f != "gap_unfilled"]
            else:
                _mark_gap(ev)
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the job
            ev.setdefault("flags", []).append(f"source_error:{type(exc).__name__}")
            _mark_gap(ev)
        finally:
            done["n"] += 1
            await ctx.report(step, done["n"] / len(events),
                             f"Sourced {done['n']}/{len(events)}")

    await asyncio.gather(*(handle(ev) for ev in events))
    save_edl(project_id, edl)
    filled = sum(1 for e in events if e.get("asset_id"))
    await ctx.finish_step(step, f"{filled}/{len(events)} clips sourced")

    # Single-event re-source (Search again): place its moment + rebuild preview.
    if only and filled:
        from .assemble import run_assemble
        from .moment import run_moment
        await run_moment(ctx)
        await run_assemble(ctx)


def _mark_gap(ev: dict) -> None:
    ev["kind"] = "caption_card"  # fallback so it still renders (NFR5)
    ev.setdefault("flags", [])
    if "gap_unfilled" not in ev["flags"]:
        ev["flags"].append("gap_unfilled")
    if not ev["caption"].get("text"):
        q = (ev.get("queries") or ["clip"])[0]
        ev["caption"] = {"text": q, "style": "card", "enabled": True}


def _source_one(project_id: str, ev: dict, provider, filters: Filters):
    """Blocking: search + rank + reuse-or-download. Returns (asset_id, source, cands)."""
    from ...sourcing.rank import rank

    query = ev["queries"][0]
    candidates = provider.search(query, SEARCH_N, filters)
    ranked = rank(query, candidates, filters)[:5]
    cand_meta = [{"source_id": c.source_id, "title": c.title, "score": c.score,
                  "url": c.url, "thumbnail": c.thumbnail,
                  "duration_s": c.duration_s} for c in ranked]
    if not ranked:
        return None, None, cand_meta

    lib_dir = settings().library_dir
    for cand in ranked:
        with session_scope() as db:
            existing = find_asset(db, cand.provider, cand.source_id)
            if existing:
                touch(db, existing.id)
                return existing.id, _naive_source(ev, existing.duration_s), cand_meta
        # Download the top viable candidate.
        try:
            meta = provider.fetch(cand, lib_dir / cand.source_id)
        except Exception:  # noqa: BLE001 — try next candidate
            continue
        with session_scope() as db:
            asset = record_asset(db, meta, ev["queries"])
            return asset.id, _naive_source(ev, asset.duration_s), cand_meta
    return None, None, cand_meta


def _naive_source(ev: dict, asset_dur: float) -> dict:
    beat_dur = ev["end_s"] - ev["start_s"]
    out = min(asset_dur, beat_dur) if asset_dur else beat_dur
    return {"in_s": 0.0, "out_s": round(out, 3), "chosen_rank": 1}
