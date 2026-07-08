"""Assemble job (M3): edl.json → stitched proxy preview (§10.2)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl
from ...media.render import render_proxy
from ...models import Project
from ..bus import bus
from ..runner import JobContext, register


@register("assemble")
async def run_assemble(ctx: JobContext) -> None:
    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    edl = load_edl(project_id)

    with session_scope() as db:
        p = db.get(Project, project_id)
        master = p.voiceover_path if p else None

    step = ctx.add_step("assemble")
    await ctx.report(step, 0.05, "Rendering preview")
    loop = asyncio.get_running_loop()

    def on_progress(frac: float) -> None:
        asyncio.run_coroutine_threadsafe(
            ctx.report(step, max(0.05, frac), f"Rendering preview… {int(frac*100)}%"),
            loop)

    out = await asyncio.to_thread(
        render_proxy, project_id, edl,
        Path(master) if master else None, pdir, True, on_progress)

    with session_scope() as db:
        p = db.get(Project, project_id)
        if p:
            p.status = "ready"
            db.add(p)
            db.commit()

    await ctx.finish_step(step, f"Preview ready: {out.name}")
    await bus.publish({"type": "preview_updated", "project_id": project_id,
                       "url": f"/api/projects/{project_id}/preview"})
