"""Scene-boundary detection via ffmpeg (spec §9.6) — no PySceneDetect/opencv dep.

Uses the `scdet` filter to log scene-change timestamps, cached per asset.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..media.probe import ffmpeg

_SCENE_RE = re.compile(r"lavfi\.scd\.time=([0-9.]+)")


def detect_scenes(video: Path, cache: Path, threshold: float = 10.0) -> list[float]:
    if cache.exists():
        return json.loads(cache.read_text())
    proc = subprocess.run(
        [ffmpeg(), "-i", str(video), "-vf",
         f"scdet=threshold={threshold}", "-f", "null", "-"],
        capture_output=True, text=True, check=False,
    )
    times = sorted({round(float(m), 3)
                    for m in _SCENE_RE.findall(proc.stderr)})
    cache.write_text(json.dumps(times))
    return times


def snap_to_scenes(in_s: float, out_s: float, scenes: list[float],
                   tolerance: float) -> tuple[float, float]:
    """Expand/contract the window to the nearest scene boundaries within tolerance
    so the segment reads as an intentional clip (§9.6)."""
    if not scenes:
        return in_s, out_s
    new_in = min((s for s in scenes if abs(s - in_s) <= tolerance),
                 key=lambda s: abs(s - in_s), default=in_s)
    new_out = min((s for s in scenes if abs(s - out_s) <= tolerance),
                  key=lambda s: abs(s - out_s), default=out_s)
    if new_out - new_in < 0.5:  # don't collapse
        return in_s, out_s
    return round(new_in, 3), round(new_out, 3)


def interior_cut_density(in_s: float, out_s: float, scenes: list[float]) -> float:
    """Scene cuts per second inside the window (chaotic-montage veto, §9.6)."""
    dur = max(0.1, out_s - in_s)
    interior = sum(1 for s in scenes if in_s < s < out_s)
    return interior / dur
