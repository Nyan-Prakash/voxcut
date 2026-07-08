"""Generate job (M3): the full DAG beats → plan → assemble in one job.

Transcription runs on upload; generate assumes a transcript exists. Each sub-step
adds its own progress rows to the same JobContext, so the UI shows the whole DAG.
"""
from __future__ import annotations

from sqlmodel import select

from ...db import session_scope
from ...models import Word
from ..runner import JobContext, register
from .assemble import run_assemble
from .beats import run_beats
from .plan import run_plan
from .source import run_source


@register("generate")
async def run_generate(ctx: JobContext) -> None:
    with session_scope() as db:
        has_words = db.exec(
            select(Word).where(Word.project_id == ctx.project_id).limit(1)
        ).first() is not None
    if not has_words:
        raise RuntimeError("No transcript — upload a voiceover first.")

    await run_beats(ctx)
    await run_plan(ctx)
    if not ctx.payload.get("skip_sourcing"):
        await run_source(ctx)
    await run_assemble(ctx)
