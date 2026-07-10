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

    # Beat narration text — the judge scores candidates against what's SAID.
    # Punchline beats are marked so judges can match visual energy (§playbook).
    beats_path = settings().project_dir(project_id) / "beats.json"
    beat_text = {}
    if beats_path.exists():
        for b in json.loads(beats_path.read_text())["beats"]:
            txt = b.get("text") or b.get("gist", "")
            if b.get("emphasis", 0) >= 0.7:
                txt += " [PUNCHLINE beat — wants a chaotic, high-energy visual]"
            elif b.get("emphasis", 1) < 0.4:
                txt += " [setup beat — calm/medium footage is right]"
            beat_text[b["id"]] = txt

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
    used_sources: set[str] = set()  # variety guard: one asset per run

    async def handle(ev: dict) -> None:
        reaction = ev["kind"] == "clip_reaction"
        filters = Filters(avoid=avoid, reaction_intent=reaction)
        try:
            async with sem:
                asset_id, source, candidates = await asyncio.to_thread(
                    _source_one, project_id, ev, provider, filters,
                    beat_text.get(ev.get("beat_id"), ""), used_sources)
            if asset_id:
                ev["asset_id"] = asset_id
                ev["source"] = source
                ev["source_candidates"] = candidates
                ev["flags"] = [f for f in ev.get("flags", []) if f != "gap_unfilled"]
            else:
                _mark_gap(ev, beat_text.get(ev.get("beat_id"), ""))
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the job
            ev.setdefault("flags", []).append(f"source_error:{type(exc).__name__}")
            _mark_gap(ev, beat_text.get(ev.get("beat_id"), ""))
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


def _mark_gap(ev: dict, beat_text: str = "") -> None:
    """Unsourceable beat. The renderer extends the neighboring clip through the
    gap (no text card on screen — operator preference). The stored caption is a
    last-resort only, used when there is no neighboring clip to extend."""
    ev["kind"] = "caption_card"
    ev.setdefault("flags", [])
    if "gap_unfilled" not in ev["flags"]:
        ev["flags"].append("gap_unfilled")
    if not ev["caption"].get("text"):
        words = beat_text.split()
        text = " ".join(words[:10]) + ("…" if len(words) > 10 else "")
        ev["caption"]["text"] = text or "—"
        ev["caption"]["style"] = "card"
    ev["caption"]["enabled"] = False  # no caption-track entry, no burned text


def _source_one(project_id: str, ev: dict, provider, filters: Filters,
                beat_text: str = "", used_sources: set[str] | None = None):
    """Blocking: search ALL queries → merge → rank → LLM relevance judge →
    reuse-or-download. Returns (asset_id, source, cands).

    The judge is the quality gate: a candidate that merely keyword-matches gets
    rejected, and rejecting everything (→ caption card) is a valid outcome —
    better than shipping a random clip."""
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_candidates
    from ...sourcing.rank import rank

    queries = [q for q in ev.get("queries", []) if q.strip()][:3]
    if not queries:
        return None, None, []

    # Search every query the planner wrote; merge + dedupe by source_id.
    merged: dict[str, object] = {}
    for q in queries:
        try:
            for c in provider.search(q, SEARCH_N, filters):
                if c.source_id not in merged:
                    merged[c.source_id] = c
        except Exception:  # noqa: BLE001 — a failed search shouldn't kill the event
            continue
    if not merged:
        return None, None, []

    # Heuristic rank (embeddings + metadata) against the primary query.
    ranked = rank(queries[0], list(merged.values()), filters)[:10]

    # LLM judge: score actual relevance to the narration. Order = judge order.
    order = ranked
    judged_by_id: dict[str, float] = {}
    if is_available() and beat_text:
        try:
            picks = judge_candidates(
                beat_text, ev["kind"], queries,
                [{"title": c.title, "channel": c.channel,
                  "duration_s": c.duration_s, "views": c.view_count,
                  "thumbnail": c.thumbnail}
                 for c in ranked])
            order = [ranked[i] for i, _rel in picks]
            judged_by_id = {ranked[i].source_id: rel for i, rel in picks}
        except BrainError:
            pass  # judge unavailable → fall back to heuristic order

    cand_meta = [{"source_id": c.source_id, "title": c.title,
                  "score": round(judged_by_id.get(c.source_id, c.score), 3),
                  "url": c.url, "thumbnail": c.thumbnail,
                  "duration_s": c.duration_s}
                 for c in (order or ranked)[:5]]
    if not order:
        return None, None, cand_meta  # judge rejected everything → caption card

    # Variety guard: don't reuse a source already placed in this run — the same
    # clip on adjacent beats reads as a glitch. Prefer fresh; reuse only if
    # every approved candidate is taken.
    if used_sources is not None:
        fresh = [c for c in order if c.source_id not in used_sources]
        order = fresh or order

    lib_dir = settings().library_dir
    for cand in order[:4]:
        with session_scope() as db:
            existing = find_asset(db, cand.provider, cand.source_id)
            if existing:
                touch(db, existing.id)
                if used_sources is not None:
                    used_sources.add(cand.source_id)
                return existing.id, _naive_source(ev, existing.duration_s), cand_meta
        try:
            meta = provider.fetch(cand, lib_dir / cand.source_id)
        except Exception:  # noqa: BLE001 — try the next approved candidate
            continue
        with session_scope() as db:
            asset = record_asset(db, meta, queries)
            if used_sources is not None:
                used_sources.add(cand.source_id)
            return asset.id, _naive_source(ev, asset.duration_s), cand_meta
    return None, None, cand_meta


def _naive_source(ev: dict, asset_dur: float) -> dict:
    beat_dur = ev["end_s"] - ev["start_s"]
    out = min(asset_dur, beat_dur) if asset_dur else beat_dur
    return {"in_s": 0.0, "out_s": round(out, 3), "chosen_rank": 1}
