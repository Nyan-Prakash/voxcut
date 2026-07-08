"""Signal fusion + moment selection (spec §9.7).

Fuse per-time signals by intent, pick top-5 non-overlapping windows (NMS), snap
each to scene boundaries, and score confidence. Winner fills the EDL event; all
five become the editor's candidate strip (the "good-enough + easy nudge" contract).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import signals
from .scenes import detect_scenes, interior_cut_density, snap_to_scenes

# intent → (w_text, w_heat, w_audio, w_vis)
WEIGHTS = {
    "clip_literal":  (0.55, 0.20, 0.05, 0.20),
    "clip_reaction": (0.25, 0.40, 0.25, 0.10),
    "meme_image":    (0.30, 0.45, 0.15, 0.10),
    "featured":      (0.30, 0.45, 0.15, 0.10),
    "broll":         (0.45, 0.20, 0.10, 0.25),
}
CONF_THRESHOLD = 0.08


@dataclass
class Moment:
    in_s: float
    out_s: float
    score: float

    def to_dict(self) -> dict:
        return {"in_s": self.in_s, "out_s": self.out_s,
                "score": round(self.score, 4)}


def select_moments(*, video: Path, cache_dir: Path, duration: float,
                   beat_query: str, entities: list[str], intent: str,
                   beat_dur: float, subs_path: Path | None,
                   heatmap_path: Path | None,
                   top_k: int = 5) -> tuple[list[Moment], float]:
    win = max(0.8, min(beat_dur, duration))
    starts = signals.make_windows(duration, win, hop=1.0)

    subs = (json.loads(Path(subs_path).read_text())
            if subs_path and Path(subs_path).exists() else [])
    w_text, w_heat, w_audio, w_vis = WEIGHTS.get(intent, WEIGHTS["clip_literal"])

    s_text = signals.text_signal(starts, win, subs, beat_query, entities)
    s_heat = signals.heat_signal(starts, win, heatmap_path)
    s_audio = (signals.audio_signal(starts, win, video, cache_dir / "energy.npz")
               if w_audio > 0.05 else np.zeros(len(starts), dtype=np.float32))
    s_vis = np.zeros(len(starts), dtype=np.float32)  # CLIP escalation = v2 (§9.5)

    fused = (w_text * s_text + w_heat * s_heat
             + w_audio * s_audio + w_vis * s_vis)

    scenes = detect_scenes(video, cache_dir / "scenes.json")

    # Chaotic-montage veto (§9.6): penalize windows with >1 interior cut / 2s.
    for i, t0 in enumerate(starts):
        if interior_cut_density(t0, t0 + win, scenes) > 0.5:
            fused[i] *= 0.6

    order = np.argsort(fused)[::-1]
    chosen: list[Moment] = []
    used: list[float] = []
    for idx in order:
        t0 = starts[idx]
        if any(abs(t0 - u) < win * 0.75 for u in used):  # NMS min separation
            continue
        in_s, out_s = snap_to_scenes(t0, min(duration, t0 + win), scenes,
                                     tolerance=win * 0.2)
        chosen.append(Moment(round(in_s, 3), round(out_s, 3), float(fused[idx])))
        used.append(t0)
        if len(chosen) >= top_k:
            break

    if not chosen:
        chosen = [Moment(0.0, round(min(duration, win), 3), 0.0)]

    # Confidence = best - 2nd-best from a different region (§9.7).
    conf = chosen[0].score - (chosen[1].score if len(chosen) > 1 else 0.0)
    return chosen, round(conf, 4)


def intent_for(kind: str) -> str:
    return kind if kind in WEIGHTS else "clip_literal"
