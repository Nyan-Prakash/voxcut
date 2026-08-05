"""Voiceover master surgery for ripple edits (Interject).

The VO master is the timeline clock, so inserting or removing time means
re-rendering it: concat(vo[0:t], silence, vo[t:]) or concat(vo[0:a], vo[b:]).
Outputs are written to NEW files (never in place) — undo of a structural edit
is a pointer flip back to the previous master, not a re-render.
"""
from __future__ import annotations

from pathlib import Path

from .probe import ffmpeg, run

# Match the ingest master format (ingest.normalize): 48 kHz AAC 192k. Channel
# layout is pinned to stereo across all three concat legs so a mono upload
# can't poison the concat.
_AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def insert_silence(master: Path, at_s: float, dur_s: float, out: Path) -> None:
    """Write a new master = master[0:at_s] + silence(dur_s) + master[at_s:]."""
    fc = (
        f"[0:a]atrim=end={at_s:.3f},asetpts=PTS-STARTPTS,{_AFMT}[pre];"
        f"[1:a]{_AFMT}[gap];"
        f"[0:a]atrim=start={at_s:.3f},asetpts=PTS-STARTPTS,{_AFMT}[post];"
        f"[pre][gap][post]concat=n=3:v=0:a=1[out]"
    )
    run([ffmpeg(), "-y", "-i", str(master),
         "-f", "lavfi", "-t", f"{dur_s:.3f}", "-i", "anullsrc=r=48000:cl=stereo",
         "-filter_complex", fc, "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(out)])


def remove_span(master: Path, start_s: float, end_s: float, out: Path) -> None:
    """Write a new master = master[0:start_s] + master[end_s:]."""
    fc = (
        f"[0:a]atrim=end={start_s:.3f},asetpts=PTS-STARTPTS,{_AFMT}[pre];"
        f"[0:a]atrim=start={end_s:.3f},asetpts=PTS-STARTPTS,{_AFMT}[post];"
        f"[pre][post]concat=n=2:v=0:a=1[out]"
    )
    run([ffmpeg(), "-y", "-i", str(master),
         "-filter_complex", fc, "-map", "[out]",
         "-c:a", "aac", "-b:a", "192k", str(out)])
