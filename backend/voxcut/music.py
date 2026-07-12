"""Music engine: operator's track library, duck envelope, theme suggestions.

Post-mortem rules (docs/roadmap.md): tracks come from the OPERATOR only —
nothing bundled, nothing fetched. Suggestion runs only when explicitly asked,
proposes regions from the operator's own mood-tagged tracks, and everything
it places is a normal editable region.
"""
from __future__ import annotations

import json
import math

from .config import settings
from .media.probe import ffprobe

MOODS = ["chill", "whimsical", "hype", "tense", "dramatic", "sad", "angry"]
# The bed: normal narration plays these tracks at natural length, in order.
BASELINE_MOODS = ["chill", "whimsical"]
# Emotional sections interrupt the bed only when sustained this long.
EMO_MIN_S = 8.0
# No track tagged with the wanted mood → try these before giving up
# (giving up = the normal bed just keeps playing).
EMO_FALLBACK = {"angry": ["tense", "dramatic"],
                "sad": ["tense"],
                "dramatic": ["tense", "angry"]}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}

# Music plays at one constant level — ducking/swell removed entirely
# (operator preference 2026-07-12).
DEFAULT_MUSIC = {"enabled": True, "volume_db": -25.0, "regions": []}


def music_dir():
    d = settings().data_dir / "music"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path():
    return music_dir() / "index.json"


def _load_index() -> dict:
    p = _index_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save_index(idx: dict) -> None:
    _index_path().write_text(json.dumps(idx, indent=2))


def list_tracks() -> list[dict]:
    idx = _load_index()
    out = []
    for f in sorted(music_dir().iterdir()):
        if f.suffix.lower() not in AUDIO_EXTS:
            continue
        meta = idx.get(f.name, {})
        dur = meta.get("duration_s")
        if dur is None:
            try:
                dur = float(ffprobe(f).get("format", {}).get("duration") or 0)
            except Exception:  # noqa: BLE001
                dur = 0.0
            idx[f.name] = {**meta, "duration_s": round(dur, 2)}
        out.append({"name": f.name, "duration_s": idx[f.name]["duration_s"],
                    "mood": meta.get("mood"), "size_bytes": f.stat().st_size})
    _save_index(idx)
    return out


def set_mood(name: str, mood: str | None) -> None:
    idx = _load_index()
    idx[name] = {**idx.get(name, {}), "mood": mood}
    _save_index(idx)


def track_path(name: str):
    p = music_dir() / name
    return p if p.exists() and p.suffix.lower() in AUDIO_EXTS else None


# ---------------------------------------------------------------- suggestion

def _beat_emotion(b: dict) -> str:
    """One label per beat: which music the narration wants right now.
    'normal' = the baseline bed; only sustained runs of the others
    interrupt it (rants → angry, sad stretches → sad, heavy → dramatic)."""
    if b.get("rhythm") == "escalation":  # pile-on rant
        return "angry"
    tone = b.get("tone", "neutral")
    emph = float(b.get("emphasis", 0.4))
    if tone in ("hype", "sarcastic") and emph >= 0.7:
        return "angry"
    if tone == "serious":
        return "dramatic" if emph >= 0.6 else "sad"
    return "normal"


def suggest_regions(beats: list[dict], tracks: list[dict],
                    duration: float) -> list[dict]:
    """One normal song plays as the bed — uncut, resuming after interruptions,
    chaining to the next normal song only when it naturally ends. Sustained
    emotional stretches (>= EMO_MIN_S) cut to a matching angry/sad/dramatic
    track, then the bed picks up where it left off (region offset_s)."""
    tagged = [t for t in tracks if t.get("mood")]
    if not tagged or duration <= 0:
        return []
    by_mood: dict[str, list[dict]] = {}
    for t in tagged:
        by_mood.setdefault(t["mood"], []).append(t)
    normal_pool = [t for m in BASELINE_MOODS for t in by_mood.get(m, [])] or tagged

    rr: dict[str, int] = {}

    def pick_emotional(mood: str) -> dict | None:
        for m in [mood, *EMO_FALLBACK.get(mood, [])]:
            pool = by_mood.get(m)
            if pool:
                i = rr.get(m, 0)
                rr[m] = i + 1
                return pool[i % len(pool)]
        return None

    # 1. Beat labels → contiguous sections.
    sections: list[dict] = []
    for b in beats:
        mood = _beat_emotion(b)
        if sections and sections[-1]["mood"] == mood:
            sections[-1]["end_s"] = b["end_s"]
        else:
            sections.append({"mood": mood, "start_s": b["start_s"],
                             "end_s": b["end_s"]})
    if not sections:
        return []
    # 2. Too-short emotional blips (and moods with no track at all) stay on
    #    the bed; then merge adjacent normals back together.
    for s in sections:
        if s["mood"] != "normal" and (
                s["end_s"] - s["start_s"] < EMO_MIN_S
                or pick_emotional(s["mood"]) is None):
            s["mood"] = "normal"
    rr.clear()  # the probe picks above must not skew round-robin
    merged: list[dict] = []
    for s in sections:
        if merged and merged[-1]["mood"] == s["mood"]:
            merged[-1]["end_s"] = s["end_s"]
        else:
            merged.append(s)
    merged[0]["start_s"] = 0.0
    merged[-1]["end_s"] = max(duration, merged[-1]["end_s"])

    # 3. Lay out regions. The bed keeps its own position (track index +
    #    offset) across interruptions — it resumes, never restarts.
    regions: list[dict] = []
    bed_i, bed_off = 0, 0.0
    rid = 0

    def emit(track: dict, t0: float, t1: float, offset: float = 0.0) -> None:
        nonlocal rid
        regions.append({"id": f"mr_{rid}", "file": track["name"],
                        "start_s": round(t0, 3), "end_s": round(t1, 3),
                        "gain_db": 0.0, "offset_s": round(offset, 3)})
        rid += 1

    for sec in merged:
        if sec["mood"] != "normal":
            tr = pick_emotional(sec["mood"])  # never None (demoted above)
            emit(tr, sec["start_s"], sec["end_s"])
            continue
        t0 = sec["start_s"]
        while sec["end_s"] - t0 > 0.5:
            tr = normal_pool[bed_i % len(normal_pool)]
            tdur = float(tr.get("duration_s") or 0)
            if tdur > 1 and tdur - bed_off < 8.0:
                # Not enough left in this song for a meaningful stretch —
                # it has effectively ended; move to the next normal song.
                bed_i += 1
                bed_off = 0.0
                continue
            seg = (sec["end_s"] - t0 if tdur <= 1
                   else min(tdur - bed_off, sec["end_s"] - t0))
            emit(tr, t0, t0 + seg, offset=bed_off)
            bed_off += seg
            t0 += seg
            if tdur > 1 and bed_off >= tdur - 1.0:
                bed_i += 1
                bed_off = 0.0
    return regions


def loops_needed(track_dur: float, region_dur: float) -> int:
    if track_dur <= 0:
        return 0
    return max(0, math.ceil(region_dur / track_dur) - 1)
