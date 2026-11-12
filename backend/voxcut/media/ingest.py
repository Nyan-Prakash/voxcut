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

from ..trim import FPS as FACECAM_FPS
from .probe import duration_s, ffmpeg, ffprobe, run

FACECAM_MAX_H = 1080


def has_video(src: Path) -> bool:
    """True when the upload carries a real video stream (a facecam) — cover
    art embedded in an audio file does not count."""
    try:
        streams = ffprobe(src).get("streams", [])
    except Exception:  # noqa: BLE001
        return False
    return any(s.get("codec_type") == "video"
               and not (s.get("disposition") or {}).get("attached_pic")
               for s in streams)


def extract_facecam(src: Path, out: Path) -> None:
    """The facecam master: constant 30 fps (so trim cuts land on whole
    frames), at most 1080p, a keyframe every second for fast seeking, no
    audio — the VO master is the audio."""
    run([ffmpeg(), "-y", "-i", str(src), "-an",
         "-vf", f"fps={FACECAM_FPS},scale=-2:'min({FACECAM_MAX_H},ih)',setsar=1",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-g", str(FACECAM_FPS), "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", str(out)])


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

    facecam = None
    if has_video(src):
        facecam = project_dir / "facecam.mp4"
        extract_facecam(src, facecam)

    return {
        "asr_wav": str(asr_wav),
        "master": str(master),
        "duration_s": dur,
        "waveform": str(peaks_path),
        "facecam": str(facecam) if facecam else None,
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
