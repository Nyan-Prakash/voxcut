"""Proxy + final render orchestration (spec §10).

Per-event segments are rendered to segments/<event_id>.mp4, concatenated, then
the voiceover master is muxed on top. Per-event caching enables dirty-segment
re-render (M6): only changed events re-render.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from pathlib import Path

from .compose import CARD_BG, FPS, dims
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


def _render_blank(dur: float, out: Path, w: int, h: int,
                  crf: str, preset: str) -> Path:
    """Plain dark background — the only non-footage segment we ever render.
    No text on screen, ever (captions removed by design)."""
    _run_atomic([
        ffmpeg(), "-y",
        "-f", "lavfi", "-i", f"color=c={CARD_BG}:s={w}x{h}:d={dur}:r={FPS}",
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
    covered by the next clip). Gaps with no clip neighbor render blank."""
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

    if asset_path and Path(asset_path).exists() and ev.get("source"):
        # --- Real clip: seek, cover-crop to aspect, pad to exact beat duration.
        #     Source audio dropped (VO muxed at concat). ---
        in_s = float(ev["source"].get("in_s", 0.0))
        # Clamp: a seek at/past EOF decodes zero frames and kills the encoder.
        if asset_dur > 0:
            in_s = max(0.0, min(in_s, asset_dur - min(dur, asset_dur) - 0.05))
        # setsar=1 normalizes pixel aspect so concat stream-copy stays valid.
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},tpad=stop_mode=clone:stop_duration={dur}"
              f",fps={FPS},setsar=1")
        try:
            _run_atomic([
                ffmpeg(), "-y", "-ss", f"{in_s:.3f}", "-i", asset_path,
                "-t", f"{dur}", "-vf", vf,
                "-c:v", "libx264", "-preset", preset, "-crf", crf,
                "-pix_fmt", "yuv420p", "-an", str(out),
            ], out)
            if _probe_ok(out):
                return out
            out.unlink(missing_ok=True)  # zero-frame output → blank fallback
        except Exception:  # noqa: BLE001 — one bad clip must not sink the render
            pass

    # --- Gap / unavailable clip: plain background, absorbed by neighbors
    #     whenever one exists (see _absorb_gaps). ---
    return _render_blank(dur, out, w, h, crf, preset)


def render_proxy(project_id: str, edl: dict, master_path: Path | None,
                 project_dir: Path, proxy: bool = True, on_progress=None) -> Path:
    with _RENDER_LOCKS[f"{project_id}:{proxy}"]:
        return _render_locked(project_id, edl, master_path, project_dir,
                              proxy, on_progress)


def _has_audio(path: str) -> bool:
    try:
        info = ffprobe(Path(path))
    except Exception:  # noqa: BLE001
        return False
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
# Export loudness target (YouTube normalizes to ~-14 LUFS; matching it keeps
# every upload consistent). loudnorm resamples internally, so pin 48k after.
LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


def _audio_overlays(events: list[dict]) -> list[tuple[dict, str]]:
    """Events whose source audio plays in the final mix: (event, asset_path)
    for audio.mode keep (full volume) or duck (attenuated under the VO)."""
    out = []
    for ev in events:
        mode = (ev.get("audio") or {}).get("mode", "mute")
        if mode not in ("keep", "duck") or not ev.get("asset_id") or not ev.get("source"):
            continue
        path, _dur = _resolve_asset(ev["asset_id"])
        if path and Path(path).exists() and _has_audio(path):
            out.append((ev, path))
    return out


def _music_regions(project_id: str, project_dir: Path) -> tuple[list[dict], dict, list]:
    """Enabled music regions from project settings + the VO silence map."""
    import json as _json

    from ..db import session_scope
    from ..models import Project
    with session_scope() as db:
        p = db.get(Project, project_id)
        cfg = (_json.loads(p.settings or "{}") if p else {}).get("music") or {}
    if not cfg.get("enabled", True):
        return [], cfg, []
    from ..music import track_path
    regions = []
    for r in cfg.get("regions", []):
        path = track_path(r.get("file", ""))
        if path and r.get("end_s", 0) - r.get("start_s", 0) >= 1.0:
            regions.append({**r, "path": str(path)})
    sil_path = project_dir / "silences.json"
    silences = ([tuple(s) for s in _json.loads(sil_path.read_text())["silences"]]
                if sil_path.exists() else [])
    return regions, cfg, silences


def _mux_final(video_only: Path, master_path: Path, overlays: list[tuple[dict, str]],
               music: tuple[list[dict], dict, list], out: Path,
               loudnorm: bool = False) -> None:
    """Final audio mix: VO + keep/duck event audio + ducked music regions.
    Overlays: keep → 0 dB; duck → the event's duck_db (default -18).
    Music: base volume under speech, swells in VO silences (duck envelope)."""
    from ..music import duck_envelope_expr, loops_needed
    regions, cfg, silences = music
    cmd = [ffmpeg(), "-y", "-i", str(video_only), "-i", str(master_path)]
    parts, labels = [], []
    n_in = 2

    for k, (ev, path) in enumerate(overlays):
        cmd += ["-i", path]
        src = ev["source"]
        in_s = float(src.get("in_s", 0.0))
        dur = max(0.05, ev["end_s"] - ev["start_s"])
        gain = 0.0 if ev["audio"].get("mode") == "keep" else float(
            ev["audio"].get("duck_db", -18))
        delay = int(round(ev["start_s"] * 1000))
        parts.append(
            f"[{n_in}:a]atrim=start={in_s:.3f}:end={in_s + dur:.3f},"
            f"asetpts=PTS-STARTPTS,{AFMT},volume={gain:.1f}dB,"
            f"adelay={delay}|{delay}[ax{k}]")
        labels.append(f"[ax{k}]")
        n_in += 1

    base_db = float(cfg.get("volume_db", -25.0)) if regions else 0.0
    swell_db = float(cfg.get("duck_db", 8.0)) if regions else 0.0
    for k, r in enumerate(regions):
        dur = r["end_s"] - r["start_s"]
        try:
            track_dur = float(ffprobe(Path(r["path"])).get("format", {})
                              .get("duration") or 0)
        except Exception:  # noqa: BLE001
            track_dur = 0.0
        cmd += ["-stream_loop", str(loops_needed(track_dur, dur)), "-i", r["path"]]
        env = duck_envelope_expr(silences, r["start_s"], dur,
                                 base_db + float(r.get("gain_db", 0.0)), swell_db)
        fade_out = max(0.0, dur - 0.8)
        delay = int(round(r["start_s"] * 1000))
        parts.append(
            f"[{n_in}:a]atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,{AFMT},"
            f"afade=t=in:d=0.8,afade=t=out:st={fade_out:.3f}:d=0.8,"
            f"volume=volume='{env}':eval=frame,"
            f"adelay={delay}|{delay}[mx{k}]")
        labels.append(f"[mx{k}]")
        n_in += 1

    parts.append(f"[1:a]{AFMT}[voa]")
    # normalize=0: overlays ADD to the VO instead of dividing everyone's level.
    tail = f",{LOUDNORM},aresample=48000" if loudnorm else ""
    parts.append(f"[voa]{''.join(labels)}amix=inputs={len(labels) + 1}:"
                 f"duration=first:normalize=0{tail}[aout]")
    cmd += ["-filter_complex", ";".join(parts),
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", "-movflags", "+faststart", str(out)]
    _run_atomic(cmd, out)


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
            seg = _render_blank(dur, seg, w, h, crf, preset)
        # Validate before it can poison the concat; a broken segment goes blank.
        if not _probe_ok(seg):
            seg.unlink(missing_ok=True)
            seg = _render_blank(dur, seg, w, h, crf, preset)
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
        # Export only: normalize to the YouTube loudness target. Preview keeps
        # the raw mix so rebuilds stay fast.
        norm_af = (["-af", f"{LOUDNORM},aresample=48000"] if not proxy else [])
        overlays = _audio_overlays(events)
        try:
            music = _music_regions(project_id, project_dir)
        except Exception:  # noqa: BLE001 — bad music config must not kill renders
            music = ([], {}, [])
        done = False
        if overlays or music[0]:
            try:
                _mux_final(video_only, master_path, overlays, music, out,
                           loudnorm=not proxy)
                done = True
            except Exception:  # noqa: BLE001 — overlay mix fails → plain VO mux
                pass
        if not done:
            try:
                _run_atomic([ffmpeg(), "-y", "-i", str(video_only),
                             "-i", str(master_path),
                             "-map", "0:v:0", "-map", "1:a:0", *norm_af,
                             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                             "-shortest", "-movflags", "+faststart", str(out)], out)
            except Exception:  # noqa: BLE001 — mux fallback: re-encode video
                _run_atomic([ffmpeg(), "-y", "-i", str(video_only),
                             "-i", str(master_path),
                             "-map", "0:v:0", "-map", "1:a:0", *norm_af,
                             "-c:v", "libx264", "-preset", preset, "-crf", crf,
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                             "-shortest", "-movflags", "+faststart", str(out)], out)
    else:
        _run_atomic([ffmpeg(), "-y", "-i", str(video_only), "-c", "copy",
                     "-movflags", "+faststart", str(out)], out)
    if on_progress:
        on_progress(1.0)
    return out
