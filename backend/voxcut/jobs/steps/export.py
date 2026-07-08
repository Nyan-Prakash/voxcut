"""Export job (M7): full-quality render (§10.2).

Re-extracts each event at full resolution from cached sources (never from the
proxy) and encodes a YouTube-clean 1080p/4K MP4.
"""
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


@register("export")
async def run_export(ctx: JobContext) -> None:
    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    edl = load_edl(project_id)
    with session_scope() as db:
        p = db.get(Project, project_id)
        master = p.voiceover_path if p else None

    step = ctx.add_step("export")
    await ctx.report(step, 0.02, "Rendering full-quality export")
    loop = asyncio.get_running_loop()

    def on_progress(frac: float) -> None:
        asyncio.run_coroutine_threadsafe(
            ctx.report(step, max(0.02, frac), f"Exporting… {int(frac*100)}%"), loop)

    out = await asyncio.to_thread(
        render_proxy, project_id, edl,
        Path(master) if master else None, pdir, False, on_progress)

    await ctx.finish_step(step, f"Export ready: {out.name}")
    await bus.publish({"type": "export_ready", "project_id": project_id,
                       "url": f"/api/projects/{project_id}/export/download"})
