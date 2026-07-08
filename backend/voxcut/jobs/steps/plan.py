"""Plan job (M3): beats.json → edl.json via the edit brain."""
from __future__ import annotations

import asyncio
import json

from ...brain import plan as planner
from ...brain.client import is_available
from ...config import settings
from ...db import session_scope
from ...edl_store import save_edl
from ...models import Project
from ..runner import JobContext, register


@register("plan")
async def run_plan(ctx: JobContext) -> None:
    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)
    beats_path = pdir / "beats.json"
    if not beats_path.exists():
        raise RuntimeError("beats.json missing — run beats first")
    beats_doc = json.loads(beats_path.read_text())
    beats = beats_doc["beats"]

    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
        proj_settings = json.loads(p.settings or "{}") if p else {}
    aspect = proj_settings.get("aspect", "16:9")

    step = ctx.add_step("plan")
    mode = "LLM" if is_available() else "heuristic"
    await ctx.report(step, 0.2, f"Planning the edit ({mode})")
    edl = await asyncio.to_thread(planner.plan, beats, brief, aspect)
    save_edl(project_id, edl)
    await ctx.finish_step(step, f"{len(edl['events'])} events ({mode})")
