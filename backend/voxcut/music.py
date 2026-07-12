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

MOODS = ["chill", "whimsical", "hype", "tense", "dramatic", "sad"]
# Beat tone (brain/segment) → track mood to hunt for.
TONE_TO_MOOD = {
    "deadpan": "chill",
    "neutral": "chill",
    "sarcastic": "whimsical",
    "absurd": "whimsical",
    "hype": "hype",
    "serious": "tense",
}
MIN_SECTION_S = 20.0   # don't switch tracks faster than this
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}

# duck_db 0 = solid constant level under the whole VO (operator preference);
# raise it to make music swell up in VO pauses.
DEFAULT_MUSIC = {"enabled": True, "volume_db": -25.0, "duck_db": 0.0,
                 "regions": []}


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


# ------------------------------------------------------------- duck envelope

def duck_envelope_expr(silences: list[tuple[float, float]], region_start: float,
                       region_dur: float, base_db: float, swell_db: float,
                       ramp: float = 0.15, min_gap: float = 0.5,
                       max_terms: int = 80) -> str:
    """ffmpeg volume expression (region-local t): music sits at base_db under
    speech and swells by swell_db inside VO silences, with linear ramps."""
    base = 10 ** (base_db / 20)
    if abs(swell_db) < 0.01:  # solid level — no envelope at all
        return f"{base:.6f}"
    swell = 10 ** ((base_db + swell_db) / 20) - base

    # Silences overlapping this region, region-local, merged if nearly touching.
    local: list[list[float]] = []
    for s, e in sorted(silences):
        s, e = s - region_start, e - region_start
        s, e = max(s, 0.0), min(e, region_dur)
        if e - s < min_gap:
            continue
        if local and s - local[-1][1] < 2 * ramp:
            local[-1][1] = e
        else:
            local.append([s, e])
    local = sorted(local, key=lambda x: x[1] - x[0], reverse=True)[:max_terms]
    local.sort()
    if not local:
        return f"{base:.6f}"

    terms = [
        f"min(max((t-{s:.3f})/{ramp},0),1)*min(max(({e:.3f}-t)/{ramp},0),1)"
        for s, e in local
    ]
    return f"{base:.6f}+{swell:.6f}*({'+'.join(terms)})"


# ---------------------------------------------------------------- suggestion

def suggest_regions(beats: list[dict], tracks: list[dict],
                    duration: float) -> list[dict]:
    """Deterministic theme-matching: group beats into tone sections (>=20s),
    map each section's dominant tone to a mood, pick the operator's best
    matching track (round-robin on ties so one track doesn't hog the video)."""
    tagged = [t for t in tracks if t.get("mood")]
    if not tagged or duration <= 0:
        return []

    # Sections: runs of same target mood, merged up to the minimum length.
    sections: list[dict] = []
    for b in beats:
        mood = TONE_TO_MOOD.get(b.get("tone", "neutral"), "chill")
        if sections and (sections[-1]["mood"] == mood
                         or b["end_s"] - sections[-1]["start_s"] < MIN_SECTION_S):
            sections[-1]["end_s"] = b["end_s"]
            sections[-1]["tones"].append(mood)
        else:
            sections.append({"start_s": b["start_s"], "end_s": b["end_s"],
                             "mood": mood, "tones": [mood]})
    for sec in sections:  # dominant mood across everything merged in
        sec["mood"] = max(set(sec["tones"]), key=sec["tones"].count)
    # A trailing short section folds into its neighbor.
    if len(sections) > 1 and (sections[-1]["end_s"] - sections[-1]["start_s"]) < MIN_SECTION_S:
        sections[-2]["end_s"] = sections[-1]["end_s"]
        sections.pop()

    by_mood: dict[str, list[dict]] = {}
    for t in tagged:
        by_mood.setdefault(t["mood"], []).append(t)
    FALLBACK = {"chill": ["whimsical", "sad"], "whimsical": ["chill", "hype"],
                "hype": ["whimsical", "dramatic"], "tense": ["dramatic", "chill"],
                "dramatic": ["tense", "hype"], "sad": ["chill", "tense"]}
    rr: dict[str, int] = {}

    def pick(mood: str) -> dict:
        for m in [mood, *FALLBACK.get(mood, []), *MOODS]:
            pool = by_mood.get(m)
            if pool:
                i = rr.get(m, 0)
                rr[m] = i + 1
                return pool[i % len(pool)]
        return tagged[0]

    regions = []
    prev_name = None
    for i, sec in enumerate(sections):
        t = pick(sec["mood"])
        end = duration if i == len(sections) - 1 else sec["end_s"]
        # Same track chosen twice in a row → one continuous region instead.
        if prev_name == t["name"] and regions:
            regions[-1]["end_s"] = round(end, 3)
            continue
        regions.append({"id": f"mr_{i}", "file": t["name"],
                        "start_s": round(max(0.0, sec["start_s"]), 3),
                        "end_s": round(end, 3), "gain_db": 0.0})
        prev_name = t["name"]
    return regions


def loops_needed(track_dur: float, region_dur: float) -> int:
    if track_dur <= 0:
        return 0
    return max(0, math.ceil(region_dur / track_dur) - 1)
