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
import random
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


def _used_interjections(project_id: str,
                        skip_event_id: str) -> tuple[list[str], dict[str, int], set[str]]:
    """What this video's OTHER interjections already play: (titles+franchises
    for the planner's never-again list, franchise counts for the judge's
    fatigue guard, source ids to exclude from search). Without this memory
    every call is amnesiac and converges on the same canonical clip."""
    from ...timeline_ops import INTERJECT_FLAG
    used: list[str] = []
    franchise_counts: dict[str, int] = {}
    used_ids: set[str] = set()
    try:
        edl = load_edl(project_id)
    except Exception:  # noqa: BLE001
        return used, franchise_counts, used_ids
    for e in edl["events"]:
        if e["id"] == skip_event_id or INTERJECT_FLAG not in (e.get("flags") or []):
            continue
        fr = ((e.get("interject") or {}).get("franchise") or "").strip()
        if fr:
            franchise_counts[fr] = franchise_counts.get(fr, 0) + 1
            used.append(fr)
        if e.get("asset_id"):
            with session_scope() as db:
                a = db.get(Asset, e["asset_id"])
            if a:
                used_ids.add(a.source_id)
                if a.title:
                    used.append(a.title)
    return list(dict.fromkeys(used)), franchise_counts, used_ids


# ------------------------------------------------------ persistent history
# The per-EDL memory above dies the moment an interjection is deleted (the
# common workflow: generate → dislike → delete → generate again) and never
# crosses projects — which is how the same canonical scene kept coming back
# in every video. This file-backed history survives both: every PICK is
# recorded globally, and recent picks are hard-avoided everywhere.

HISTORY_KEEP = 60    # entries retained on disk
HISTORY_AVOID = 25   # most-recent picks fed into plan/judge/search exclusion


def _history_path() -> Path:
    return settings().data_dir / "interject_history.json"


def _load_history() -> list[dict]:
    try:
        return json.loads(_history_path().read_text()).get("picks", [])
    except Exception:  # noqa: BLE001 — missing/corrupt history = empty
        return []


def _record_history(project_id: str, title: str, franchise: str,
                    source_id: str) -> None:
    from datetime import datetime, timezone
    picks = _load_history()
    picks.append({"ts": datetime.now(timezone.utc).isoformat(),
                  "project_id": project_id, "title": title,
                  "franchise": franchise, "source_id": source_id})
    try:
        _history_path().write_text(json.dumps(
            {"version": 1, "picks": picks[-HISTORY_KEEP:]}, indent=2))
    except Exception:  # noqa: BLE001 — history is best-effort
        pass


def source_interject_clip(project_id: str, ev: dict, hint: str | None,
                          avoid_source_ids: set[str] | None = None,
                          fixed_dur_s: float | None = None,
                          avoid_titles: list[str] | None = None) -> dict | None:
    """Blocking tournament for one interjection. The planner returns SEVERAL
    distinct ideas (different franchises/registers) and the server samples
    them in random order — the model is bad at being random across calls, so
    the dice live here. Each idea runs search → judge → download → audio+
    frame moment pick; the first idea that lands wins and is recorded in the
    global history, which (together with this video's own interjections)
    is hard-avoided by plan, judge, and search alike.

    Returns {asset_id, in_s, out_s, dur_s, intent, vision, franchise} or None
    when nothing survives. Raises BrainError when the LLM is unavailable —
    Interject has no heuristic fallback; a random unmuted clip is worse
    than no interjection."""
    from ...brain.interject import (judge_interject_candidates, plan_interject)
    from ...brain.steps_helpers import brief_summary

    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
    before, after = _surrounding_narration(project_id, ev["start_s"], ev["end_s"])

    used, franchise_counts, used_ids = _used_interjections(project_id, ev["id"])
    used += [t for t in (avoid_titles or []) if t not in used]
    for h in _load_history()[-HISTORY_AVOID:]:
        if h.get("source_id"):
            used_ids.add(h["source_id"])
        fr = (h.get("franchise") or "").strip()
        if fr:
            franchise_counts[fr] = franchise_counts.get(fr, 0) + 1
        for name in (h.get("title"), fr):
            if name and name not in used:
                used.append(name)
    avoid_source_ids = (avoid_source_ids or set()) | used_ids

    ideas = plan_interject(brief_summary(brief), ", ".join(brief.get("avoid", [])),
                           before, after, hint, used_clips=used)
    if not ideas:
        return None

    provider = YouTubeProvider()
    filters = Filters(avoid=brief.get("avoid", []), reaction_intent=True)
    # With an operator hint the first idea is the one that follows it — keep
    # that order. Otherwise: shuffle, so the model's favorite never wins by
    # default.
    order = ideas if hint else random.sample(ideas, len(ideas))
    for idea in order:
        result = _try_idea(project_id, ev, idea, provider, filters,
                           before, after, avoid_source_ids,
                           franchise_counts, used, fixed_dur_s)
        if result:
            _record_history(project_id, result["title"],
                            result["franchise"], result["source_sid"])
            result.pop("title", None)
            result.pop("source_sid", None)
            return result
    return None


def _try_idea(project_id: str, ev: dict, idea: dict, provider, filters,
              before: str, after: str, avoid_source_ids: set[str],
              franchise_counts: dict[str, int], used: list[str],
              fixed_dur_s: float | None) -> dict | None:
    """Run one planned idea through search → judge → download → moment pick.
    None = this idea found nothing usable (caller tries the next idea)."""
    from ...brain.interject import judge_interject_candidates
    from ...sourcing.rank import rank

    win_s = fixed_dur_s if fixed_dur_s else max(
        MIN_GAP_S, min(MAX_GAP_S, (idea["min_s"] + idea["max_s"]) / 2))

    merged: dict[str, object] = {}
    for q in idea["queries"]:
        try:
            with _SEARCH_GATE:
                results = provider.search(q, SEARCH_N, filters)
                time.sleep(0.4)
            for c in results:
                merged.setdefault(c.source_id, c)
        except Exception:  # noqa: BLE001 — one failed search ≠ dead idea
            continue
    merged = {k: v for k, v in merged.items() if k not in avoid_source_ids}
    if not merged:
        return None

    ranked = rank(idea["queries"][0], list(merged.values()), filters)[:RANK_KEEP]
    clean = [c for c in ranked if not _COMPILATION_RE.search(c.title or "")]
    ranked = clean or ranked

    picks = judge_interject_candidates(
        idea["comedic_intent"], before, after, idea["queries"],
        [{"title": c.title, "channel": c.channel, "duration_s": c.duration_s,
          "views": c.view_count, "thumbnail": c.thumbnail} for c in ranked],
        franchise_counts=franchise_counts, used_clips=used)
    order = [ranked[i] for i, _rel, _fr in picks]
    franchise_of = {ranked[i].source_id: fr for i, _rel, fr in picks if fr}
    if not order:
        return None

    best: tuple[Asset, float, float, float] | None = None
    for cand in order[:3]:
        aid = _fetch_or_reuse(provider, cand, idea["queries"])
        if not aid:
            continue
        with session_scope() as db:
            asset = db.get(Asset, aid)
        if not asset or not Path(asset.file_path).exists():
            continue
        try:
            moment = _pick_moment(asset, win_s, idea["comedic_intent"], ev["id"])
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
            "intent": idea["comedic_intent"], "vision": round(score, 3),
            "franchise": franchise_of.get(asset.source_id, ""),
            "title": asset.title or "", "source_sid": asset.source_id}


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
    ev["interject"] = {"intent": result["intent"], "vision": result["vision"],
                       "franchise": result.get("franchise", "")}
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
