"""Facecam: which lines show the speaker and which show a clip.

When the upload is a video, every beat is still planned as a clip (so any
line can be flipped later), and then a share of the timeline is handed back
to the facecam. The planner rates each line's `visual_need`; the lines that
need footage least go to the face first, until the project's face share is
met. Pure functions — no I/O.
"""
from __future__ import annotations

DEFAULT_FACE_SHARE = 0.5
NEED_RANK = {"low": 0, "medium": 1, "high": 2}
MIN_FACE_RUN_S = 1.5   # a shorter flash of face between two clips is choppy
MIN_CLIP_RUN_S = 1.0   # … and so is a blink of clip between two face shots
FLAG = "facecam"


def is_facecam(ev: dict) -> bool:
    return bool(ev.get("facecam"))


def set_facecam(ev: dict, on: bool) -> None:
    flags = [f for f in ev.get("flags", []) if f != FLAG]
    if on:
        ev["facecam"] = True
        flags.append(FLAG)
    else:
        ev.pop("facecam", None)
        # Back to a clip: with no footage chosen yet it is a gap to fill.
        if not ev.get("asset_id") and "gap_unfilled" not in flags:
            flags.append("gap_unfilled")
    ev["flags"] = flags


def _dur(ev: dict) -> float:
    return max(0.0, ev["end_s"] - ev["start_s"])


def _runs(events: list[dict]) -> list[tuple[int, int, bool]]:
    """Consecutive events with the same face/clip state: (first, last, face)."""
    runs, i = [], 0
    while i < len(events):
        j = i
        while j + 1 < len(events) and is_facecam(events[j + 1]) == is_facecam(events[i]):
            j += 1
        runs.append((i, j, is_facecam(events[i])))
        i = j + 1
    return runs


def assign_facecam(events: list[dict], share: float) -> list[dict]:
    """Mark events as facecam until ~`share` of the runtime shows the face.

    Lowest visual_need first; among equals the longer line goes to the face
    (fewer, longer face shots beat many short ones). Then one smoothing pass
    removes face or clip slivers stranded between the other kind — the
    operator does not want choppy back-and-forth."""
    share = max(0.0, min(1.0, float(share)))
    for ev in events:
        set_facecam(ev, False) if is_facecam(ev) else None
    total = sum(_dur(e) for e in events)
    if not events or total <= 0 or share <= 0:
        return events
    order = sorted(range(len(events)), key=lambda i: (
        NEED_RANK.get(events[i].get("visual_need") or "medium", 1),
        -_dur(events[i]), i))
    face_s, target = 0.0, share * total
    for i in order:
        if face_s >= target - 1e-6:
            break
        # Don't overshoot badly on the last pick.
        if face_s + _dur(events[i]) - target > target - face_s and face_s > 0:
            continue
        set_facecam(events[i], True)
        face_s += _dur(events[i])

    if 0 < share < 1:
        for first, last, face in _runs(events):
            inner = 0 < first and last < len(events) - 1
            run_s = sum(_dur(e) for e in events[first:last + 1])
            if inner and face and run_s < MIN_FACE_RUN_S:
                for e in events[first:last + 1]:
                    set_facecam(e, False)
            elif inner and not face and run_s < MIN_CLIP_RUN_S:
                for e in events[first:last + 1]:
                    set_facecam(e, True)
    for ev in events:
        if is_facecam(ev):   # a face shot is never a gap to fill
            ev["flags"] = [f for f in ev["flags"] if f != "gap_unfilled"]
    return events


def face_share_of(events: list[dict]) -> float:
    total = sum(_dur(e) for e in events)
    return round(sum(_dur(e) for e in events if is_facecam(e)) / total, 3) if total else 0.0
