"""Frame sampling for visual verification (spec §9.5, upgraded to a VLM).

Two samplers:

- ``sample_window_frames``: one small JPEG per window at its center (320px,
  low-detail). Used by QC, highlights and the interject judge, where one
  representative frame per clip is the point.
- ``sample_window_tiles``: one JPEG per window that tiles THREE frames from
  across the window (15% / 50% / 85%) at 640px. Used by the moment judge:
  a single center frame hid title cards, logos and dead stretches that a
  three-frame strip makes obvious, and 640px is wide enough for the model
  to actually read overlaid text and branding at "high" detail.
"""
from __future__ import annotations

import base64
import subprocess
from pathlib import Path

from ..media.probe import ffmpeg, ffprobe

TILE_WIDTH = 640
TILE_FRAMES = 3
# Fractions of the window to sample. Off the exact edges so a scene-snapped
# boundary frame (half a transition) doesn't stand in for the window.
TILE_OFFSETS = (0.15, 0.5, 0.85)


def extract_frame(video: Path, t: float, out_jpg: Path, width: int = 320) -> bool:
    """Grab one frame at time t. Returns False on failure (caller skips it)."""
    proc = subprocess.run(
        [ffmpeg(), "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "6",
         str(out_jpg)],
        capture_output=True, text=True, check=False, timeout=60,
    )
    return proc.returncode == 0 and out_jpg.exists() and out_jpg.stat().st_size > 0


def is_portrait(video: Path) -> bool:
    """True for vertical footage (shorts). Tiles stack sideways for those so
    the result stays roughly square instead of a 1:5 ribbon the vision
    model would downscale into nothing."""
    try:
        for s in ffprobe(video).get("streams", []):
            if s.get("codec_type") == "video":
                w, h = int(s.get("width") or 0), int(s.get("height") or 0)
                return h > w > 0
    except Exception:  # noqa: BLE001
        pass
    return False


def tile_times(in_s: float, out_s: float, n: int = TILE_FRAMES) -> list[float]:
    """Sample times across a window: 15/50/85% for n=3, evenly spread otherwise."""
    span = max(0.0, out_s - in_s)
    if n == len(TILE_OFFSETS):
        fracs = TILE_OFFSETS
    else:
        fracs = tuple((i + 0.5) / n for i in range(n))
    return [max(0.0, in_s + span * f) for f in fracs]


def extract_tile(video: Path, times: list[float], out_jpg: Path,
                 width: int = TILE_WIDTH, portrait: bool = False) -> bool:
    """One JPEG with len(times) frames stacked in time order — top→bottom for
    landscape footage, left→right for portrait. Each frame is `width` px
    wide. Returns False on failure."""
    cmd = [ffmpeg(), "-y"]
    for t in times:
        cmd += ["-ss", f"{max(0.0, t):.2f}", "-i", str(video)]
    n = len(times)
    scaled = "".join(f"[{i}:v]scale={width}:-2,setsar=1[f{i}];" for i in range(n))
    stack = "".join(f"[f{i}]" for i in range(n))
    stack += f"hstack=inputs={n}" if portrait else f"vstack=inputs={n}"
    if n == 1:
        stack = "[f0]copy"
    cmd += ["-filter_complex", scaled + stack, "-frames:v", "1", "-q:v", "4",
            str(out_jpg)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False,
                          timeout=90)
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


def sample_window_tiles(video: Path, windows: list[tuple[float, float]],
                        work_dir: Path, *, width: int = TILE_WIDTH,
                        per_window: int = TILE_FRAMES) -> list[str | None]:
    """One data-URL image per (in_s, out_s) window, each tiling `per_window`
    frames sampled across the window so the judge sees what the window DOES
    (start → middle → end), not one lucky frame. Falls back to a single
    center frame at the same width when the tile can't be built. Entries
    are None where nothing could be extracted."""
    work_dir.mkdir(parents=True, exist_ok=True)
    portrait = is_portrait(video)
    urls: list[str | None] = []
    for i, (in_s, out_s) in enumerate(windows):
        jpg = work_dir / f"tile_{i}.jpg"
        try:
            ok = extract_tile(video, tile_times(in_s, out_s, per_window), jpg,
                              width=width, portrait=portrait)
            if not ok:
                ok = extract_frame(video, (in_s + out_s) / 2, jpg, width=width)
        except Exception:  # noqa: BLE001
            ok = False
        urls.append(frame_data_url(jpg) if ok else None)
        jpg.unlink(missing_ok=True)
    return urls
