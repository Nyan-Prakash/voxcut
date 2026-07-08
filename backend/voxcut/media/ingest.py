"""Voiceover ingest & normalization (spec §5.1).

On upload we produce a canonical master:
  - voiceover_asr.wav   16 kHz mono  → ASR
  - voiceover_master.m4a 48 kHz AAC  → timeline/export
and precompute waveform peaks JSON so the frontend paints instantly.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .probe import duration_s, ffmpeg, run


def normalize(src: Path, project_dir: Path) -> dict:
    asr_wav = project_dir / "voiceover_asr.wav"
    master = project_dir / "voiceover_master.m4a"

    run([ffmpeg(), "-y", "-i", str(src), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(asr_wav)])
    run([ffmpeg(), "-y", "-i", str(src), "-ar", "48000", "-c:a", "aac",
         "-b:a", "192k", str(master)])

    dur = duration_s(asr_wav)
    peaks_path = project_dir / "waveform.json"
    peaks = compute_peaks(asr_wav, dur)
    peaks_path.write_text(json.dumps(peaks))

    return {
        "asr_wav": str(asr_wav),
        "master": str(master),
        "duration_s": dur,
        "waveform": str(peaks_path),
    }


def compute_peaks(wav_path: Path, dur: float, buckets_per_s: int = 20) -> dict:
    """Downsample the 16 kHz PCM to min/max peaks per bucket for the waveform."""
    raw = np.memmap(wav_path, dtype=np.int16, mode="r")
    # WAV header is 44 bytes = 22 int16 samples; skip it.
    samples = np.asarray(raw[22:], dtype=np.float32) / 32768.0
    n_buckets = max(1, int(dur * buckets_per_s))
    if samples.size == 0:
        return {"version": 1, "buckets_per_s": buckets_per_s, "peaks": []}
    bucket_size = max(1, samples.size // n_buckets)
    usable = samples[: bucket_size * n_buckets].reshape(n_buckets, bucket_size)
    # Peak amplitude per bucket, normalized 0..1.
    peaks = np.abs(usable).max(axis=1)
    peak_max = float(peaks.max()) or 1.0
    peaks = (peaks / peak_max).round(4)
    return {"version": 1, "buckets_per_s": buckets_per_s, "peaks": peaks.tolist()}
