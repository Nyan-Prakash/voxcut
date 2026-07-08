"""Transcribe job (M1): ingest → normalize → ASR → persist words + artifacts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlmodel import delete, select

from ...asr.transcribe import transcribe
from ...config import settings
from ...db import session_scope
from ...media.ingest import normalize
from ...models import Project, Word
from ..runner import JobContext, register


@register("transcribe")
async def run_transcribe(ctx: JobContext) -> None:
    project_id = ctx.project_id
    src = Path(ctx.payload["src"])
    tier = ctx.payload.get("tier", "balanced")
    pdir = settings().project_dir(project_id)

    # --- Step 1: ingest/normalize (ffmpeg + waveform peaks) ---
    ingest_step = ctx.add_step("ingest")
    await ctx.report(ingest_step, 0.1, "Normalizing audio")
    norm = await asyncio.to_thread(normalize, src, pdir)
    await ctx.finish_step(ingest_step, f"{norm['duration_s']:.1f}s of audio")

    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.voiceover_path = norm["master"]
            p.duration_s = norm["duration_s"]
            p.status = "generating"
            db.add(p)
            db.commit()

    # --- Step 2: ASR ---
    asr_step = ctx.add_step("transcribe")
    await ctx.report(asr_step, 0.0, f"Transcribing ({tier})")
    loop = asyncio.get_running_loop()

    def on_progress(frac: float) -> None:
        # Called from the worker thread; schedule the async report on the loop.
        asyncio.run_coroutine_threadsafe(
            ctx.report(asr_step, frac, f"Transcribing… {int(frac * 100)}%"), loop)

    transcript = await asyncio.to_thread(
        transcribe, Path(norm["asr_wav"]), tier, on_progress)
    await ctx.finish_step(asr_step, f"{len(transcript.words)} words")

    # --- Step 3: persist words + artifacts ---
    save_step = ctx.add_step("persist")
    await ctx.report(save_step, 0.3, "Saving transcript")
    with session_scope() as db:
        db.exec(delete(Word).where(Word.project_id == project_id))
        for w in transcript.words:
            db.add(Word(project_id=project_id, idx=w.idx, text=w.text,
                        start_s=w.start_s, end_s=w.end_s, confidence=w.confidence))
        db.commit()

    (pdir / "transcript.json").write_text(json.dumps({
        "language": transcript.language,
        "words": [w.__dict__ for w in transcript.words],
    }))
    (pdir / "silences.json").write_text(json.dumps({"silences": transcript.silences}))

    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.status = "ready"
            db.add(p)
            db.commit()
    await ctx.finish_step(save_step, "Transcript ready")
