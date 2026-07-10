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
    sem = asyncio.Semaphore(3)  # vision calls + occasional escalation downloads

    async def handle(ev: dict) -> None:
        try:
            async with sem:
                await asyncio.to_thread(_place_one, ev, beats)
        except Exception as exc:  # noqa: BLE001
            ev.setdefault("flags", []).append(f"moment_error:{type(exc).__name__}")
        finally:
            done["n"] += 1
            await ctx.report(step, done["n"] / len(events),
                             f"Placed {done['n']}/{len(events)}")

    await asyncio.gather(*(handle(ev) for ev in events))
    reviewed = sum(1 for e in events if "needs_review" in e.get("flags", []))
    save_edl(project_id, edl)
    await ctx.finish_step(step, f"{len(events)} placed, {reviewed} need review")


# Below this vision score, the chosen asset's footage doesn't show the gag —
# try the next judge-approved source before settling. (Talking-head explainers
# measure ~0.3-0.4; real visual gags 0.7+.)
ESCALATE_BELOW = 0.45
REVIEW_BELOW = 0.5
VISION_WEIGHT = 0.7  # final window score = 0.7*vision + 0.3*signal fusion


def _signal_moments(ev: dict, asset: Asset, query: str, entities: list[str]):
    cache_dir = settings().library_dir / asset.source_id
    beat_dur = ev["end_s"] - ev["start_s"]
    return select_moments(
        video=Path(asset.file_path), cache_dir=cache_dir,
        duration=asset.duration_s or 0.0, beat_query=query, entities=entities,
        intent=intent_for(ev["kind"]), beat_dur=beat_dur,
        subs_path=Path(asset.subs_path) if asset.subs_path else None,
        heatmap_path=Path(asset.heatmap_path) if asset.heatmap_path else None,
    )


def _verify_visually(ev: dict, asset: Asset, beat: dict, moments) -> list[float] | None:
    """Frame-judge the candidate windows. None = vision unavailable/failed."""
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_frames
    from ...moments.frames import sample_window_frames

    if not is_available():
        return None
    windows = [(m.in_s, m.out_s) for m in moments]
    urls = sample_window_frames(
        Path(asset.file_path), windows,
        settings().library_dir / asset.source_id / "verify")
    present = [(i, u) for i, u in enumerate(urls) if u]
    if not present:
        return None
    try:
        scores_present = judge_frames(
            beat.get("text") or beat.get("gist", ""), ev["kind"],
            asset.title or "", [u for _i, u in present])
    except BrainError:
        return None
    scores = [0.0] * len(moments)
    for (i, _u), s in zip(present, scores_present):
        scores[i] = s
    return scores


def _try_asset(ev: dict, asset: Asset, beat: dict, query: str,
               entities: list[str]) -> dict:
    """Score one asset: signal windows + visual verification. Returns a result
    dict with the best window and its vision score (None if vision was off)."""
    moments, conf = _signal_moments(ev, asset, query, entities)
    vision = _verify_visually(ev, asset, beat, moments)
    if vision is None:
        order = list(range(len(moments)))
        best_vision = None
    else:
        blended = [VISION_WEIGHT * v + (1 - VISION_WEIGHT) * m.score
                   for v, m in zip(vision, moments)]
        order = sorted(range(len(moments)), key=lambda i: blended[i], reverse=True)
        best_vision = vision[order[0]]
    cands = []
    for i in order:
        d = moments[i].to_dict()
        if vision is not None:
            d["visual"] = round(vision[i], 3)
        cands.append(d)
    return {"asset": asset, "moments": cands, "conf": conf,
            "best_vision": best_vision}


def _next_unused_source(ev: dict, current_source_id: str):
    """Next judge-approved source candidate ≠ the current asset (for escalation)."""
    for c in ev.get("source_candidates", []):
        if c.get("source_id") and c["source_id"] != current_source_id and c.get("url"):
            return c
    return None


def _fetch_candidate(cand: dict) -> Asset | None:
    from ...library.index import find_asset, record_asset
    from ...sourcing.youtube import YouTubeProvider
    with session_scope() as db:
        existing = find_asset(db, "youtube", cand["source_id"])
        if existing:
            return existing
    try:
        meta = YouTubeProvider().fetch_url(
            cand["url"], settings().library_dir / cand["source_id"])
    except Exception:  # noqa: BLE001
        return None
    with session_scope() as db:
        return record_asset(db, meta, [])


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

    result = _try_asset(ev, asset, beat, query, entities)

    # Closed loop: if the footage verifiably doesn't show the gag, try the
    # next judge-approved source once and keep whichever verifies better.
    if (result["best_vision"] is not None
            and result["best_vision"] < ESCALATE_BELOW):
        nxt = _next_unused_source(ev, asset.source_id)
        alt_asset = _fetch_candidate(nxt) if nxt else None
        if alt_asset and Path(alt_asset.file_path).exists():
            alt = _try_asset(ev, alt_asset, beat, query, entities)
            if (alt["best_vision"] or 0) > result["best_vision"]:
                result = alt
                ev["asset_id"] = alt_asset.id

    best = result["moments"][0]
    src = {"in_s": best["in_s"], "out_s": best["out_s"], "chosen_rank": 1,
           "confidence": result["conf"]}
    if result["best_vision"] is not None:
        src["visual"] = round(result["best_vision"], 3)
    ev["source"] = src
    ev["moment_candidates"] = result["moments"]

    flags = [f for f in ev.get("flags", []) if f != "needs_review"]
    from ...moments.select import CONF_THRESHOLD
    needs_review = (result["best_vision"] < REVIEW_BELOW
                    if result["best_vision"] is not None
                    else result["conf"] < CONF_THRESHOLD)
    if needs_review:
        flags.append("needs_review")
    ev["flags"] = flags
