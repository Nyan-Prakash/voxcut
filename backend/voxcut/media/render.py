"""Proxy + final render orchestration (spec §10).

Per-event segments are rendered to segments/<event_id>.mp4, concatenated, then
the voiceover master is muxed on top. Per-event caching enables dirty-segment
re-render (M6): only changed events re-render.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path

from .compose import CARD_BG, FPS, dims, timeline_ass, write_card_ass
from .probe import ffmpeg, ffprobe, run

# One render at a time per project: concurrent assembles (double-click, rebuild
# during generate) otherwise race on video_only/preview files.
_RENDER_LOCKS: dict[str, threading.Lock] = defaultdict(threading.Lock)


def _resolve_asset(asset_id: str) -> tuple[str | None, float]:
    from ..db import session_scope
    from ..models import Asset
    with session_scope() as db:
        a = db.get(Asset, asset_id)
        return (a.file_path, a.duration_s or 0.0) if a else (None, 0.0)


def _probe_ok(path: Path, min_dur: float = 0.05) -> bool:
    """A segment is usable iff it has a video stream with real duration."""
    try:
        info = ffprobe(path)
    except Exception:  # noqa: BLE001
        return False
    has_video = any(s.get("codec_type") == "video" for s in info.get("streams", []))
    dur = float(info.get("format", {}).get("duration") or 0)
    return has_video and dur >= min_dur


def _run_atomic(cmd: list[str], out: Path) -> None:
    """Run an ffmpeg command writing to a tmp path, rename into place on
    success. Readers (the preview player, the mux) can never observe a
    truncated file, even across processes."""
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    cmd = [*cmd[:-1], str(tmp)]  # replace final output arg
    try:
        run(cmd)
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


def _seg_text_style(ev: dict) -> tuple[str, str]:
    cap = ev.get("caption") or {}
    if cap.get("enabled") and cap.get("text"):
        return cap["text"], cap.get("style", "subtitle")
    # Placeholder for a sourcing event without an asset yet — keep it watchable.
    q = ev.get("queries") or []
    if ev["kind"] != "caption_card" and q:
        return f"[{ev['kind']}] {q[0]}", "card"
    return (cap.get("text") or ""), "card"


def _render_card(text: str, style: str, dur: float, out: Path, ass_path: Path,
                 w: int, h: int, crf: str, preset: str) -> Path:
    ass = write_card_ass(text, dur, w, h, style, ass_path)
    _run_atomic([
        ffmpeg(), "-y",
        "-f", "lavfi", "-i", f"color=c={CARD_BG}:s={w}x{h}:d={dur}:r={FPS}",
        "-vf", f"ass={ass.as_posix()}",
        "-c:v", "libx264", "-preset", preset, "-crf", crf,
        "-pix_fmt", "yuv420p", "-an", str(out),
    ], out)
    return out


def _is_gap(ev: dict) -> bool:
    return "gap_unfilled" in (ev.get("flags") or []) and not ev.get("asset_id")


def _absorb_gaps(events: list[dict]) -> list[tuple[dict, float]]:
    """Unsourced gap events don't get their own screen time — the neighboring
    clip plays through them (operator preference: no fallback text cards).
    Returns (event, render_duration) pairs; absorbed gaps are dropped.
    Backward pass first (previous clip holds), then forward (a leading gap is
    covered by the next clip). Gaps with no clip neighbor render as cards."""
    spans: list[list] = [[ev, ev["start_s"], ev["end_s"]] for ev in events]
    absorbed: list[list] = []
    for item in spans:
        if _is_gap(item[0]) and absorbed and absorbed[-1][0].get("asset_id"):
            absorbed[-1][2] = item[2]      # previous clip holds through the gap
            continue
        absorbed.append(item)
    result: list[list] = []
    for item in reversed(absorbed):
        if _is_gap(item[0]) and result and result[-1][0].get("asset_id"):
            result[-1][1] = item[1]        # next clip starts early to cover it
            continue
        result.append(item)
    result.reverse()
    return [(ev, max(0.1, round(e - s, 3))) for ev, s, e in result]


def render_event_segment(ev: dict, seg_dir: Path, w: int, h: int,
                         proxy: bool = True, dur: float | None = None) -> Path:
    out = seg_dir / f"{ev['id']}.mp4"
    if dur is None:
        dur = max(0.1, round(ev["end_s"] - ev["start_s"], 3))
    crf = "28" if proxy else "18"
    preset = "ultrafast" if proxy else "medium"

    asset_path, asset_dur = (_resolve_asset(ev["asset_id"])
                             if ev.get("asset_id") else (None, 0.0))
    cap = ev.get("caption") or {}
    ass_path = seg_dir / f"{ev['id']}.ass"

    if asset_path and Path(asset_path).exists() and ev.get("source"):
        # --- Real clip: seek, cover-crop to aspect, pad to exact beat duration,
        #     burn any caption. Source audio dropped (VO muxed at concat). ---
        in_s = float(ev["source"].get("in_s", 0.0))
        # Clamp: a seek at/past EOF decodes zero frames and kills the encoder.
        if asset_dur > 0:
            in_s = max(0.0, min(in_s, asset_dur - min(dur, asset_dur) - 0.05))
        ass_expr = ""
        if cap.get("enabled") and cap.get("text"):
            write_card_ass(cap["text"], dur, w, h, cap.get("style", "meme_bottom"),
                           ass_path)
            ass_expr = f",ass={ass_path.as_posix()}"
        # setsar=1 normalizes pixel aspect so concat stream-copy stays valid.
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},tpad=stop_mode=clone:stop_duration={dur}"
              f",fps={FPS},setsar=1{ass_expr}")
        try:
            _run_atomic([
                ffmpeg(), "-y", "-ss", f"{in_s:.3f}", "-i", asset_path,
                "-t", f"{dur}", "-vf", vf,
                "-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p", "-an", str(out),
            ], out)
            if _probe_ok(out):
                return out
            out.unlink(missing_ok=True)  # zero-frame output → card fallback
        except Exception:  # noqa: BLE001 — one bad clip must not sink the render
            pass
        # Fall back to a caption card so the timeline stays intact (NFR5).
        fallback = cap.get("text") or (ev.get("queries") or ["clip unavailable"])[0]
        return _render_card(f"[unavailable] {fallback}", "card", dur, out,
                            ass_path, w, h, crf, preset)

    # --- Caption card / placeholder (color background + centered text). ---
    text, style = _seg_text_style(ev)
    return _render_card(text, style, dur, out, ass_path, w, h, crf, preset)


def render_proxy(project_id: str, edl: dict, master_path: Path | None,
                 project_dir: Path, proxy: bool = True, on_progress=None) -> Path:
    with _RENDER_LOCKS[f"{project_id}:{proxy}"]:
        return _render_locked(project_id, edl, master_path, project_dir,
                              proxy, on_progress)


def _render_locked(project_id: str, edl: dict, master_path: Path | None,
                   project_dir: Path, proxy: bool, on_progress) -> Path:
    aspect = edl.get("aspect", "16:9")
    w, h = dims(aspect, proxy)
    seg_dir = project_dir / ("segments" if proxy else "segments_full")
    seg_dir.mkdir(exist_ok=True)
    crf = "28" if proxy else "18"
    preset = "ultrafast" if proxy else "medium"

    events = sorted(edl.get("events", []), key=lambda e: e["start_s"])
    render_plan = _absorb_gaps(events)
    seg_paths: list[Path] = []
    for i, (ev, dur) in enumerate(render_plan):
        seg = seg_dir / f"{ev['id']}.mp4"
        try:
            seg = render_event_segment(ev, seg_dir, w, h, proxy, dur=dur)
        except Exception:  # noqa: BLE001 — last-resort per-segment guard
            seg = _render_card(
                (ev.get("caption") or {}).get("text") or ev.get("kind", "clip"),
                "card", dur, seg, seg_dir / f"{ev['id']}.ass", w, h, crf, preset)
        # Validate before it can poison the concat; a broken segment becomes a card.
        if not _probe_ok(seg):
            seg.unlink(missing_ok=True)
            seg = _render_card("…", "card", dur, seg,
                               seg_dir / f"{ev['id']}.ass", w, h, crf, preset)
        seg_paths.append(seg)
        if on_progress:
            on_progress((i + 1) / max(1, len(render_plan)) * 0.85)

    if not seg_paths:
        raise RuntimeError("no events to render")

    # Concat: stream-copy first (fast); re-encode fallback if params disagree.
    concat_list = seg_dir / "concat.txt"
    concat_list.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_paths))
    video_only = project_dir / ("video_only_proxy.mp4" if proxy else "video_only.mp4")
    try:
        _run_atomic([ffmpeg(), "-y", "-f", "concat", "-safe", "0",
                     "-i", str(concat_list), "-c", "copy", str(video_only)],
                    video_only)
    except Exception:  # noqa: BLE001
        _run_atomic([ffmpeg(), "-y", "-f", "concat", "-safe", "0",
                     "-i", str(concat_list),
                     "-c:v", "libx264", "-preset", preset, "-crf", crf,
                     "-pix_fmt", "yuv420p", str(video_only)], video_only)
    if on_progress:
        on_progress(0.92)

    out_name = "preview_proxy.mp4" if proxy else "export.mp4"
    out = project_dir / out_name
    if master_path and Path(master_path).exists():
        try:
            _run_atomic([ffmpeg(), "-y", "-i", str(video_only),
                         "-i", str(master_path),
                         "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-shortest", "-movflags", "+faststart", str(out)], out)
        except Exception:  # noqa: BLE001 — mux fallback: re-encode video
            _run_atomic([ffmpeg(), "-y", "-i", str(video_only),
                         "-i", str(master_path),
                         "-map", "0:v:0", "-map", "1:a:0",
                         "-c:v", "libx264", "-preset", preset, "-crf", crf,
                         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                         "-shortest", "-movflags", "+faststart", str(out)], out)
    else:
        _run_atomic([ffmpeg(), "-y", "-i", str(video_only), "-c", "copy",
                     "-movflags", "+faststart", str(out)], out)
    if on_progress:
        on_progress(1.0)
    return out
