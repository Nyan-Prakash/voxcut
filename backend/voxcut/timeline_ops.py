"""Structural timeline edits: cut (split) events and carve new segments.

Cuts snap to word boundaries so the edit stays on speech rhythm, and every
structural edit keeps beats.json and edl.json in lockstep (one beat per
event) — that 1:1 mapping is what lets a single event be re-planned and
re-sourced on its own (reroll).
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException
from sqlmodel import select
from ulid import ULID

from .config import settings
from .db import session_scope
from .edl_store import load_edl, save_edl
from .models import Project, Word, new_id

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


# ------------------------------------------------------- ripple edits (Interject)
# Unlike split/add_segment (which preserve total duration), these insert or
# remove TIME: the VO master is re-rendered with silence spliced in/out and
# everything keyed to the timeline clock — events, beats, word times,
# silences, waveform peaks, music regions, Project.duration_s — moves with it.
# Each op writes a struct.v{n}.json snapshot (struct_store) so undo restores
# the whole world, not just edl.json.

EPS = 1e-6
# Technical guard, NOT word snapping: a cut this close to an event edge lands
# ON the edge, so a ripple insert can never leave a sliver clip behind.
EDGE_SNAP_S = 0.1
INTERJECT_FLAG = "interject"
PLACEHOLDER_GAP_S = 2.0


def _read_doc(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


def make_interject_event(start_s: float, end_s: float) -> dict:
    """Placeholder event for a freshly inserted gap: renders as a dark card
    (no gap_unfilled flag — _absorb_gaps must never eat the inserted time)
    until the interject job fills it with an unmuted clip."""
    return {
        "id": f"ev_{ULID()}",
        "beat_id": None,
        "start_s": round(start_s, 3),
        "end_s": round(end_s, 3),
        "kind": "clip_reaction",
        "asset_id": None,
        "source": None,
        "queries": [],
        "joke_queries": [],
        "treatment": {"layout": "fullscreen", "zoom": {"start": 1.0, "end": 1.0},
                      "transition_in": "cut", "fit": "cover"},
        "audio": {"mode": "keep", "duck_db": -18},
        "flags": ["user_added", INTERJECT_FLAG, "sourcing"],
        "locked": False,
    }


def _split_beat_exact(beats_doc: dict, words: list[dict], at_s: float) -> str | None:
    """Split the beat straddling at_s exactly there, partitioning its words by
    start time. Returns the new (second) beat's id, or None when no split
    happened (cut in a beat's leading/trailing silence → clamp instead;
    split_event has the same tolerance when word snapping fails)."""
    for b in beats_doc["beats"]:
        if not (b["start_s"] + EPS < at_s < b["end_s"] - EPS):
            continue
        k = next((w["idx"] for w in words
                  if b["word_start_idx"] <= w["idx"] <= b["word_end_idx"]
                  and w["start_s"] >= at_s), None)
        if k is not None and k > b["word_start_idx"]:
            second = dict(b)
            second["id"] = new_id("bt")
            second["word_start_idx"] = k
            second["start_s"] = at_s
            second["text"] = _beat_text(words, k, b["word_end_idx"])
            second["gist"] = second["text"][:120]
            b["word_end_idx"] = k - 1
            b["end_s"] = at_s
            b["text"] = _beat_text(words, b["word_start_idx"], k - 1)
            b["gist"] = b["text"][:120]
            beats_doc["beats"].insert(beats_doc["beats"].index(b) + 1, second)
            return second["id"]
        if k is None:
            b["end_s"] = at_s      # all words end before the cut
        else:
            b["start_s"] = at_s    # all words start after the cut (will shift)
        return None
    return None


def _merge_silences(silences: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for s, e in sorted((s, e) for s, e in silences if e - s > 0.01):
        if out and s <= out[-1][1] + 0.01:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [[round(s, 3), round(e, 3)] for s, e in out]


def insert_time(project_id: str, at_s: float, dur_s: float,
                new_event: dict | None = None,
                extend_event_id: str | None = None) -> dict:
    """Ripple-insert dur_s of brand-new time at at_s. The event straddling
    at_s splits exactly there (no word snapping — Interject cuts land where
    clicked, modulo the sliver guard). The optional new_event fills the gap;
    its start/end are overwritten to [at_s, at_s + dur_s]. Alternatively
    extend_event_id names an event ENDING at at_s that absorbs the inserted
    time itself (gap growth) — all inside this op's single undo step."""
    if dur_s <= EPS:
        raise HTTPException(400, "insert duration must be positive")
    edl = load_edl(project_id)
    with session_scope() as db:
        p = db.get(Project, project_id)
        master = p.voiceover_path if p else None
        total = p.duration_s if p else 0.0
    if not master or not Path(master).exists():
        raise HTTPException(400, "no voiceover master to edit")
    at_s = round(min(max(at_s, 0.0), total), 3)
    dur_s = round(dur_s, 3)
    pdir = settings().project_dir(project_id)
    version = edl.get("version", 0)

    # Sliver guard: land on the nearest event edge when the click is
    # imperceptibly close to one.
    for ev in sorted(edl["events"], key=lambda e: e["start_s"]):
        if abs(at_s - ev["start_s"]) <= EDGE_SNAP_S:
            at_s = round(ev["start_s"], 3)
            break
        if abs(at_s - ev["end_s"]) <= EDGE_SNAP_S:
            at_s = round(ev["end_s"], 3)
            break

    dirty: list[str] = []

    # --- events: split the straddler exactly at at_s ---
    host = next((e for e in edl["events"]
                 if e["start_s"] + EPS < at_s < e["end_s"] - EPS), None)
    tail = None
    if host:
        src_a, src_b = _split_source(host, at_s)
        tail = {k: (json.loads(json.dumps(host[k]))
                    if isinstance(host.get(k), (dict, list)) else host.get(k))
                for k in COPY_KEYS if k in host}
        tail.update({
            "id": f"ev_{ULID()}",
            "beat_id": host.get("beat_id"),
            "start_s": at_s,
            "end_s": host["end_s"],
            "asset_id": host.get("asset_id"),
            "source": src_b,
            "flags": [f for f in host.get("flags", []) if f != "auto"] + ["user_cut"],
            "locked": False,
        })
        host["end_s"] = at_s
        if src_a:
            host["source"] = src_a
        edl["events"].insert(edl["events"].index(host) + 1, tail)
        dirty += [host["id"], tail["id"]]

    # --- beats: split/clamp the straddler, exactly at at_s ---
    beats_doc = _beats_doc(project_id)
    if beats_doc:
        words = _words(project_id)
        second_beat_id = _split_beat_exact(beats_doc, words, at_s)
        if tail is not None and second_beat_id:
            tail["beat_id"] = second_beat_id

    # --- render the new VO to a per-version file (undo = pointer flip) ---
    from .media.vo_edit import insert_silence
    from .struct_store import vo_version_path
    new_master = vo_version_path(project_id, version + 1)
    insert_silence(Path(master), at_s, dur_s, new_master)

    # --- snapshot the pre-op world, then commit every mutation ---
    from .struct_store import capture_struct, write_struct_snapshot
    if new_event is not None:
        new_event["start_s"] = at_s
        new_event["end_s"] = round(at_s + dur_s, 3)
        dirty.append(new_event["id"])
    if extend_event_id:
        dirty.append(extend_event_id)
    snap = capture_struct(project_id, dirty_event_ids=dirty)
    write_struct_snapshot(project_id, version, snap)

    for e in edl["events"]:
        if e["start_s"] >= at_s - EPS:
            e["start_s"] = round(e["start_s"] + dur_s, 3)
            e["end_s"] = round(e["end_s"] + dur_s, 3)
    if new_event is not None:
        pos = next((i for i, e in enumerate(edl["events"])
                    if e["start_s"] >= at_s + dur_s - EPS), len(edl["events"]))
        edl["events"].insert(pos, new_event)
    elif extend_event_id:
        grown = next((e for e in edl["events"] if e["id"] == extend_event_id), None)
        if grown:
            grown["end_s"] = round(grown["end_s"] + dur_s, 3)

    if beats_doc:
        for b in beats_doc["beats"]:
            if b["start_s"] >= at_s - EPS:
                b["start_s"] = round(b["start_s"] + dur_s, 3)
                b["end_s"] = round(b["end_s"] + dur_s, 3)
        _save_beats(project_id, beats_doc)

    with session_scope() as db:
        rows = db.exec(select(Word).where(Word.project_id == project_id)).all()
        for w in rows:
            if w.start_s >= at_s - EPS:
                w.start_s = round(w.start_s + dur_s, 3)
                w.end_s = round(w.end_s + dur_s, 3)
                db.add(w)
        p = db.get(Project, project_id)
        if p:
            p.voiceover_path = str(new_master)
            p.duration_s = round((p.duration_s or 0.0) + dur_s, 3)
            db.add(p)
        db.commit()

    sil_doc = _read_doc(pdir / "silences.json")
    if sil_doc is not None:
        moved = []
        for s, e in sil_doc.get("silences", []):
            if s >= at_s - EPS:
                moved.append([s + dur_s, e + dur_s])
            elif e > at_s:
                moved.append([s, e + dur_s])   # straddler spans the new gap
            else:
                moved.append([s, e])
        moved.append([at_s, at_s + dur_s])     # the gap IS a silence
        sil_doc["silences"] = _merge_silences(moved)
        (pdir / "silences.json").write_text(json.dumps(sil_doc))

    wf = _read_doc(pdir / "waveform.json")
    if wf is not None and wf.get("peaks"):
        bps = wf.get("buckets_per_s", 20)
        i = min(len(wf["peaks"]), max(0, int(round(at_s * bps))))
        wf["peaks"][i:i] = [0.0] * int(round(dur_s * bps))
        (pdir / "waveform.json").write_text(json.dumps(wf))

    with session_scope() as db:
        p = db.get(Project, project_id)
        cfg = json.loads(p.settings or "{}") if p else {}
    regions = (cfg.get("music") or {}).get("regions") or []
    if regions:
        for r in regions:
            if r.get("start_s", 0) >= at_s - EPS:
                r["start_s"] = round(r["start_s"] + dur_s, 3)
                r["end_s"] = round(r["end_s"] + dur_s, 3)
            elif r.get("end_s", 0) > at_s:
                r["end_s"] = round(r["end_s"] + dur_s, 3)  # play through, ducked
        with session_scope() as db:
            p = db.get(Project, project_id)
            if p:
                s = json.loads(p.settings or "{}")
                s.setdefault("music", {})["regions"] = regions
                p.settings = json.dumps(s)
                db.add(p)
                db.commit()

    (pdir / "highlights.json").unlink(missing_ok=True)

    edl = save_edl(project_id, edl)
    _mark_dirty(project_id, dirty)
    return {"edl": edl, "at_s": at_s, "dur_s": dur_s,
            "new_event_id": new_event["id"] if new_event else None,
            "split_event_ids": [host["id"], tail["id"]] if host else []}


def remove_time(project_id: str, start_s: float, end_s: float) -> dict:
    """Ripple-remove [start_s, end_s] from the timeline — the inverse of
    insert_time, only ever aimed at a known interject gap (rollback, delete,
    resize), never at arbitrary narration."""
    edl = load_edl(project_id)
    with session_scope() as db:
        p = db.get(Project, project_id)
        master = p.voiceover_path if p else None
        total = p.duration_s if p else 0.0
    if not master or not Path(master).exists():
        raise HTTPException(400, "no voiceover master to edit")
    a = round(max(0.0, start_s), 3)
    b = round(min(end_s, total), 3)
    d = round(b - a, 3)
    if d <= EPS:
        raise HTTPException(400, "nothing to remove")
    pdir = settings().project_dir(project_id)
    version = edl.get("version", 0)

    def rip(t: float) -> float:
        return t if t <= a + EPS else (a if t <= b else round(t - d, 3))

    # --- render the new VO first (non-destructive side file) ---
    from .media.vo_edit import remove_span
    from .struct_store import capture_struct, vo_version_path, write_struct_snapshot
    new_master = vo_version_path(project_id, version + 1)
    remove_span(Path(master), a, b, new_master)

    removed_ids = [e["id"] for e in edl["events"]
                   if rip(e["end_s"]) - rip(e["start_s"]) < MIN_PIECE_S]
    trimmed_ids = [e["id"] for e in edl["events"]
                   if e["id"] not in removed_ids
                   and max(0.0, min(e["end_s"], b) - max(e["start_s"], a)) > EPS]
    snap = capture_struct(project_id, dirty_event_ids=removed_ids + trimmed_ids)
    write_struct_snapshot(project_id, version, snap)

    kept = []
    for e in edl["events"]:
        if e["id"] in removed_ids:
            continue
        overlap = max(0.0, min(e["end_s"], b) - max(e["start_s"], a))
        e["start_s"], e["end_s"] = rip(e["start_s"]), rip(e["end_s"])
        if overlap > EPS and e.get("source"):
            out_s = float(e["source"].get("out_s", 0.0))
            in_s = float(e["source"].get("in_s", 0.0))
            e["source"]["out_s"] = round(max(in_s, out_s - overlap), 3)
        kept.append(e)
    edl["events"] = kept

    beats_doc = _beats_doc(project_id)
    if beats_doc:
        beats_kept = []
        for bt in beats_doc["beats"]:
            ns, ne = rip(bt["start_s"]), rip(bt["end_s"])
            if ne - ns < 0.05:
                continue
            bt["start_s"], bt["end_s"] = ns, ne
            beats_kept.append(bt)
        beats_doc["beats"] = beats_kept
        _save_beats(project_id, beats_doc)

    with session_scope() as db:
        rows = db.exec(select(Word).where(Word.project_id == project_id)).all()
        for w in rows:
            ns, ne = rip(w.start_s), rip(w.end_s)
            if ns != w.start_s or ne != w.end_s:
                w.start_s, w.end_s = ns, max(ns, ne)
                db.add(w)
        p = db.get(Project, project_id)
        if p:
            p.voiceover_path = str(new_master)
            p.duration_s = round(max(0.0, (p.duration_s or 0.0) - d), 3)
            db.add(p)
        db.commit()

    sil_doc = _read_doc(pdir / "silences.json")
    if sil_doc is not None:
        moved = [[rip(s), rip(e)] for s, e in sil_doc.get("silences", [])]
        sil_doc["silences"] = _merge_silences(moved)
        (pdir / "silences.json").write_text(json.dumps(sil_doc))

    wf = _read_doc(pdir / "waveform.json")
    if wf is not None and wf.get("peaks"):
        bps = wf.get("buckets_per_s", 20)
        i0 = max(0, int(round(a * bps)))
        i1 = min(len(wf["peaks"]), int(round(b * bps)))
        del wf["peaks"][i0:i1]
        (pdir / "waveform.json").write_text(json.dumps(wf))

    with session_scope() as db:
        p = db.get(Project, project_id)
        cfg = json.loads(p.settings or "{}") if p else {}
    regions = (cfg.get("music") or {}).get("regions") or []
    if regions:
        kept_regions = []
        for r in regions:
            ns, ne = rip(r.get("start_s", 0)), rip(r.get("end_s", 0))
            if ne - ns >= 1.0:
                r["start_s"], r["end_s"] = ns, ne
                kept_regions.append(r)
        with session_scope() as db:
            p = db.get(Project, project_id)
            if p:
                s = json.loads(p.settings or "{}")
                s.setdefault("music", {})["regions"] = kept_regions
                p.settings = json.dumps(s)
                db.add(p)
                db.commit()

    (pdir / "highlights.json").unlink(missing_ok=True)

    edl = save_edl(project_id, edl)
    _mark_dirty(project_id, removed_ids + trimmed_ids)
    return {"edl": edl, "removed": removed_ids, "start_s": a, "dur_s": d}


def resize_gap(project_id: str, event_id: str, new_dur_s: float) -> dict:
    """Grow or shrink an interject gap to fit the clip the AI picked. Growth
    inserts time at the gap's end; shrink removes the gap's tail — either way
    the narration on both sides is untouched."""
    edl = load_edl(project_id)
    ev = next((e for e in edl["events"] if e["id"] == event_id), None)
    if not ev:
        raise HTTPException(404, "event not found")
    cur = ev["end_s"] - ev["start_s"]
    delta = round(new_dur_s - cur, 3)
    if abs(delta) < 0.05:
        return {"edl": edl, "dur_s": cur}
    if delta > 0:
        # The gap absorbs the inserted time inside insert_time's own save —
        # one atomic undo step, never an intermediate state with a hole.
        res = insert_time(project_id, ev["end_s"], delta,
                          extend_event_id=event_id)
    else:
        # remove_time's ripple math already trims the gap event to new_dur_s.
        res = remove_time(project_id, ev["end_s"] + delta, ev["end_s"])
    return {"edl": res["edl"], "dur_s": new_dur_s}
