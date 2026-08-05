"""Interject job: fill a freshly ripple-inserted gap with an UNMUTED clip.

The endpoint already cut the VO and opened a provisional 2s gap (a placeholder
event flagged interject+sourcing). This job plans audio-forward search angles
around the surrounding narration, runs the search → judge → download
tournament with the interject brain (inverted rules: the clip's AUDIO is the
joke), picks the exact seconds by audio energy + frame verification, resizes
the gap to fit the chosen moment, and fills the event. Any failure rolls the
gap back entirely — no orphaned silence.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl, save_edl
from ...models import Asset, Project
from ...sourcing.base import Filters
from ...sourcing.youtube import YouTubeProvider
from ..bus import bus
from ..runner import JobContext, register
from .source import _COMPILATION_RE, _SEARCH_GATE, _fetch_or_reuse

SEARCH_N = 8
RANK_KEEP = 14
ESCALATE_BELOW = 0.45
MIN_GAP_S = 1.0
MAX_GAP_S = 4.0


class InterjectMiss(RuntimeError):
    """No usable clip found — the gap must roll back."""


def _surrounding_narration(project_id: str, gap_start: float,
                           gap_end: float) -> tuple[str, str]:
    """Text of the ±2 beats around the gap — the narration the interjection
    interrupts and responds to."""
    beats_path = settings().project_dir(project_id) / "beats.json"
    if not beats_path.exists():
        return "", ""
    beats = sorted(json.loads(beats_path.read_text())["beats"],
                   key=lambda b: b["start_s"])
    before = [b.get("text") or b.get("gist", "") for b in beats
              if b["end_s"] <= gap_start + 0.05]
    after = [b.get("text") or b.get("gist", "") for b in beats
             if b["start_s"] >= gap_end - 0.05]
    return " ".join(before[-2:]).strip(), " ".join(after[:2]).strip()


def _pick_moment(asset: Asset, win_s: float, intent: str,
                 work_tag: str) -> tuple[float, float, float] | None:
    """Best (in_s, out_s, score) window inside the asset: top audio-energy
    windows, frame-verified by the interject judge. None when the asset has
    no usable audio."""
    from ...brain.client import BrainError
    from ...brain.interject import judge_interject_frames
    from ...moments.audio import ANALYZE_MAX_S, rms_envelope, top_energy_windows
    from ...moments.frames import sample_window_frames

    path = Path(asset.file_path)
    env = rms_envelope(path, min(asset.duration_s or ANALYZE_MAX_S, ANALYZE_MAX_S))
    windows = top_energy_windows(env, win_s)
    if not windows:
        return None
    urls = sample_window_frames(
        path, [(a, b) for a, b, _e in windows],
        settings().library_dir / asset.source_id / "verify" / f"ij_{work_tag}")
    present = [(i, u) for i, u in enumerate(urls) if u]
    vision: list[float] | None = None
    if present:
        try:
            scores = judge_interject_frames(
                intent, asset.title or "", [u for _i, u in present],
                [windows[i][2] for i, _u in present])
            vision = [0.0] * len(windows)
            for (i, _u), s in zip(present, scores):
                vision[i] = s
        except BrainError:
            vision = None
    if vision is None:
        a, b, e = windows[0]  # loudest window — best guess without eyes
        return a, b, e
    blended = [0.6 * v + 0.4 * e for v, (_a, _b, e) in zip(vision, windows)]
    best = max(range(len(windows)), key=lambda i: blended[i])
    return windows[best][0], windows[best][1], vision[best]


def source_interject_clip(project_id: str, ev: dict, hint: str | None,
                          avoid_source_ids: set[str] | None = None,
                          fixed_dur_s: float | None = None) -> dict | None:
    """Blocking tournament for one interjection: plan → search → judge →
    download 2 finalists → audio+frame moment pick (escalating to the second
    finalist when the first verifiably misses).

    Returns {asset_id, in_s, out_s, dur_s, intent, vision} or None when
    nothing survives. Raises BrainError when the LLM is unavailable —
    Interject has no heuristic fallback; a random unmuted clip is worse
    than no interjection."""
    from ...brain.interject import (judge_interject_candidates, plan_interject)
    from ...brain.steps_helpers import brief_summary
    from ...sourcing.rank import rank

    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
    before, after = _surrounding_narration(project_id, ev["start_s"], ev["end_s"])

    plan = plan_interject(brief_summary(brief), ", ".join(brief.get("avoid", [])),
                          before, after, hint)
    if not plan["queries"]:
        return None
    win_s = fixed_dur_s if fixed_dur_s else max(
        MIN_GAP_S, min(MAX_GAP_S, (plan["min_s"] + plan["max_s"]) / 2))

    provider = YouTubeProvider()
    filters = Filters(avoid=brief.get("avoid", []), reaction_intent=True)
    merged: dict[str, object] = {}
    for q in plan["queries"]:
        try:
            with _SEARCH_GATE:
                results = provider.search(q, SEARCH_N, filters)
                time.sleep(0.4)
            for c in results:
                merged.setdefault(c.source_id, c)
        except Exception:  # noqa: BLE001 — one failed search ≠ dead interject
            continue
    if avoid_source_ids:
        merged = {k: v for k, v in merged.items() if k not in avoid_source_ids}
    if not merged:
        return None

    ranked = rank(plan["queries"][0], list(merged.values()), filters)[:RANK_KEEP]
    clean = [c for c in ranked if not _COMPILATION_RE.search(c.title or "")]
    ranked = clean or ranked

    picks = judge_interject_candidates(
        plan["comedic_intent"], before, after, plan["queries"],
        [{"title": c.title, "channel": c.channel, "duration_s": c.duration_s,
          "views": c.view_count, "thumbnail": c.thumbnail} for c in ranked])
    order = [ranked[i] for i, _rel, _fr in picks]
    if not order:
        return None

    best: tuple[Asset, float, float, float] | None = None
    for cand in order[:3]:
        aid = _fetch_or_reuse(provider, cand, plan["queries"])
        if not aid:
            continue
        with session_scope() as db:
            asset = db.get(Asset, aid)
        if not asset or not Path(asset.file_path).exists():
            continue
        try:
            moment = _pick_moment(asset, win_s, plan["comedic_intent"], ev["id"])
        except Exception:  # noqa: BLE001 — a broken finalist forfeits
            continue
        if moment is None:
            continue  # no audio stream — useless as an interjection
        in_s, out_s, score = moment
        if best is None or score > best[3]:
            best = (asset, in_s, out_s, score)
        if score >= ESCALATE_BELOW:
            break  # good enough — don't spend another download
    if best is None:
        return None
    asset, in_s, out_s, score = best
    return {"asset_id": asset.id, "in_s": round(in_s, 3),
            "out_s": round(out_s, 3), "dur_s": round(out_s - in_s, 3),
            "intent": plan["comedic_intent"], "vision": round(score, 3)}


async def _rollback(project_id: str, event_id: str) -> None:
    """Remove the provisional gap entirely — narration flows as before. The
    pre-insert preview on disk is still valid, so no re-render needed."""
    from ...timeline_ops import remove_time
    try:
        edl = load_edl(project_id)
        ev = next((e for e in edl["events"] if e["id"] == event_id), None)
        if ev:
            await asyncio.to_thread(remove_time, project_id,
                                    ev["start_s"], ev["end_s"])
    except Exception:  # noqa: BLE001 — rollback is best-effort
        pass


@register("interject")
async def run_interject(ctx: JobContext) -> None:
    from ...brain.client import BrainError, is_available
    from ...timeline_ops import resize_gap

    project_id = ctx.project_id
    event_id = ctx.payload.get("event_id") or ""
    hint = (ctx.payload.get("hint") or "").strip() or None
    step = ctx.add_step("interject")

    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        await ctx.finish_step(step, "gap vanished — nothing to fill")
        return

    async def fail(reason: str) -> None:
        await _rollback(project_id, event_id)
        await bus.publish({"type": "interject_failed", "project_id": project_id,
                           "event_id": event_id, "reason": reason})
        await ctx.finish_step(step, f"rolled back — {reason}")

    if not is_available():
        await fail("Interject needs the AI brain (Settings → OpenAI key)")
        return

    await ctx.report(step, 0.1, "Reading the narration around the cut")
    try:
        result = await asyncio.to_thread(
            source_interject_clip, project_id, ev, hint)
    except BrainError:
        await fail("the AI brain was unavailable — try again")
        return
    except Exception as exc:  # noqa: BLE001 — never leave an orphaned gap
        await fail(f"sourcing crashed ({type(exc).__name__})")
        raise
    if not result:
        await fail("nothing funny enough survived the judge — "
                   "try again or add a direction")
        return

    # Fit the gap to the clip's natural moment, then fill the event.
    await ctx.report(step, 0.7, "Fitting the gap to the clip")
    await asyncio.to_thread(resize_gap, project_id, event_id, result["dur_s"])
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        await fail("gap vanished during sourcing")
        return
    ev["asset_id"] = result["asset_id"]
    ev["source"] = {"in_s": result["in_s"], "out_s": result["out_s"],
                    "chosen_rank": 1, "visual": result["vision"]}
    ev["audio"] = {"mode": "keep", "duck_db": -18}
    ev["interject"] = {"intent": result["intent"], "vision": result["vision"]}
    ev["flags"] = [f for f in ev.get("flags", []) if f != "sourcing"]
    save_edl(project_id, edl)
    for sub in ("segments", "segments_full"):
        seg_dir = settings().project_dir(project_id) / sub
        (seg_dir / f"{event_id}.mp4").unlink(missing_ok=True)
        (seg_dir / f"thumb_{event_id}.jpg").unlink(missing_ok=True)

    await ctx.report(step, 0.8, "Stitching it into the preview")
    from .assemble import run_assemble
    await run_assemble(ctx)
    await bus.publish({"type": "interject_done", "project_id": project_id,
                       "event_id": event_id, "intent": result["intent"]})
    await ctx.finish_step(step, f"⚡ {result['intent'] or 'interjection landed'}")
