"""Per-time signals for moment selection (spec §9.2–9.4).

Each signal produces a score over candidate window start times. Cheap signals
(transcript, heatmap, audio) run first; visual CLIP (§9.5) is a lazy escalation
handled in select.py.
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from ..media.probe import ffmpeg
from . import embed


def make_windows(duration: float, win: float, hop: float = 1.0) -> list[float]:
    """Candidate window start times (0, hop, 2*hop, …) that fit within duration."""
    if duration <= win:
        return [0.0]
    starts = []
    t = 0.0
    while t + win <= duration + 0.5:
        starts.append(round(t, 2))
        t += hop
    return starts or [0.0]


# ---------------------------------------------------------------- Signal 1: text
def text_signal(starts: list[float], win: float, subs: list[dict],
                beat_query: str, entities: list[str]) -> np.ndarray:
    if not subs:
        return np.zeros(len(starts), dtype=np.float32)

    def window_text(t0: float) -> str:
        t1 = t0 + win
        return " ".join(c["text"] for c in subs
                        if c["end"] > t0 and c["start"] < t1)

    texts = [window_text(t) for t in starts]

    vecs = embed.embed(texts + [beat_query])
    if vecs is not None:
        doc_vecs, qv = vecs[:-1], vecs[-1]
        scores = embed.cosine_matrix(qv, doc_vecs)
        scores = (scores + 1) / 2  # cosine [-1,1] → [0,1]
    else:
        scores = embed.fuzzy_sim(beat_query, texts)

    # Entity fuzzy-match bonus (§9.2).
    if entities:
        for i, txt in enumerate(texts):
            if any(fuzz.partial_ratio(e.lower(), txt.lower()) >= 85 for e in entities):
                scores[i] = min(1.0, scores[i] + 0.15)
    return _norm(np.asarray(scores, dtype=np.float32))


# ---------------------------------------------------------------- Signal 2: heat
def heat_signal(starts: list[float], win: float, heatmap_path: Path | None
                ) -> np.ndarray:
    if not heatmap_path or not Path(heatmap_path).exists():
        return np.zeros(len(starts), dtype=np.float32)
    hm = json.loads(Path(heatmap_path).read_text())
    if not hm:
        return np.zeros(len(starts), dtype=np.float32)

    def window_val(t0: float) -> float:
        t1 = t0 + win
        vals = [h["value"] for h in hm
                if h.get("end_time", 0) > t0 and h.get("start_time", 0) < t1]
        return max(vals) if vals else 0.0

    return _norm(np.array([window_val(t) for t in starts], dtype=np.float32))


# ---------------------------------------------------------------- Signal 3: audio
def audio_signal(starts: list[float], win: float, video: Path,
                 cache: Path) -> np.ndarray:
    """RMS + spectral-flux peak prominence per window (§9.4)."""
    env = _load_energy_envelope(video, cache)  # (times, energy) at 10 Hz
    if env is None:
        return np.zeros(len(starts), dtype=np.float32)
    times, energy = env

    def window_peak(t0: float) -> float:
        t1 = t0 + win
        mask = (times >= t0) & (times < t1)
        if not mask.any():
            return 0.0
        seg = energy[mask]
        return float(seg.max() - seg.mean())  # prominence

    return _norm(np.array([window_peak(t) for t in starts], dtype=np.float32))


def _load_energy_envelope(video: Path, cache: Path):
    if cache.exists():
        d = np.load(cache)
        return d["times"], d["energy"]
    # Extract 16k mono wav, compute RMS + flux at 100ms hops.
    wav = cache.with_suffix(".wav")
    proc = subprocess.run(
        [ffmpeg(), "-y", "-i", str(video), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not wav.exists():
        return None
    with wave.open(str(wav), "rb") as wf:
        sr = wf.getframerate()
        audio = np.frombuffer(wf.readframes(wf.getnframes()),
                              dtype=np.int16).astype(np.float32) / 32768.0
    wav.unlink(missing_ok=True)
    if audio.size == 0:
        return None
    hop = int(0.1 * sr)
    n = audio.size // hop
    frames = audio[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    flux = np.abs(np.diff(rms, prepend=rms[:1]))
    energy = _norm(rms) + _norm(flux)
    times = np.arange(n) * 0.1
    np.savez(cache, times=times, energy=energy)
    return times, energy


def _norm(a: np.ndarray) -> np.ndarray:
    if a.size == 0:
        return a
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-9:
        return np.zeros_like(a)
    return (a - lo) / (hi - lo)
