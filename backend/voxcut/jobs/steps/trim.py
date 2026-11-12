"""Trim job: rebuild the working media + transcript from raw and a cut list.

An empty cut list is "leave as recorded". Either way the trim step is closed
afterwards and generate is unblocked. Refused once an edit exists — the edit
is timed against the trimmed recording.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ...edl_store import edl_path
from ...media.ingest import compute_peaks
from ...media.probe import duration_s
from ...media.trim_edit import cut_audio, cut_video
from ...trim import (cut_total, keep_ranges, normalize_cuts, remap_silences,
                     remap_words)
from ...trim_store import (RAW, WORK, _link_or_copy, pdir, raw_transcript,
                           read_state, save_words, set_project, write_state,
                           write_word_base)
from ..bus import bus
from ..runner import JobContext, register


def _replace(tmp: Path, dst: Path) -> None:
    # Never write through the working path: it may be a hard link to raw.
    dst.unlink(missing_ok=True)
    tmp.replace(dst)


def _apply(project_id: str, cuts: list[tuple[float, float]], raw_dur: float,
           report) -> None:  # noqa: ANN001
    d = pdir(project_id)
    if not cuts:
        for key in ("master", "asr", "facecam"):
            if (d / RAW[key]).exists():
                _link_or_copy(d / RAW[key], d / WORK[key])
        return
    keep = keep_ranges(cuts, raw_dur)
    report(0.1, "Cutting audio")
    tmp = d / "trim_tmp.m4a"
    cut_audio(d / RAW["master"], keep, tmp,
              ["-ar", "48000", "-c:a", "aac", "-b:a", "192k"])
    _replace(tmp, d / WORK["master"])
    tmp = d / "trim_tmp.wav"
    cut_audio(d / RAW["asr"], keep, tmp,
              ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"])
    _replace(tmp, d / WORK["asr"])
    if (d / RAW["facecam"]).exists():
        report(0.35, "Cutting facecam video")
        tmp = d / "trim_tmp.mp4"
        cut_video(d / RAW["facecam"], keep, tmp)
        _replace(tmp, d / WORK["facecam"])


@register("trim")
async def run_trim(ctx: JobContext) -> None:
    project_id = ctx.project_id
    d = pdir(project_id)
    raw = raw_transcript(project_id)
    if raw is None:
        raise RuntimeError("Nothing to trim — upload a recording first.")
    if edl_path(project_id).exists():
        raise RuntimeError("This project already has an edit; trimming now "
                           "would break its timing.")
    raw_dur = float(read_state(project_id).get("raw_duration_s") or 0.0) \
        or duration_s(d / RAW["asr"])
    cuts = normalize_cuts(ctx.payload.get("cuts") or [], raw_dur)

    step = ctx.add_step("trim")
    loop = asyncio.get_running_loop()

    def report(frac: float, msg: str) -> None:
        asyncio.run_coroutine_threadsafe(ctx.report(step, frac, msg), loop)

    await ctx.report(step, 0.02, f"Applying {len(cuts)} cuts" if cuts
                     else "Keeping the recording as is")
    await asyncio.to_thread(_apply, project_id, cuts, raw_dur, report)

    await ctx.report(step, 0.85, "Updating transcript")
    words = remap_words(raw["words"], cuts)
    save_words(project_id, words, raw.get("language", "en"),
               remap_silences(raw.get("silences"), cuts))
    new_dur = duration_s(d / WORK["asr"])
    (d / "waveform.json").write_text(json.dumps(
        compute_peaks(d / WORK["asr"], new_dur)))
    (d / "beats.json").unlink(missing_ok=True)   # beats are per-transcript
    set_project(project_id, voiceover_path=str(d / WORK["master"]),
                duration_s=new_dur, status="ready")
    write_state(project_id, "done" if cuts else "skipped", cuts, raw_dur)
    write_word_base(project_id)

    removed = cut_total(cuts)
    await ctx.finish_step(
        step, f"Trimmed {removed:.1f}s in {len(cuts)} cuts → {new_dur:.1f}s"
        if cuts else "Left as recorded")
    await bus.publish({"type": "trim_done", "project_id": project_id,
                       "duration_s": new_dur})
