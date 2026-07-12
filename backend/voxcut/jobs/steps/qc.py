"""QC pass: audit the finished edit against the mediocre-middle law.

One frame per placed clip (from the exact chosen moment) + its narration line
go to the vision judge: literal / joke / middle. Middles get a qc_middle flag
with the judge's reason stored on the event — flag-only, the operator decides
what to reroll. Runs at the end of generate and on demand.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ...config import settings
from ...db import session_scope
from ...edl_store import load_edl, save_edl
from ...models import Asset
from ..runner import JobContext, register

QC_FLAG = "qc_middle"


@register("qc")
async def run_qc(ctx: JobContext) -> None:
    from ...brain.client import BrainError, is_available
    from ...brain.judge import judge_qc
    from ...moments.frames import sample_window_frames

    project_id = ctx.project_id
    step = ctx.add_step("qc")
    if not is_available():
        await ctx.finish_step(step, "skipped — no OpenAI key")
        return

    edl = load_edl(project_id)
    beats_path = settings().project_dir(project_id) / "beats.json"
    beats = ({b["id"]: b for b in json.loads(beats_path.read_text())["beats"]}
             if beats_path.exists() else {})
    events = [e for e in edl["events"] if e.get("asset_id") and e.get("source")]
    if not events:
        await ctx.finish_step(step, "no placed clips to audit")
        return
    await ctx.report(step, 0.1, f"Auditing {len(events)} clips against the "
                                f"never-mediocre law")

    def frame_for(ev: dict) -> str | None:
        with session_scope() as db:
            asset = db.get(Asset, ev["asset_id"])
        if not asset or not Path(asset.file_path).exists():
            return None
        src = ev["source"]
        in_s = float(src.get("in_s", 0.0))
        out_s = float(src.get("out_s", in_s + 2.0))
        cache = (settings().library_dir / asset.source_id / "verify"
                 / f"qc_{ev['id']}")
        urls = sample_window_frames(Path(asset.file_path),
                                    [(in_s, max(out_s, in_s + 0.5))], cache)
        return urls[0] if urls else None

    entries, audited = [], []
    for ev in events:
        url = await asyncio.to_thread(frame_for, ev)
        if not url:
            continue
        beat = beats.get(ev.get("beat_id"), {})
        text = beat.get("text") or beat.get("gist") or (ev.get("queries") or [""])[0]
        entries.append((text, ev["kind"], url))
        audited.append(ev)
    if not entries:
        await ctx.finish_step(step, "no frames could be extracted")
        return

    await ctx.report(step, 0.6, "Judging frames")
    try:
        verdicts = await asyncio.to_thread(judge_qc, entries)
    except BrainError as exc:
        await ctx.finish_step(step, f"judge unavailable ({type(exc).__name__})")
        return

    flagged = 0
    for ev, v in zip(audited, verdicts):
        if v is None:
            continue
        ev["qc"] = v
        flags = [f for f in ev.get("flags", []) if f != QC_FLAG]
        if v["verdict"] == "middle":
            flags.append(QC_FLAG)
            flagged += 1
        ev["flags"] = flags
    save_edl(project_id, edl)
    await ctx.finish_step(
        step, f"{flagged}/{len(audited)} clips flagged as mediocre middle"
              + (" — review ⚑ and reroll them" if flagged else " 🎉"))
