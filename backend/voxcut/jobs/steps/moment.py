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
    _attach_words(beats, pdir / "transcript.json")
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
    sem = asyncio.Semaphore(6)  # vision calls + occasional escalation downloads

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
# Cold open: the hook is held to a higher bar and gets more retries — a weak
# first clip costs more watch time than a weak clip anywhere else.
COLD_ESCALATE_BELOW = 0.65
COLD_MAX_ESCALATIONS = 3
# Final window score = VISION_WEIGHT*vision + the rest from signal fusion.
# Signals are min-max normalized per video, so the best window scores 1.0
# even when the signals are noise; at 0.3 that swing outvoted a 0.3 vision
# gap. With the calibrated judge, signals only break near-ties (same split
# the interject picker uses).
VISION_WEIGHT = 0.85
# A finalist verified at or above this ends the tournament early. It was 0.7,
# which the first finalist cleared most of the time — so the second comedic
# angle was downloaded and then never looked at. 0.8 is the judge's "THE
# exact gag" band; anything less and the other angle gets its shot.
FINALIST_SKIP_AT = 0.8
CLOSE_CALL_GAP = 0.1  # finalists within this vision margin → flag for review
COARSE_MAX_FRAMES = 12
COARSE_KEEP = 0.4     # coarse regions below this score aren't worth refining


ANALYZE_MAX_S = 900.0  # never analyze past 15 min — protects against the
                        # oversized sources already cached in the library


def _attach_words(beats: dict, transcript_path: Path) -> None:
    """Give each beat its spoken words as (text, start_s) so the moment step
    can find when the planner's punch word is said."""
    try:
        words = json.loads(transcript_path.read_text())["words"]
    except Exception:  # noqa: BLE001 — no transcript → no alignment
        return
    by_idx = {w["idx"]: w for w in words}
    for b in beats.values():
        lo, hi = b.get("word_start_idx"), b.get("word_end_idx")
        if lo is None or hi is None:
            continue
        b["words"] = [(by_idx[i]["text"], by_idx[i]["start_s"])
                      for i in range(lo, hi + 1) if i in by_idx]


def _punch_off(ev: dict, beat: dict) -> float | None:
    """Seconds into this event at which its punch word is spoken, or None.
    Only for events that still span their whole beat — a split or trimmed
    event no longer starts where the beat's words do."""
    from ...moments.timing import punch_offset
    if not beat or not ev.get("punch_word") or not beat.get("words"):
        return None
    beat_dur = beat["end_s"] - beat["start_s"]
    if abs((ev["end_s"] - ev["start_s"]) - beat_dur) > 0.05:
        return None
    return punch_offset(ev["punch_word"], beat["words"], beat["start_s"])


def _signal_moments(ev: dict, asset: Asset, query: str, entities: list[str],
                    curve_out: dict | None = None):
    cache_dir = settings().library_dir / asset.source_id
    beat_dur = ev["end_s"] - ev["start_s"]
    return select_moments(
        curve_out=curve_out,
        video=Path(asset.file_path), cache_dir=cache_dir,
        duration=min(asset.duration_s or 0.0, ANALYZE_MAX_S),
        beat_query=query, entities=entities,
        intent=intent_for(ev["kind"]), beat_dur=beat_dur,
        subs_path=Path(asset.subs_path) if asset.subs_path else None,
        heatmap_path=Path(asset.heatmap_path) if asset.heatmap_path else None,
    )


def _judge_windows(ev: dict, asset: Asset, beat: dict,
                   windows: list[tuple[float, float]],
                   work_tag: str) -> list[dict] | None:
    """Frame-judge arbitrary windows. Returns one record per window
    ({"score", "action_at"}), or None when vision is unavailable/failed."""
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_frames_detailed
    from ...moments.frames import sample_window_tiles

    if not is_available() or not windows:
        return None
    urls = sample_window_tiles(
        Path(asset.file_path), windows,
        settings().library_dir / asset.source_id / "verify" / f"{ev['id']}_{work_tag}")
    present = [(i, u) for i, u in enumerate(urls) if u]
    if not present:
        return None
    try:
        # Coarse scan: up to 12 tiles just to find the region — low detail.
        # Fine verify: the score that gates escalation — high detail, so the
        # judge can actually read a title card or a logo before approving.
        recs_present = judge_frames_detailed(
            beat.get("text") or beat.get("gist", ""), ev["kind"],
            asset.title or "", [u for _i, u in present],
            detail="low" if work_tag == "coarse" else "high"
            # Deliberately the narration ONLY — no planner gag, no neighbor
            # lines. Both were measured: neighbor context cost agreement on
            # the labelled set (15/15 → 13/15), and the gag made the judge
            # grade footage against the plan ("it is a dog in sunglasses,
            # as planned" → 1.0) instead of against the line, which QC then
            # flagged as middle. This judge is the independent check.
            )
    except BrainError:
        return None
    recs = [{"score": 0.0, "action_at": "none"} for _ in windows]
    for (i, _u), r in zip(present, recs_present):
        recs[i] = {"score": r["score"], "action_at": r.get("action_at", "none")}
    return recs


def _coarse_windows(ev: dict, asset: Asset, beat: dict,
                    beat_dur: float) -> list[tuple[float, float]]:
    """Stage 1: scan frames across the ENTIRE video to find where the gag
    lives, independent of subtitle/heatmap/audio signals. Fixes the
    'window misses the gag entirely' failure — weak signals can no longer
    strand the search in the wrong region."""
    dur = min(asset.duration_s or 0.0, ANALYZE_MAX_S)
    if dur <= beat_dur * 4:  # short clip — fine windows already cover it
        return []
    n = min(COARSE_MAX_FRAMES, max(6, int(dur // 15)))
    step = dur / n
    windows = []
    for i in range(n):
        c = step / 2 + i * step
        windows.append((max(0.0, c - beat_dur / 2), min(dur, c + beat_dur / 2)))
    recs = _judge_windows(ev, asset, beat, windows, "coarse")
    if recs is None:
        return []
    scores = [r["score"] for r in recs]
    top = sorted(range(len(windows)), key=lambda i: scores[i], reverse=True)[:3]
    return [windows[i] for i in top if scores[i] >= COARSE_KEEP]


def _try_asset(ev: dict, asset: Asset, beat: dict, query: str,
               entities: list[str]) -> dict:
    """Score one asset: coarse whole-video vision scan + signal windows →
    fine frame verification. Returns the best window + its vision score."""
    from ...moments.scenes import detect_scenes, snap_to_scenes
    from ...moments.select import score_at
    from ...moments.timing import place_in_point

    beat_dur = ev["end_s"] - ev["start_s"]
    punch_off = _punch_off(ev, beat)
    curve: dict = {}
    moments, conf = _signal_moments(ev, asset, query, entities, curve)
    sig_windows = [(m.in_s, m.out_s, m.score) for m in moments[:3]]

    coarse = _coarse_windows(ev, asset, beat, beat_dur)
    scenes = detect_scenes(Path(asset.file_path),
                           settings().library_dir / asset.source_id / "scenes.json")
    # A vision-found window carries the signal score of that spot in the
    # video. It used to enter the blend at 0.0, which handed every
    # signal-found window a head start of up to 0.3 over footage the vision
    # scan had actually looked at.
    coarse_snapped = []
    for (a, b) in coarse:
        sa, sb = snap_to_scenes(a, b, scenes, tolerance=beat_dur * 0.25)
        coarse_snapped.append((sa, sb, score_at(curve, sa)))

    # Merge candidate windows (coarse first — they came from real pixels),
    # dropping near-duplicates.
    merged: list[tuple[float, float, float]] = []
    for w in coarse_snapped + sig_windows:
        if all(abs(w[0] - m[0]) > beat_dur * 0.5 for m in merged):
            merged.append(w)
    merged = merged[:6]
    if not merged:
        merged = [(0.0, min(asset.duration_s or beat_dur, beat_dur), 0.0)]

    fine = _judge_windows(ev, asset, beat, [(a, b) for a, b, _s in merged], "fine")
    vision = [r["score"] for r in fine] if fine is not None else None
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
            d["action_at"] = fine[i]["action_at"]
        # Where to actually start playing: slide a late payoff into the clip
        # and keep scene cuts off the first/last few frames.
        play_in = place_in_point(
            a, beat_dur, d.get("action_at", "none"), scenes,
            min(asset.duration_s or 0.0, ANALYZE_MAX_S),
            punch_off=punch_off)
        if abs(play_in - a) > 0.01:
            # The strip, the EDL and a manual pick all use the placed
            # window; judged_in_s keeps what the judge actually looked at.
            d["judged_in_s"] = d["in_s"]
            d["in_s"] = round(play_in, 3)
            d["out_s"] = round(b + (play_in - a), 3)
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


def _source_for(moment: dict) -> dict:
    """EDL source block for a judged (and placed) window."""
    src = {"in_s": moment["in_s"], "out_s": moment["out_s"], "chosen_rank": 1}
    if "judged_in_s" in moment:
        src["judged_in_s"] = moment["judged_in_s"]
    return src


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

    # Tournament, lazily: verify finalist #1 first and only spend on the next
    # finalist when the current best hasn't clearly nailed it (vision below
    # FINALIST_SKIP_AT).
    finalist_ids = list(dict.fromkeys(
        ev.get("finalist_asset_ids") or
        ([ev["asset_id"]] if ev.get("asset_id") else [])))
    results: list[dict] = []
    for aid in finalist_ids:
        if results:
            best = max((r["best_vision"] or 0) for r in results
                       if r["best_vision"] is not None) if any(
                r["best_vision"] is not None for r in results) else None
            if best is not None and best >= FINALIST_SKIP_AT:
                break  # already verified-great; skip remaining finalists
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

    # Escalation net: if even the tournament winner verifiably misses, try
    # more judge-approved sources before settling. The cold open escalates
    # harder: higher bar, up to 3 extra sources.
    cold = "cold_open" in (ev.get("flags") or [])
    escalate_below = COLD_ESCALATE_BELOW if cold else ESCALATE_BELOW
    attempts = COLD_MAX_ESCALATIONS if cold else 1
    tried = {r["asset"].source_id for r in results}
    while (attempts > 0 and winner["best_vision"] is not None
           and winner["best_vision"] < escalate_below):
        attempts -= 1
        nxt = next((c for c in ev.get("source_candidates", [])
                    if c.get("source_id") and c["source_id"] not in tried
                    and c.get("url")), None)
        if not nxt:
            break
        tried.add(nxt["source_id"])
        alt_asset = _fetch_candidate(nxt)
        if not alt_asset or not Path(alt_asset.file_path).exists():
            continue
        try:
            alt = _try_asset(ev, alt_asset, beat, query, entities)
            results.append(alt)
            results.sort(key=_score_of, reverse=True)
            winner = results[0]
        except Exception:  # noqa: BLE001
            continue

    ev["asset_id"] = winner["asset"].id
    best = winner["moments"][0]
    src = _source_for(best)
    src["confidence"] = winner["conf"]
    if winner["best_vision"] is not None:
        src["visual"] = round(winner["best_vision"], 3)
    ev["source"] = src
    ev["moment_candidates"] = winner["moments"]
    ev["finalists"] = [{
        "asset_id": r["asset"].id,
        "title": r["asset"].title,
        "in_s": _source_for(r["moments"][0])["in_s"],
        "out_s": _source_for(r["moments"][0])["out_s"],
        "visual": r["best_vision"],
    } for r in results[:3]]

    flags = [f for f in ev.get("flags", [])
             if f not in ("needs_review", "close_call", "cold_open_weak")]
    from ...moments.select import CONF_THRESHOLD
    needs_review = (winner["best_vision"] < REVIEW_BELOW
                    if winner["best_vision"] is not None
                    else winner["conf"] < CONF_THRESHOLD)
    if needs_review:
        flags.append("needs_review")
    # The hook may never resolve weak silently — flag it front and center.
    if cold and (winner["best_vision"] is None
                 or winner["best_vision"] < COLD_ESCALATE_BELOW):
        flags.append("cold_open_weak")
    # Close call: two finalists verified nearly equal — worth a human eyeball.
    if (len(results) > 1
            and results[0]["best_vision"] is not None
            and results[1]["best_vision"] is not None
            and results[0]["best_vision"] - results[1]["best_vision"] < CLOSE_CALL_GAP):
        flags.append("close_call")
    ev["flags"] = flags
