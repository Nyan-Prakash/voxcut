"""Audio-energy signal for the Interject moment picker.

The muted-clip moment pipeline fuses visual signals; an interjection's payload
is a SOUND, so its windows come from the loudness envelope instead: decode the
asset's audio to mono PCM, bucket it into RMS, and surface the highest-energy
windows of the target length. The frame judge then verifies what those windows
look like.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..media.probe import ffmpeg

SAMPLE_RATE = 8000
BUCKET_S = 0.25
ANALYZE_MAX_S = 900.0  # same cap as the visual pipeline


def rms_envelope(video: Path, max_s: float = ANALYZE_MAX_S) -> np.ndarray:
    """Normalized (0..1) RMS per BUCKET_S bucket. Empty array on failure or
    when the file has no audio stream."""
    proc = subprocess.run(
        [ffmpeg(), "-v", "quiet", "-t", f"{max_s:.1f}", "-i", str(video),
         "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
         "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
        capture_output=True, check=False, timeout=300)
    if proc.returncode != 0 or not proc.stdout:
        return np.array([])
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    bucket = int(SAMPLE_RATE * BUCKET_S)
    n = samples.size // bucket
    if n == 0:
        return np.array([])
    rms = np.sqrt((samples[: n * bucket].reshape(n, bucket) ** 2).mean(axis=1))
    peak = float(rms.max()) or 1.0
    return rms / peak


def top_energy_windows(env: np.ndarray, win_s: float,
                       n: int = 6) -> list[tuple[float, float, float]]:
    """Up to n non-overlapping (start_s, end_s, energy 0..1) windows of
    win_s length, loudest-first, from an rms_envelope."""
    if env.size == 0:
        return []
    w = max(1, int(round(win_s / BUCKET_S)))
    if env.size < w:
        return [(0.0, env.size * BUCKET_S, float(env.mean()))]
    rolling = np.convolve(env, np.ones(w) / w, mode="valid")
    order = np.argsort(rolling)[::-1]
    picked: list[int] = []
    for i in order:
        if all(abs(int(i) - j) >= w for j in picked):
            picked.append(int(i))
        if len(picked) >= n:
            break
    return [(round(i * BUCKET_S, 3), round(i * BUCKET_S + win_s, 3),
             float(rolling[i])) for i in picked]
