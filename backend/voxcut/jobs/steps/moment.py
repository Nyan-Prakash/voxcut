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

    from .source import _only_ids
    only = _only_ids(ctx.payload)
    events = [e for e in edl["events"] if e.get("asset_id") and not e.get("locked")
              and (only is None or e["id"] in only)]
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
VISION_WEIGHT = 0.7   # final window score = 0.7*vision + 0.3*signal fusion
CLOSE_CALL_GAP = 0.1  # finalists within this vision margin → flag for review
COARSE_MAX_FRAMES = 20
COARSE_KEEP = 0.4     # coarse regions below this score aren't worth refining


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


def _judge_windows(ev: dict, asset: Asset, beat: dict,
                   windows: list[tuple[float, float]],
                   work_tag: str) -> list[float] | None:
    """Frame-judge arbitrary windows. None = vision unavailable/failed."""
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_frames
    from ...moments.frames import sample_window_frames

    if not is_available() or not windows:
        return None
    urls = sample_window_frames(
        Path(asset.file_path), windows,
        settings().library_dir / asset.source_id / "verify" / f"{ev['id']}_{work_tag}")
    present = [(i, u) for i, u in enumerate(urls) if u]
    if not present:
        return None
    try:
        scores_present = judge_frames(
            beat.get("text") or beat.get("gist", ""), ev["kind"],
            asset.title or "", [u for _i, u in present])
    except BrainError:
        return None
    scores = [0.0] * len(windows)
    for (i, _u), s in zip(present, scores_present):
        scores[i] = s
    return scores


def _coarse_windows(ev: dict, asset: Asset, beat: dict,
                    beat_dur: float) -> list[tuple[float, float]]:
    """Stage 1: scan frames across the ENTIRE video to find where the gag
    lives, independent of subtitle/heatmap/audio signals. Fixes the
    'window misses the gag entirely' failure — weak signals can no longer
    strand the search in the wrong region."""
    dur = asset.duration_s or 0.0
    if dur <= beat_dur * 4:  # short clip — fine windows already cover it
        return []
    n = min(COARSE_MAX_FRAMES, max(6, int(dur // 8)))
    step = dur / n
    windows = []
    for i in range(n):
        c = step / 2 + i * step
        windows.append((max(0.0, c - beat_dur / 2), min(dur, c + beat_dur / 2)))
    scores = _judge_windows(ev, asset, beat, windows, "coarse")
    if scores is None:
        return []
    top = sorted(range(len(windows)), key=lambda i: scores[i], reverse=True)[:3]
    return [windows[i] for i in top if scores[i] >= COARSE_KEEP]


def _try_asset(ev: dict, asset: Asset, beat: dict, query: str,
               entities: list[str]) -> dict:
    """Score one asset: coarse whole-video vision scan + signal windows →
    fine frame verification. Returns the best window + its vision score."""
    from ...moments.scenes import detect_scenes, snap_to_scenes

    beat_dur = ev["end_s"] - ev["start_s"]
    moments, conf = _signal_moments(ev, asset, query, entities)
    sig_windows = [(m.in_s, m.out_s, m.score) for m in moments[:3]]

    coarse = _coarse_windows(ev, asset, beat, beat_dur)
    scenes = detect_scenes(Path(asset.file_path),
                           settings().library_dir / asset.source_id / "scenes.json")
    coarse_snapped = [(*snap_to_scenes(a, b, scenes, tolerance=beat_dur * 0.25), 0.0)
                      for (a, b) in coarse]

    # Merge candidate windows (coarse first — they came from real pixels),
    # dropping near-duplicates.
    merged: list[tuple[float, float, float]] = []
    for w in coarse_snapped + sig_windows:
        if all(abs(w[0] - m[0]) > beat_dur * 0.5 for m in merged):
            merged.append(w)
    merged = merged[:6]
    if not merged:
        merged = [(0.0, min(asset.duration_s or beat_dur, beat_dur), 0.0)]

    vision = _judge_windows(ev, asset, beat, [(a, b) for a, b, _s in merged], "fine")
    if vision is None:
        order = list(range(len(merged)))
        best_vision = None
    else:
        blended = [VISION_WEIGHT * v + (1 - VISION_WEIGHT) * s
                   for v, (_a, _b, s) in zip(vision, merged)]
        order = sorted(range(len(merged)), key=lambda i: blended[i], reverse=True)
        best_vision = vision[order[0]]

    cands = []
    for i in order:
        a, b, s = merged[i]
        d = {"in_s": round(a, 3), "out_s": round(b, 3), "score": round(s, 4)}
        if vision is not None:
            d["visual"] = round(vision[i], 3)
        cands.append(d)
    return {"asset": asset, "moments": cands, "conf": conf,
            "best_vision": best_vision}


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


def _score_of(result: dict) -> float:
    if result["best_vision"] is not None:
        return result["best_vision"]
    return result["moments"][0].get("score", 0.0) if result["moments"] else 0.0


def _place_one(ev: dict, beats: dict) -> None:
    beat = beats.get(ev.get("beat_id"), {})
    query = " ".join(filter(None, [
        beat.get("gist", ""), beat.get("text", ""),
        " ".join(ev.get("queries", [])),
    ])).strip() or (ev.get("queries") or ["clip"])[0]
    entities = beat.get("concrete_entities", [])

    # Tournament: verify every downloaded finalist (both comedic angles).
    finalist_ids = list(dict.fromkeys(
        ev.get("finalist_asset_ids") or
        ([ev["asset_id"]] if ev.get("asset_id") else [])))
    results: list[dict] = []
    for aid in finalist_ids:
        with session_scope() as db:
            asset = db.get(Asset, aid)
        if not asset or not Path(asset.file_path).exists():
            continue
        try:
            results.append(_try_asset(ev, asset, beat, query, entities))
        except Exception:  # noqa: BLE001 — a broken finalist forfeits
            continue
    if not results:
        return

    results.sort(key=_score_of, reverse=True)
    winner = results[0]

    # Escalation net: if even the tournament winner verifiably misses, try one
    # more judge-approved source before settling.
    if (winner["best_vision"] is not None
            and winner["best_vision"] < ESCALATE_BELOW):
        tried = {r["asset"].source_id for r in results}
        nxt = next((c for c in ev.get("source_candidates", [])
                    if c.get("source_id") and c["source_id"] not in tried
                    and c.get("url")), None)
        alt_asset = _fetch_candidate(nxt) if nxt else None
        if alt_asset and Path(alt_asset.file_path).exists():
            try:
                alt = _try_asset(ev, alt_asset, beat, query, entities)
                results.append(alt)
                results.sort(key=_score_of, reverse=True)
                winner = results[0]
            except Exception:  # noqa: BLE001
                pass

    ev["asset_id"] = winner["asset"].id
    best = winner["moments"][0]
    src = {"in_s": best["in_s"], "out_s": best["out_s"], "chosen_rank": 1,
           "confidence": winner["conf"]}
    if winner["best_vision"] is not None:
        src["visual"] = round(winner["best_vision"], 3)
    ev["source"] = src
    ev["moment_candidates"] = winner["moments"]
    ev["finalists"] = [{
        "asset_id": r["asset"].id,
        "title": r["asset"].title,
        "in_s": r["moments"][0]["in_s"],
        "out_s": r["moments"][0]["out_s"],
        "visual": r["best_vision"],
    } for r in results[:3]]

    flags = [f for f in ev.get("flags", [])
             if f not in ("needs_review", "close_call")]
    from ...moments.select import CONF_THRESHOLD
    needs_review = (winner["best_vision"] < REVIEW_BELOW
                    if winner["best_vision"] is not None
                    else winner["conf"] < CONF_THRESHOLD)
    if needs_review:
        flags.append("needs_review")
    # Close call: two finalists verified nearly equal — worth a human eyeball.
    if (len(results) > 1
            and results[0]["best_vision"] is not None
            and results[1]["best_vision"] is not None
            and results[0]["best_vision"] - results[1]["best_vision"] < CLOSE_CALL_GAP):
        flags.append("close_call")
    ev["flags"] = flags
