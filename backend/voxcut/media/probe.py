"""ffprobe / ffmpeg helpers."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class MediaError(RuntimeError):
    pass


def _tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise MediaError(f"{name} not found on PATH")
    return path


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        [_tool("ffprobe"), "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        raise MediaError(f"ffprobe failed: {out.stderr[:300]}")
    return json.loads(out.stdout)


def duration_s(path: Path) -> float:
    info = ffprobe(path)
    if "format" in info and info["format"].get("duration"):
        return float(info["format"]["duration"])
    for s in info.get("streams", []):
        if s.get("duration"):
            return float(s["duration"])
    raise MediaError("could not determine duration")


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise MediaError(f"command failed ({cmd[0]}): {proc.stderr[-400:]}")


def ffmpeg() -> str:
    return _tool("ffmpeg")
