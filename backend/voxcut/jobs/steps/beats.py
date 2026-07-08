"""Beat segmentation job (M2). Reads words+silences, writes beats.json."""
from __future__ import annotations

import asyncio
import json

from sqlmodel import select

from ...brain import segment as seg
from ...brain.client import is_available
from ...config import settings
from ...db import session_scope
from ...models import Project, Word
from ..runner import JobContext, register


def _load_words(project_id: str) -> list[seg.W]:
    with session_scope() as db:
        rows = db.exec(
            select(Word).where(Word.project_id == project_id).order_by(Word.idx)
        ).all()
        return [seg.W(idx=w.idx, text=w.corrected_text or w.text,
                      start_s=w.start_s, end_s=w.end_s) for w in rows]


@register("beats")
async def run_beats(ctx: JobContext) -> None:
    project_id = ctx.project_id
    pdir = settings().project_dir(project_id)

    with session_scope() as db:
        p = db.get(Project, project_id)
        brief = json.loads(p.context_brief or "{}") if p else {}
        proj_settings = json.loads(p.settings or "{}") if p else {}
        duration = p.duration_s if p else 0.0
    density = proj_settings.get("cut_density", "normal")

    sil_path = pdir / "silences.json"
    silences = json.loads(sil_path.read_text())["silences"] if sil_path.exists() else []
    silences = [tuple(s) for s in silences]

    words = _load_words(project_id)
    step = ctx.add_step("beats")
    mode = "LLM" if is_available() else "heuristic"
    await ctx.report(step, 0.1, f"Segmenting beats ({mode})")

    context = _brief_summary(brief)
    beats = await asyncio.to_thread(
        seg.segment, words, silences, context, density, duration)

    await ctx.report(step, 0.9, f"{len(beats)} beats")
    doc = {"version": 1, "cut_density": density, "mode": mode, "beats": beats}
    (pdir / "beats.json").write_text(json.dumps(doc, indent=2))
    await ctx.finish_step(step, f"{len(beats)} beats ({mode})")


def _brief_summary(brief: dict) -> str:
    parts = []
    if brief.get("title"):
        parts.append(f"Title: {brief['title']}")
    if brief.get("subject"):
        parts.append(f"Subject: {brief['subject']}")
    if brief.get("tone") and brief["tone"] != "infer":
        parts.append(f"Tone: {brief['tone']}")
    refs = brief.get("named_references") or []
    if refs:
        parts.append("References: " + ", ".join(
            f"{r.get('name')} ({r.get('hint')})" if r.get("hint") else r.get("name", "")
            for r in refs))
    if brief.get("notes"):
        parts.append(f"Notes: {brief['notes']}")
    return " | ".join(parts)
