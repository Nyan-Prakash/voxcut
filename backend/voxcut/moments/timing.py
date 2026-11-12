"""In-point placement for a chosen window (the "when" inside the source).

The renderer plays `beat_dur` seconds starting at in_s, so timing a clip is
choosing in_s. Two things go wrong when the window is taken as judged:

- The payoff sits at the very end of the window, so the edit cuts away just
  as the thing happens. The frame judge reports where the action is
  (action_at); a late payoff slides the window later so it lands mid-clip.
- A scene cut falls a few frames after the in-point or before the out-point,
  which plays as a flash of the wrong shot. The in-point moves onto the cut.

Pure functions — no I/O — so the rules are unit-testable.
"""
from __future__ import annotations

LATE_SHIFT = 0.3     # fraction of the clip to slide when the payoff is at the end
HEAD_GUARD_S = 0.4   # a cut this soon after the in-point reads as a glitch
TAIL_GUARD_S = 0.35  # … and one this close to the out-point
CUT_PAD_S = 0.04     # land just past a cut, never on the blended frame


def place_in_point(in_s: float, play_s: float, action_at: str,
                   scenes: list[float], asset_dur: float) -> float:
    """Return the in-point to play `play_s` seconds from.

    action_at: the frame judge's report for this window — "start", "middle",
    "end", "throughout" or "none". Only "end" moves the window.
    """
    play_s = max(0.1, play_s)
    max_in = max(0.0, asset_dur - play_s) if asset_dur > 0 else float("inf")
    new_in = in_s
    if action_at == "end":
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
