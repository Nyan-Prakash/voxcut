"""Proxy + final render orchestration (spec §10).

Per-event segments are rendered to segments/<event_id>.mp4, concatenated, then
the voiceover master is muxed on top. Per-event caching enables dirty-segment
re-render (M6): only changed events re-render.
"""
from __future__ import annotations

from pathlib import Path

from .compose import CARD_BG, FPS, dims, timeline_ass, write_card_ass
from .probe import ffmpeg, run


def _resolve_asset_path(asset_id: str) -> str | None:
    from ..db import session_scope
    from ..models import Asset
    with session_scope() as db:
        a = db.get(Asset, asset_id)
        return a.file_path if a else None


def _seg_text_style(ev: dict) -> tuple[str, str]:
    cap = ev.get("caption") or {}
    if cap.get("enabled") and cap.get("text"):
        return cap["text"], cap.get("style", "subtitle")
    # Placeholder for a sourcing event without an asset yet — keep it watchable.
    q = ev.get("queries") or []
    if ev["kind"] != "caption_card" and q:
        return f"[{ev['kind']}] {q[0]}", "card"
    return (cap.get("text") or ""), "card"


def render_event_segment(ev: dict, seg_dir: Path, w: int, h: int,
                         proxy: bool = True) -> Path:
    out = seg_dir / f"{ev['id']}.mp4"
    dur = max(0.1, round(ev["end_s"] - ev["start_s"], 3))
    crf = "28" if proxy else "18"
    preset = "ultrafast" if proxy else "medium"

    asset_path = _resolve_asset_path(ev["asset_id"]) if ev.get("asset_id") else None
    cap = ev.get("caption") or {}
    ass_path = seg_dir / f"{ev['id']}.ass"

    if asset_path and Path(asset_path).exists() and ev.get("source"):
        # --- Real clip: seek, cover-crop to aspect, pad to exact beat duration,
        #     burn any caption. Source audio dropped (VO muxed at concat). ---
        in_s = float(ev["source"].get("in_s", 0.0))
        # Caption over the clip (optional) via a per-segment ASS.
        ass_expr = ""
        if cap.get("enabled") and cap.get("text"):
            write_card_ass(cap["text"], dur, w, h, cap.get("style", "meme_bottom"),
                           ass_path)
            ass_expr = f",ass={ass_path.as_posix()}"
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},tpad=stop_mode=clone:stop_duration={dur}"
              f",fps={FPS}{ass_expr}")
        run([
            ffmpeg(), "-y", "-ss", f"{in_s}", "-i", asset_path, "-t", f"{dur}",
            "-vf", vf, "-c:v", "libx264", "-preset", preset, "-crf", crf,
            "-pix_fmt", "yuv420p", "-an", str(out),
        ])
        return out

    # --- Caption card / placeholder (color background + centered text). ---
    text, style = _seg_text_style(ev)
    ass = write_card_ass(text, dur, w, h, style, ass_path)
    run([
        ffmpeg(), "-y",
        "-f", "lavfi", "-i", f"color=c={CARD_BG}:s={w}x{h}:d={dur}:r={FPS}",
        "-vf", f"ass={ass.as_posix()}",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-an", str(out),
    ])
    return out


def render_proxy(project_id: str, edl: dict, master_path: Path | None,
                 project_dir: Path, proxy: bool = True, on_progress=None) -> Path:
    aspect = edl.get("aspect", "16:9")
    w, h = dims(aspect, proxy)
    seg_dir = project_dir / ("segments" if proxy else "segments_full")
    seg_dir.mkdir(exist_ok=True)

    events = sorted(edl.get("events", []), key=lambda e: e["start_s"])
    seg_paths: list[Path] = []
    for i, ev in enumerate(events):
        seg_paths.append(render_event_segment(ev, seg_dir, w, h, proxy))
        if on_progress:
            on_progress((i + 1) / max(1, len(events)) * 0.85)

    # Concat (segments share codec/params → stream copy is safe & fast).
    concat_list = seg_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_paths))
    video_only = project_dir / ("video_only_proxy.mp4" if proxy else "video_only.mp4")
    run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(video_only)])
    if on_progress:
        on_progress(0.92)

    out_name = "preview_proxy.mp4" if proxy else "export.mp4"
    out = project_dir / out_name
    if master_path and Path(master_path).exists():
        run([ffmpeg(), "-y", "-i", str(video_only), "-i", str(master_path),
             "-map", "0:v:0", "-map", "1:a:0",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", "-movflags", "+faststart", str(out)])
    else:
        run([ffmpeg(), "-y", "-i", str(video_only), "-c", "copy",
             "-movflags", "+faststart", str(out)])
    if on_progress:
        on_progress(1.0)
    return out
