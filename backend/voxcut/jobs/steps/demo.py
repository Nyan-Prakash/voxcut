"""M0 demo job — proves runner + checkpointing + SSE end-to-end.

Replaced by the real pipeline (transcribe → beats → plan → source → moment →
assemble) in later milestones.
"""
from __future__ import annotations

import asyncio

from ..runner import JobContext, register


@register("demo")
async def run_demo(ctx: JobContext) -> None:
    stages = [
        ("transcribe", "Transcribing voiceover"),
        ("beats", "Segmenting beats"),
        ("plan", "Planning the edit"),
        ("source", "Sourcing clips"),
        ("assemble", "Stitching preview"),
    ]
    for name, label in stages:
        step = ctx.add_step(name)
        for i in range(1, 6):
            await asyncio.sleep(0.15)
            await ctx.report(step, i / 5, f"{label}… {i * 20}%")
        await ctx.finish_step(step, f"{label} — done")
