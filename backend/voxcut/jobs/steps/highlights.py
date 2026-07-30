"""TikTok highlight scouting + vertical clip export.

`highlights`: the vision judge reads the whole narration (beat-by-beat, with
one frame per beat from the finished preview) and proposes standalone
TikTok-worthy clips — hook-first, self-contained, 8-60s. Proposals are
snapped to beat boundaries and stored in highlights.json; the operator
decides what to export.

`export_clip`: cuts one proposal from the full-quality export (rendering it
first if needed) and center-crops to 9:16 1080x1920 for TikTok/Shorts.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl
from ...media.probe import ffmpeg, run
from ...models import Project
from ..bus import bus
from ..runner import JobContext, register

MIN_CLIP_S = 8.0
MAX_CLIP_S = 60.0
MAX_FRAME_BEATS = 80  # beyond this, extra beats are judged text-only


def highlights_path(project_id: str) -> Path:
    return settings().project_dir(project_id) / "highlights.json"


def clip_path(project_id: str, index: int) -> Path:
    return settings().project_dir(project_id) / "highlights" / f"clip_{index:02d}.mp4"


@register("highlights")
async def run_highlights(ctx: JobContext) -> None:
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_highlights
    from ...moments.frames import sample_window_frames

    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    step = ctx.add_step("highlights")
    if not is_available():
        await ctx.finish_step(step, "skipped — no OpenAI key")
        return

    beats_path = pdir / "beats.json"
    if not beats_path.exists():
        await ctx.finish_step(step, "no beats yet — generate the edit first")
        return
    beats = sorted(json.loads(beats_path.read_text())["beats"],
                   key=lambda b: b["start_s"])
    if not beats:
        await ctx.finish_step(step, "no beats to scout")
        return

    # One frame per beat from the finished preview — the judge sees what
    # actually plays, not what was planned. Text-only when no preview yet.
    frames: list[str | None] = [None] * len(beats)
    preview = pdir / "preview_proxy.mp4"
    if preview.exists():
        await ctx.report(step, 0.1, f"Sampling {min(len(beats), MAX_FRAME_BEATS)}"
                                    " frames from the finished edit")
        windows = [(b["start_s"], b["end_s"]) for b in beats[:MAX_FRAME_BEATS]]
        sampled = await asyncio.to_thread(
            sample_window_frames, preview, windows, pdir / "highlight_frames")
        frames[:len(sampled)] = sampled
    else:
        await ctx.report(step, 0.1, "No preview yet — scouting from narration only")

    await ctx.report(step, 0.5, "Scouting for TikTok-worthy clips")
    try:
        proposals = await asyncio.to_thread(judge_highlights, beats, frames)
    except BrainError as exc:
        await ctx.finish_step(step, f"scout unavailable ({type(exc).__name__})")
        return

    clips = []
    for p in proposals:
        sb, eb = int(p.get("start_beat", -1)), int(p.get("end_beat", -1))
        if not (0 <= sb <= eb < len(beats)):
            continue
        start_s, end_s = beats[sb]["start_s"], beats[eb]["end_s"]
        dur = end_s - start_s
        if not (MIN_CLIP_S * 0.5 <= dur <= MAX_CLIP_S * 1.5):
            continue
        clips.append({
            "start_beat": sb, "end_beat": eb,
            "start_s": round(start_s, 3), "end_s": round(end_s, 3),
            "duration_s": round(dur, 3),
            "title": p.get("title", ""), "hook": p.get("hook", ""),
            "reason": p.get("reason", ""),
            "score": max(0.0, min(1.0, float(p.get("score", 0.0)))),
        })
    clips.sort(key=lambda c: c["score"], reverse=True)
    for i, c in enumerate(clips):
        c["index"] = i

    highlights_path(project_id).write_text(json.dumps(
        {"version": 1, "clips": clips}, indent=2))
    await ctx.finish_step(
        step, f"{len(clips)} TikTok-worthy clip{'s'[:len(clips) != 1]} found"
              + (" — review and export ⬇" if clips else ""))
    await bus.publish({"type": "highlights_ready", "project_id": project_id,
                       "count": len(clips)})


@register("export_clip")
async def run_export_clip(ctx: JobContext) -> None:
    from ...media.render import render_proxy

    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    index = int(ctx.payload.get("index", -1))

    step = ctx.add_step("export_clip")
    hpath = highlights_path(project_id)
    if not hpath.exists():
        await ctx.finish_step(step, "no highlights analyzed yet")
        return
    clips = json.loads(hpath.read_text()).get("clips", [])
    clip = next((c for c in clips if c.get("index") == index), None)
    if not clip:
        await ctx.finish_step(step, f"clip {index} not found")
        return

    # Cut from the full-quality export so the clip carries the final audio
    # mix (VO + kept clip audio + music). Render it first if it's stale/missing.
    export = pdir / "export.mp4"
    if not export.exists():
        await ctx.report(step, 0.05, "Rendering full-quality master first")
        edl = load_edl(project_id)
        with session_scope() as db:
            p = db.get(Project, project_id)
            master = p.voiceover_path if p else None
        loop = asyncio.get_running_loop()

        def on_progress(frac: float) -> None:
            asyncio.run_coroutine_threadsafe(
                ctx.report(step, 0.05 + frac * 0.7,
                           f"Rendering master… {int(frac * 100)}%"), loop)

        await asyncio.to_thread(
            render_proxy, project_id, edl,
            Path(master) if master else None, pdir, False, on_progress)

    out = clip_path(project_id, index)
    out.parent.mkdir(exist_ok=True)
    dur = max(0.5, clip["end_s"] - clip["start_s"])
    await ctx.report(step, 0.8, f"Cutting {dur:.0f}s vertical clip")
    # Center-crop to 9:16 then scale to 1080x1920 (crop width forced even).
    vf = ("crop='min(iw,trunc(ih*9/16/2)*2)':ih,"
          "scale=1080:1920,setsar=1")
    await asyncio.to_thread(run, [
        ffmpeg(), "-y", "-ss", f"{clip['start_s']:.3f}", "-t", f"{dur:.3f}",
        "-i", str(export), "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out)])

    await ctx.finish_step(step, f"TikTok clip ready: {out.name}")
    await bus.publish({
        "type": "clip_ready", "project_id": project_id, "index": index,
        "url": f"/api/projects/{project_id}/highlights/{index}/download"})
