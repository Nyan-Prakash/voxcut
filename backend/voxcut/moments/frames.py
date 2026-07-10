"""Frame sampling for visual verification (spec §9.5, upgraded to a VLM).

Extracts small JPEGs at candidate-window centers so the frame judge can score
what the footage actually shows. Frames are tiny (320px, q6, low-detail) — a
5-frame judge call costs fractions of a cent.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from ..media.probe import ffmpeg


def extract_frame(video: Path, t: float, out_jpg: Path, width: int = 320) -> bool:
    """Grab one frame at time t. Returns False on failure (caller skips it)."""
    proc = subprocess.run(
        [ffmpeg(), "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "6",
         str(out_jpg)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    return proc.returncode == 0 and out_jpg.exists() and out_jpg.stat().st_size > 0


def frame_data_url(jpg: Path) -> str:
    b64 = base64.b64encode(jpg.read_bytes()).decode()
    return f"data:image/jpeg;base64,{b64}"


def sample_window_frames(video: Path, windows: list[tuple[float, float]],
                         work_dir: Path) -> list[str | None]:
    """One data-URL frame per (in_s, out_s) window, sampled at its center.
    Entries are None where extraction failed."""
    work_dir.mkdir(parents=True, exist_ok=True)
    urls: list[str | None] = []
    for i, (in_s, out_s) in enumerate(windows):
        jpg = work_dir / f"verify_{i}.jpg"
        mid = (in_s + out_s) / 2
        try:
            ok = extract_frame(video, mid, jpg)
        except Exception:  # noqa: BLE001
            ok = False
        urls.append(frame_data_url(jpg) if ok else None)
        jpg.unlink(missing_ok=True)
    return urls
