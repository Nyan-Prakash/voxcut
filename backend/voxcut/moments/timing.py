"""In-point placement for a chosen window (the "when" inside the source).

The renderer plays `beat_dur` seconds starting at in_s, so timing a clip is
choosing in_s. Two things go wrong when the window is taken as judged:

- The payoff sits at the very end of the window, so the edit cuts away just
  as the thing happens. The frame judge reports where the action is
  (action_at); a late payoff slides the window later so it lands mid-clip.
- A scene cut falls a few frames after the in-point or before the out-point,
  which plays as a flash of the wrong shot. The in-point moves onto the cut.

- The funny thing happens on the wrong word. The planner marks the word the
  visual should land on (punch_word); when the judge can say where the action
  sits, the window slides so the action plays as that word is spoken.

Pure functions — no I/O — so the rules are unit-testable.
"""
from __future__ import annotations

import re

LATE_SHIFT = 0.3     # fraction of the clip to slide when the payoff is at the end
HEAD_GUARD_S = 0.4   # a cut this soon after the in-point reads as a glitch
TAIL_GUARD_S = 0.35  # … and one this close to the out-point
CUT_PAD_S = 0.04     # land just past a cut, never on the blended frame

# Where in a judged window each action_at report sits — the three frames the
# judge was shown (moments.frames.TILE_OFFSETS).
ACTION_FRAC = {"start": 0.15, "middle": 0.5, "end": 0.85}
MAX_ALIGN = 0.35       # never slide more than this fraction of the clip:
                       # most of what plays must be footage the judge saw
MIN_ALIGN_PLAY_S = 1.5  # shorter clips: the word and the action can't miss
PAYOFF_ROOM = 0.25     # the action needs this share of the clip (and at
PAYOFF_ROOM_S = 0.6    # least this long) left to play out after it lands

_NORM = re.compile(r"[^a-z0-9']+")


def _norm(word: str) -> str:
    return _NORM.sub("", (word or "").lower()).strip("'")


def punch_offset(punch_word: str, words: list[tuple[str, float]],
                 beat_start: float) -> float | None:
    """Seconds into the beat at which `punch_word` starts being spoken.

    words: (text, start_s) for the beat's words, in order. The LAST match
    wins — a repeated word is the punch the second time. None when the word
    is empty or is not in the beat (the planner paraphrased)."""
    # The planner sometimes returns a short phrase — land on its first word.
    parts = [t for t in (_norm(w) for w in (punch_word or "").split()) if t]
    if not parts:
        return None
    target = parts[0]
    for text, start_s in reversed(words):
        if _norm(text) == target:
            return round(max(0.0, start_s - beat_start), 3)
    return None


def place_in_point(in_s: float, play_s: float, action_at: str,
                   scenes: list[float], asset_dur: float,
                   punch_off: float | None = None) -> float:
    """Return the in-point to play `play_s` seconds from.

    action_at: the frame judge's report for this window — "start", "middle",
    "end", "throughout" or "none".
    punch_off: seconds into the clip at which the narration's punch word is
    spoken (punch_offset), or None. With a punch word and a located action,
    the window slides so the action lands on the word; otherwise only a
    late ("end") payoff moves the window.
    """
    play_s = max(0.1, play_s)
    max_in = max(0.0, asset_dur - play_s) if asset_dur > 0 else float("inf")
    new_in = in_s
    if (punch_off is not None and action_at in ACTION_FRAC
            and play_s >= MIN_ALIGN_PLAY_S):
        # Land the action on the word — but not so late that the edit cuts
        # away before it has played out.
        latest = play_s - max(PAYOFF_ROOM_S, PAYOFF_ROOM * play_s)
        target = max(0.0, min(punch_off, latest))
        shift = ACTION_FRAC[action_at] * play_s - target
        limit = MAX_ALIGN * play_s
        new_in = in_s + max(-limit, min(shift, limit))
    elif action_at == "end":
        new_in = in_s + LATE_SHIFT * play_s
    new_in = max(0.0, min(new_in, max_in))

    # Head: a cut just after the in-point → start on the cut instead.
    head = next((s for s in scenes
                 if new_in < s <= new_in + HEAD_GUARD_S), None)
    if head is not None and head + CUT_PAD_S <= max_in:
        new_in = head + CUT_PAD_S
    else:
        # Tail: a cut just before the out-point → end on it, provided that
        # does not create a head flash or run before the start of the file.
        end = new_in + play_s
        tail = next((s for s in reversed(scenes)
                     if end - TAIL_GUARD_S <= s < end), None)
        if tail is not None:
            cand = new_in - (end - tail) - CUT_PAD_S
            clean_head = not any(cand < s <= cand + HEAD_GUARD_S
                                 for s in scenes)
            if cand >= 0.0 and clean_head:
                new_in = cand
    return round(max(0.0, min(new_in, max_in)), 3)
