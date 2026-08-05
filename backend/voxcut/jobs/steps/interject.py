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


PAD_PRE = 0.15    # utterance padding: never clip the first syllable
PAD_POST = 0.25   # … or the last one
MAX_WINDOWS = 7
# Accept bar: below this judged score a candidate doesn't win outright.
# Settling for "merely loud" is the random-yelling failure the transcript
# judge replaces — but between SALVAGE_SCORE and the bar a window is an
# imperfect fit, not garbage (the judge floors noise at ~0.2), so the best
# such near-miss ships when a whole run comes up dry.
MIN_WINDOW_SCORE = 0.4
SALVAGE_SCORE = 0.3


def _dedupe_rolling(cues: list[dict]) -> list[dict]:
    """YouTube auto-captions ROLL: consecutive cues overlap in time and
    repeat the previous cue's tail ("...mr. Toby yeah" / "mr. Toby yeah why
    don't you..."). Strip each cue down to its genuinely new words, so
    utterance windows don't stutter duplicated text."""
    out: list[dict] = []
    prev_words: list[str] = []
    for c in cues:
        words = c["text"].split()
        overlap = 0
        for j in range(min(len(prev_words), len(words)), 0, -1):
            if prev_words[-j:] == [w for w in words[:j]]:
                overlap = j
                break
        prev_words = words
        fresh = words[overlap:]
        if fresh:
            out.append({**c, "text": " ".join(fresh)})
    return out


def _asset_cues(asset: Asset) -> list[dict]:
    """Parsed subtitle cues ({start, end, text}) captured at download time,
    de-rolled so each cue carries only its new words."""
    if asset.subs_path and Path(asset.subs_path).exists():
        try:
            return _dedupe_rolling(
                [c for c in json.loads(Path(asset.subs_path).read_text())
                 if c.get("text")])
        except Exception:  # noqa: BLE001
            return []
    return []


def _transcribe_span(video: Path, a: float, b: float, work: Path) -> str:
    """What is actually SAID in [a, b]: whisper (small cached model) on just
    that slice — a few seconds of audio, so it stays cheap. Empty string when
    nothing intelligible."""
    import subprocess

    from ...asr.transcribe import _get_model, _pick_model
    from ...media.probe import ffmpeg
    work.mkdir(parents=True, exist_ok=True)
    wav = work / f"span_{a:.2f}_{b:.2f}.wav"
    try:
        proc = subprocess.run(
            [ffmpeg(), "-y", "-ss", f"{max(0.0, a):.3f}", "-t", f"{b - a:.3f}",
             "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, check=False, timeout=120)
        if proc.returncode != 0 or not wav.exists():
            return ""
        model = _get_model(_pick_model("fast"))
        segments, _info = model.transcribe(
            str(wav), language="en", beam_size=1,
            condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception:  # noqa: BLE001 — no transcript is a valid (bad) signal
        return ""
    finally:
        wav.unlink(missing_ok=True)


LONG_CUE_S = 6.0  # auto-captions sometimes emit one rolling 15s mega-cue


def _whisper_segments(video: Path, a: float, b: float,
                      cache_dir: Path) -> list[dict]:
    """Split [a, b] into real utterance segments via whisper (cached per
    span). YouTube auto-captions can lump a whole scene into one rolling
    cue — the payload line in its MIDDLE is unreachable from cue boundaries
    without this. Returns [{start, end, text}] in absolute video time."""
    import subprocess

    from ...asr.transcribe import _get_model, _pick_model
    from ...media.probe import ffmpeg
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"whisper_w_{a:.1f}_{b:.1f}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except Exception:  # noqa: BLE001
            pass
    wav = cache_dir / f"span_{a:.1f}_{b:.1f}.wav"
    segs: list[dict] = []
    try:
        proc = subprocess.run(
            [ffmpeg(), "-y", "-ss", f"{max(0.0, a):.3f}", "-t", f"{b - a:.3f}",
             "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, check=False, timeout=180)
        if proc.returncode == 0 and wav.exists():
            model = _get_model(_pick_model("fast"))
            # WORD-level cues: whisper's segments can be one long run-on
            # (screams especially), but word times are solid — and the
            # utterance grouping downstream merges words back into natural
            # windows at real speech gaps.
            raw, _info = model.transcribe(
                str(wav), language="en", beam_size=1, word_timestamps=True,
                condition_on_previous_text=False)
            for s in raw:
                for w in (s.words or []):
                    if w.word.strip():
                        segs.append({"start": round(a + w.start, 2),
                                     "end": round(a + w.end, 2),
                                     "text": w.word.strip()})
    except Exception:  # noqa: BLE001 — fall back to the blob cue
        segs = []
    finally:
        wav.unlink(missing_ok=True)
    try:
        cache.write_text(json.dumps(segs))
    except Exception:  # noqa: BLE001
        pass
    return segs


def _split_long_cues(cues: list[dict], video: Path,
                     cache_dir: Path) -> list[dict]:
    out: list[dict] = []
    for c in cues:
        if c["end"] - c["start"] <= LONG_CUE_S:
            out.append(c)
            continue
        segs = _whisper_segments(video, c["start"], c["end"], cache_dir)
        out.extend(segs or [c])
    return out


def _win_energy(env, a: float, b: float) -> float:
    from ...moments.audio import BUCKET_S
    if env is None or not len(env):
        return 0.0
    i0 = max(0, int(a / BUCKET_S))
    i1 = max(i0 + 1, int(b / BUCKET_S))
    seg = env[i0:i1]
    return float(seg.mean()) if len(seg) else 0.0


def _propose_windows(asset: Asset, idea: dict,
                     fixed_dur_s: float | None) -> list[dict]:
    """Candidate windows as {in_s, out_s, energy, text}. Utterance-first:
    subtitle cues merge into complete spoken lines (padded so words never
    clip), ranked by text relevance to the idea; the loudest windows only
    fill leftover slots and get transcribed themselves — energy stopped
    being the driver because loudest-window picking selects for yelling."""
    from ...moments import embed
    from ...moments.audio import ANALYZE_MAX_S, rms_envelope, top_energy_windows

    dur = min(asset.duration_s or ANALYZE_MAX_S, ANALYZE_MAX_S)
    lo = fixed_dur_s or max(MIN_GAP_S, idea["min_s"])
    hi = fixed_dur_s or min(MAX_GAP_S, idea["max_s"])
    env = rms_envelope(Path(asset.file_path), dur)

    def clamp(a: float, b: float) -> tuple[float, float]:
        if fixed_dur_s:  # reroll: gap length is fixed — center on the line
            c = (a + b) / 2
            a, b = c - fixed_dur_s / 2, c + fixed_dur_s / 2
        b = min(dur, min(b, a + MAX_GAP_S))
        a = max(0.0, a)
        return round(a, 3), round(max(b, a + 0.8), 3)

    out: list[dict] = []
    cues = _split_long_cues(
        _asset_cues(asset), Path(asset.file_path),
        settings().library_dir / asset.source_id / "verify" / "spans")
    if cues:
        groups: list[list[dict]] = []
        for c in cues:
            if (groups and c["start"] - groups[-1][-1]["end"] <= 0.4
                    and c["end"] - groups[-1][0]["start"] <= hi):
                groups[-1].append(c)
            else:
                groups.append([c])
        cands = []
        for g in groups:
            a, b = clamp(g[0]["start"] - PAD_PRE, g[-1]["end"] + PAD_POST)
            if b - a < 0.8:
                continue
            cands.append({"in_s": a, "out_s": b,
                          "energy": _win_energy(env, a, b),
                          "text": " ".join(c["text"] for c in g)})
        if cands:
            query = f"{idea['comedic_intent']} {' '.join(idea['queries'])}"
            vecs = embed.embed([c["text"] for c in cands] + [query])
            if vecs is not None:
                sims = embed.cosine_matrix(vecs[-1], vecs[:-1])
                order = sorted(range(len(cands)), key=lambda i: float(sims[i]),
                               reverse=True)
            else:
                order = sorted(range(len(cands)),
                               key=lambda i: cands[i]["energy"], reverse=True)
            # Rolling captions overlap in TIME too — suppress windows that
            # mostly re-cover an already-kept one (best-relevance wins).
            for i in order:
                c = cands[i]
                span = c["out_s"] - c["in_s"]
                if any(min(c["out_s"], k["out_s"]) - max(c["in_s"], k["in_s"])
                       > 0.5 * span for k in out):
                    continue
                out.append(c)
                if len(out) >= 5:
                    break

    def snap_to_cues(a: float, b: float) -> tuple[float, float]:
        """Nudge energy-window edges onto word/cue boundaries so the window
        plays complete words instead of clipping one at each end."""
        if fixed_dur_s:
            return a, b
        for c in cues:
            if c["start"] < a < c["end"] and a - c["start"] <= 0.6:
                a = c["start"] - 0.1
            if c["start"] < b < c["end"] and c["end"] - b <= 0.6:
                b = c["end"] + 0.15
        a = max(0.0, round(a, 3))
        return a, round(min(dur, min(b, a + MAX_GAP_S)), 3)

    win_s = fixed_dur_s or (lo + hi) / 2
    for a, b, e in top_energy_windows(env, win_s, n=6):
        if len(out) >= MAX_WINDOWS:
            break
        a, b = snap_to_cues(a, b)
        if any(abs(a - w["in_s"]) < 1.0 for w in out):
            continue
        if cues:
            # Only lines FULLY inside the window count as its transcript; a
            # partially-overlapped line would play cut off mid-word, and the
            # judge must see that, not the full quote.
            inside = [c for c in cues
                      if c["start"] >= a - 0.05 and c["end"] <= b + 0.05]
            partial = any(c["end"] > a and c["start"] < b for c in cues
                          if c not in inside)
            text = " ".join(c["text"] for c in inside)
            if partial:
                text = (text + " [a line is cut off at the window edge]").strip()
        else:
            text = _transcribe_span(
                Path(asset.file_path), a, b,
                settings().library_dir / asset.source_id / "verify" / "spans")
        if text and any(w["text"] == text for w in out):
            continue  # same payload, worse boundaries than the utterance window
        out.append({"in_s": round(a, 3), "out_s": round(b, 3),
                    "energy": e, "text": text})
    return out[:MAX_WINDOWS]


def _pick_moment(asset: Asset, idea: dict, fixed_dur_s: float | None,
                 work_tag: str) -> tuple[float, float, float, bool] | None:
    """Best (in_s, out_s, score, judged) window: utterance/energy proposals
    judged on the frame, the audio energy, AND a transcript of what is
    actually said. judged=False means the LLM judge was unavailable and the
    score is only the energy heuristic. None when the asset has no usable
    audio at all — accept/salvage thresholds are the caller's call."""
    from ...brain.client import BrainError
    from ...brain.interject import judge_interject_frames
    from ...moments.frames import sample_window_frames

    windows = _propose_windows(asset, idea, fixed_dur_s)
    if not windows:
        return None
    urls = sample_window_frames(
        Path(asset.file_path), [(w["in_s"], w["out_s"]) for w in windows],
        settings().library_dir / asset.source_id / "verify" / f"ij_{work_tag}")
    present = [(i, u) for i, u in enumerate(urls) if u]
    vision: list[float] | None = None
    if present:
        try:
            scores = judge_interject_frames(
                idea["comedic_intent"], asset.title or "",
                [u for _i, u in present],
                [windows[i]["energy"] for i, _u in present],
                [windows[i]["text"] for i, _u in present])
            vision = [0.0] * len(windows)
            for (i, _u), s in zip(present, scores):
                vision[i] = s
        except BrainError:
            vision = None
    if vision is None:
        w = windows[0]  # best utterance match (or loudest) — blind fallback
        return w["in_s"], w["out_s"], w["energy"], False
    blended = [0.85 * v + 0.15 * w["energy"] for v, w in zip(vision, windows)]
    best = max(range(len(windows)), key=lambda i: blended[i])
    return (windows[best]["in_s"], windows[best]["out_s"],
            vision[best], True)


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
                          avoid_titles: list[str] | None = None,
                          ) -> tuple[dict | None, str]:
    """Blocking tournament for one interjection: the planner's ideas run
    best-first through search → judge → download → audio+frame moment pick.
    Exact clips/scenes already used (this video OR the global history) are
    hard-avoided; franchise fatigue is two-tier — strict inside the video,
    a mild judged penalty across videos. If no idea clears the window bar,
    the planner gets ONE retry with failure feedback, and after that the
    best near-miss (>= SALVAGE_SCORE) ships rather than nothing.

    Returns (result, fail_reason): result is {asset_id, in_s, out_s, dur_s,
    intent, vision, franchise} or None, and fail_reason says what actually
    starved so the operator's toast is diagnosable. Raises BrainError when
    the LLM is unavailable — Interject has no heuristic fallback."""
    from ...brain.client import BrainError
    from ...brain.interject import plan_interject
    from ...brain.steps_helpers import brief_summary

    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
    before, after = _surrounding_narration(project_id, ev["start_s"], ev["end_s"])

    # This video's own interjections: strict fatigue. Global history: exact
    # clips are hard-avoided, but franchises are only a MILD judged penalty —
    # blanket-banning every franchise ever used starved the pipeline dry.
    used, video_franchises, used_ids = _used_interjections(project_id, ev["id"])
    used += [t for t in (avoid_titles or []) if t not in used]
    recent_franchises: list[str] = []
    for h in _load_history()[-HISTORY_AVOID:]:
        if h.get("source_id"):
            used_ids.add(h["source_id"])
        fr = (h.get("franchise") or "").strip()
        if fr and fr not in recent_franchises:
            recent_franchises.append(fr)
        if h.get("title") and h["title"] not in used:
            used.append(h["title"])
    avoid_source_ids = (avoid_source_ids or set()) | used_ids

    ideas = plan_interject(brief_summary(brief), ", ".join(brief.get("avoid", [])),
                           before, after, hint, used_clips=used)
    if not ideas:
        return None, "the planner produced no usable ideas"

    provider = YouTubeProvider()
    filters = Filters(avoid=brief.get("avoid", []), reaction_intent=True)
    stats = {"ideas": 0, "approved": 0, "clips": 0, "best": 0.0}
    salvage: list[dict] = []

    def attempt(idea_list: list[dict]) -> dict | None:
        # Quality-first: ideas arrive best-first and are tried in order —
        # the global history already guarantees variety across picks.
        for idea in idea_list:
            stats["ideas"] += 1
            r = _try_idea(project_id, ev, idea, provider, filters,
                          before, after, avoid_source_ids, video_franchises,
                          used, recent_franchises, fixed_dur_s,
                          salvage, stats)
            if r:
                return r
        return None

    result = attempt(ideas)
    if not result:
        # One replan with failure feedback: fresh, more-searchable angles.
        note = ((hint.strip() + " | ") if hint and hint.strip() else "") + (
            "A previous attempt planned clips that could not be sourced as a "
            "clean audio moment. Propose DIFFERENT famous moments, favoring "
            "scenes that exist as short isolated clip uploads on YouTube.")
        try:
            result = attempt(plan_interject(
                brief_summary(brief), ", ".join(brief.get("avoid", [])),
                before, after, note, used_clips=used))
        except BrainError:
            pass
    if not result and salvage:
        # Ship the best near-miss over nothing: everything in the pool
        # already beat SALVAGE_SCORE, so it's imperfect — not garbage.
        result = max(salvage, key=lambda s: s["vision"])

    if result:
        _record_history(project_id, result["title"],
                        result["franchise"], result["source_sid"])
        result.pop("title", None)
        result.pop("source_sid", None)
        return result, ""
    reason = (f"no clip cleared the bar — best window scored "
              f"{stats['best']:.2f} across {stats['clips']} clips from "
              f"{stats['ideas']} ideas; add a direction to steer the hunt")
    if stats["approved"] == 0:
        reason = (f"the judge approved none of the search results across "
                  f"{stats['ideas']} ideas — add a direction to steer the hunt")
    return None, reason


def _try_idea(project_id: str, ev: dict, idea: dict, provider, filters,
              before: str, after: str, avoid_source_ids: set[str],
              video_franchises: dict[str, int], used: list[str],
              recent_franchises: list[str], fixed_dur_s: float | None,
              salvage: list[dict], stats: dict) -> dict | None:
    """Run one planned idea through search → judge → download → moment pick.
    None = nothing here cleared the accept bar (near-misses land in the
    shared salvage pool; the caller tries the next idea)."""
    from ...brain.interject import judge_interject_candidates
    from ...sourcing.rank import rank

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
        franchise_counts=video_franchises, used_clips=used,
        recent_franchises=recent_franchises)
    order = [ranked[i] for i, _rel, _fr in picks]
    franchise_of = {ranked[i].source_id: fr for i, _rel, fr in picks if fr}
    stats["approved"] += len(order)
    if not order:
        return None

    def to_result(asset: Asset, in_s: float, out_s: float, score: float) -> dict:
        return {"asset_id": asset.id, "in_s": round(in_s, 3),
                "out_s": round(out_s, 3), "dur_s": round(out_s - in_s, 3),
                "intent": idea["comedic_intent"], "vision": round(score, 3),
                "franchise": franchise_of.get(asset.source_id, ""),
                "title": asset.title or "", "source_sid": asset.source_id}

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
            moment = _pick_moment(asset, idea, fixed_dur_s, ev["id"])
        except Exception:  # noqa: BLE001 — a broken finalist forfeits
            continue
        if moment is None:
            continue  # no audio stream / no windows — useless here
        in_s, out_s, score, judged = moment
        stats["clips"] += 1
        stats["best"] = max(stats["best"], score if judged else 0.0)
        if not judged:
            # Window judge unavailable — accept the utterance-ranked best,
            # exactly as the pre-judge fallback always did.
            return to_result(asset, in_s, out_s, score)
        if score >= MIN_WINDOW_SCORE:
            if best is None or score > best[3]:
                best = (asset, in_s, out_s, score)
            if score >= ESCALATE_BELOW:
                break  # good enough — don't spend another download
        elif score >= SALVAGE_SCORE:
            # Near-miss: kept aside so a fully-dry run ships the best of
            # these instead of rolling the whole gap back.
            salvage.append(to_result(asset, in_s, out_s, score))
    if best is None:
        return None
    return to_result(*best)


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
        result, why = await asyncio.to_thread(
            source_interject_clip, project_id, ev, hint)
    except BrainError:
        await fail("the AI brain was unavailable — try again")
        return
    except Exception as exc:  # noqa: BLE001 — never leave an orphaned gap
        await fail(f"sourcing crashed ({type(exc).__name__})")
        raise
    if not result:
        await fail(why or "nothing usable found — try again or add a direction")
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
