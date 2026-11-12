"""Apply trim cuts to the raw audio and the raw facecam video.

Both are filtered with the SAME keep-ranges (trim.keep_ranges), already
snapped to the facecam frame grid, so the trimmed video and the trimmed audio
have the same length and stay in sync. Always reads the RAW file and writes a
new one — the trim can be redone.
"""
from __future__ import annotations

from pathlib import Path

from ..trim import FPS
from .probe import ffmpeg, run


def _write_script(expr: str, out: Path) -> Path:
    """Hundreds of pause cuts make a long filter — keep it off the command
    line."""
    script = out.with_name(out.name + ".filter")
    script.write_text(expr)
    return script


def cut_audio(src: Path, keep: list[tuple[float, float]], out: Path,
              codec_args: list[str]) -> None:
    # atrim is sample-accurate; one trimmed leg per kept range, concatenated.
    # (aselect drops whole decoder frames and left the length unchanged.)
    legs = "".join(
        f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS[k{i}];"
        for i, (a, b) in enumerate(keep))
    labels = "".join(f"[k{i}]" for i in range(len(keep)))
    script = _write_script(
        f"{legs}{labels}concat=n={len(keep)}:v=0:a=1[out]", out)
    try:
        run([ffmpeg(), "-y", "-i", str(src), "-/filter_complex", str(script),
             "-map", "[out]", *codec_args, str(out)])
    finally:
        script.unlink(missing_ok=True)


def cut_video(src: Path, keep: list[tuple[float, float]], out: Path) -> None:
    # Frame numbers, not times: the facecam is constant-FPS from ingest, so
    # this drops exactly the frames the audio loses.
    sel = "+".join(f"between(n,{round(a * FPS)},{round(b * FPS) - 1})"
                   for a, b in keep) or "0"
    script = _write_script(f"select='{sel}',setpts=N/{FPS}/TB", out)
    try:
        run([ffmpeg(), "-y", "-i", str(src), "-/filter:v", str(script),
             "-an", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "20", "-g", str(FPS), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)])
    finally:
        script.unlink(missing_ok=True)
