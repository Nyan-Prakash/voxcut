"""Structural timeline edits: cut (split) events and carve new segments.

Cuts snap to word boundaries so the edit stays on speech rhythm, and every
structural edit keeps beats.json and edl.json in lockstep (one beat per
event) — that 1:1 mapping is what lets a single event be re-planned and
re-sourced on its own (reroll).
"""
from __future__ import annotations

import json

from fastapi import HTTPException
from sqlmodel import select
from ulid import ULID

from .config import settings
from .db import session_scope
from .edl_store import load_edl, save_edl
from .models import Word, new_id

MIN_PIECE_S = 0.25   # refuse cuts that leave a sliver
COPY_KEYS = ("kind", "queries", "joke_queries", "audio", "treatment",
             "moment_candidates", "finalists", "source_candidates",
             "finalist_asset_ids")


# ---------------------------------------------------------------- words/beats

def _words(project_id: str) -> list[dict]:
    with session_scope() as db:
        rows = db.exec(select(Word).where(Word.project_id == project_id)
                       .order_by(Word.idx)).all()
    return [{"idx": w.idx, "text": (w.corrected_text or w.text).strip(),
             "start_s": w.start_s, "end_s": w.end_s} for w in rows]


def _beats_doc(project_id: str) -> dict | None:
    p = settings().project_dir(project_id) / "beats.json"
    return json.loads(p.read_text()) if p.exists() else None


def _save_beats(project_id: str, doc: dict) -> None:
    doc["version"] = doc.get("version", 1) + 1
    (settings().project_dir(project_id) / "beats.json").write_text(
        json.dumps(doc, indent=2))


def _beat_text(words: list[dict], w0: int, w1: int) -> str:
    by = {w["idx"]: w["text"] for w in words}
    return " ".join(by[k] for k in range(w0, w1 + 1) if k in by)


def _cut_time(words: list[dict], second_first_idx: int, fallback: float) -> float:
    """Cut between the previous word and the one starting the second half."""
    by = {w["idx"]: w for w in words}
    w = by.get(second_first_idx)
    prev = by.get(second_first_idx - 1)
    if not w:
        return round(fallback, 3)
    if prev and prev["end_s"] < w["start_s"]:
        return round((prev["end_s"] + w["start_s"]) / 2, 3)
    return round(w["start_s"], 3)


def _snap_word(words: list[dict], t: float, lo_idx: int, hi_idx: int) -> int | None:
    """Word index in (lo_idx, hi_idx] whose start is nearest to t — it will
    START the piece to the right of the cut. None when no valid split exists."""
    inside = [w for w in words if lo_idx < w["idx"] <= hi_idx]
    if not inside:
        return None
    return min(inside, key=lambda w: abs(w["start_s"] - t))["idx"]


def _mark_dirty(project_id: str, event_ids: list[str]) -> None:
    for sub in ("segments", "segments_full"):
        seg_dir = settings().project_dir(project_id) / sub
        for eid in event_ids:
            (seg_dir / f"{eid}.mp4").unlink(missing_ok=True)
            (seg_dir / f"thumb_{eid}.jpg").unlink(missing_ok=True)


def _split_source(ev: dict, cut_t: float) -> tuple[dict | None, dict | None]:
    """Split an event's source window at the cut: the same footage keeps
    playing across the cut, so the user can reroll either half."""
    src = ev.get("source")
    if not ev.get("asset_id") or not src:
        return None, None
    offset = cut_t - ev["start_s"]
    in_s = float(src.get("in_s", 0.0))
    out_s = float(src.get("out_s", in_s))
    mid = min(out_s, in_s + offset)
    a = dict(src, out_s=round(mid, 3))
    b = dict(src, in_s=round(mid, 3), out_s=round(max(out_s, mid), 3))
    return a, b


# -------------------------------------------------------------------- split

def split_event(project_id: str, event_id: str, at_s: float) -> dict:
    """Cut an event in two at ~at_s (snapped to a word boundary). The matching
    beat splits with it; both halves keep the same footage until rerolled."""
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    if not (ev["start_s"] + MIN_PIECE_S <= at_s <= ev["end_s"] - MIN_PIECE_S):
        raise HTTPException(400, "cut point too close to the event edge")

    words = _words(project_id)
    beats_doc = _beats_doc(project_id)
    cut_t = round(at_s, 3)
    new_beat_id = ev.get("beat_id")

    # Split the beat (word-snapped) whenever the event maps to one.
    beat = None
    if beats_doc and ev.get("beat_id"):
        beat = next((b for b in beats_doc["beats"] if b["id"] == ev["beat_id"]), None)
    if beat and words:
        w_idx = _snap_word(words, at_s, beat["word_start_idx"], beat["word_end_idx"])
        if w_idx is not None:
            snapped = _cut_time(words, w_idx, at_s)
            if (beat["start_s"] + MIN_PIECE_S <= snapped
                    <= beat["end_s"] - MIN_PIECE_S):
                cut_t = snapped
                second = dict(beat)
                second["id"] = new_id("bt")
                second["word_start_idx"] = w_idx
                second["start_s"] = cut_t
                second["text"] = _beat_text(words, w_idx, beat["word_end_idx"])
                second["gist"] = second["text"][:120]
                beat["word_end_idx"] = w_idx - 1
                beat["end_s"] = cut_t
                beat["text"] = _beat_text(words, beat["word_start_idx"], w_idx - 1)
                beat["gist"] = beat["text"][:120]
                i = beats_doc["beats"].index(beat)
                beats_doc["beats"].insert(i + 1, second)
                _save_beats(project_id, beats_doc)
                new_beat_id = second["id"]

    src_a, src_b = _split_source(ev, cut_t)
    tail = {k: (json.loads(json.dumps(ev[k])) if isinstance(ev.get(k), (dict, list))
                else ev.get(k))
            for k in COPY_KEYS if k in ev}
    tail.update({
        "id": f"ev_{ULID()}",
        "beat_id": new_beat_id,
        "start_s": cut_t,
        "end_s": ev["end_s"],
        "asset_id": ev.get("asset_id"),
        "source": src_b,
        "flags": [f for f in ev.get("flags", []) if f != "auto"] + ["user_cut"],
        "locked": False,
    })
    ev["end_s"] = cut_t
    if src_a:
        ev["source"] = src_a
    idx = edl["events"].index(ev)
    edl["events"].insert(idx + 1, tail)

    edl = save_edl(project_id, edl)
    _mark_dirty(project_id, [ev["id"], tail["id"]])
    return {"edl": edl, "cut_s": cut_t,
            "event_ids": [ev["id"], tail["id"]], "new_event_id": tail["id"]}


# -------------------------------------------------------------- add segment

def add_segment(project_id: str, start_s: float, end_s: float) -> dict:
    """Carve [start_s, end_s] (word-snapped) out of the timeline and insert a
    fresh empty segment there, with its own beat, ready to search or reroll.
    Overlapping events are trimmed/split/deleted; their beats follow."""
    if end_s - start_s < 2 * MIN_PIECE_S:
        raise HTTPException(400, "segment too short")
    edl = load_edl(project_id)
    events = sorted(edl["events"], key=lambda e: e["start_s"])

    # Split any event that straddles a boundary, so overlap handling below
    # only ever sees whole events inside the range. Cuts snap to words, so
    # track where they actually landed.
    snapped = [round(start_s, 3), round(end_s, 3)]
    for i, bound in enumerate((start_s, end_s)):
        host = next((e for e in events
                     if e["start_s"] + MIN_PIECE_S <= bound <= e["end_s"] - MIN_PIECE_S),
                    None)
        if host:
            res = split_event(project_id, host["id"], bound)
            edl = res["edl"]
            events = sorted(edl["events"], key=lambda e: e["start_s"])
            snapped[i] = res["cut_s"]

    lo, hi = snapped
    if hi - lo < 2 * MIN_PIECE_S:
        raise HTTPException(400, "segment too short after word snapping")
    mid = lambda e: (e["start_s"] + e["end_s"]) / 2  # noqa: E731
    inside = [e for e in events if lo - 0.05 <= e["start_s"] and e["end_s"] <= hi + 0.05
              and lo <= mid(e) <= hi]
    if inside:
        lo = min(e["start_s"] for e in inside)
        hi = max(e["end_s"] for e in inside)

    # One beat for the new segment: merge/absorb the beats of removed events.
    words = _words(project_id)
    beats_doc = _beats_doc(project_id)
    beat_id = None
    if beats_doc and words:
        removed_beats = [b for b in beats_doc["beats"]
                         if b["id"] in {e.get("beat_id") for e in inside}]
        if removed_beats:
            w0 = min(b["word_start_idx"] for b in removed_beats)
            w1 = max(b["word_end_idx"] for b in removed_beats)
            merged = dict(removed_beats[0])
            merged["id"] = new_id("bt")
            merged.update({
                "word_start_idx": w0, "word_end_idx": w1,
                "start_s": lo, "end_s": hi,
                "text": _beat_text(words, w0, w1),
                "emphasis": max(b.get("emphasis", 0.4) for b in removed_beats),
                "locked": False,
            })
            merged["gist"] = merged["text"][:120]
            keep = [b for b in beats_doc["beats"] if b not in removed_beats]
            pos = next((i for i, b in enumerate(keep) if b["start_s"] >= hi), len(keep))
            keep.insert(pos, merged)
            beats_doc["beats"] = keep
            _save_beats(project_id, beats_doc)
            beat_id = merged["id"]

    fresh = {
        "id": f"ev_{ULID()}",
        "beat_id": beat_id,
        "start_s": lo,
        "end_s": hi,
        "kind": "broll",
        "asset_id": None,
        "source": None,
        "queries": [],
        "joke_queries": [],
        "treatment": {"layout": "fullscreen", "zoom": {"start": 1.0, "end": 1.06},
                      "transition_in": "cut", "fit": "cover"},
        "audio": {"mode": "mute", "duck_db": -18},
        "flags": ["user_added", "gap_unfilled"],
        "locked": False,
    }
    removed_ids = [e["id"] for e in inside]
    kept = [e for e in edl["events"] if e["id"] not in removed_ids]
    pos = next((i for i, e in enumerate(kept) if e["start_s"] >= lo), len(kept))
    kept.insert(pos, fresh)
    edl["events"] = kept

    edl = save_edl(project_id, edl)
    _mark_dirty(project_id, removed_ids + [fresh["id"]])
    return {"edl": edl, "new_event_id": fresh["id"], "removed": removed_ids}
