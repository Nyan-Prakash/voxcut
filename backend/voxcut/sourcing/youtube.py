"""YouTube SourceProvider via yt-dlp (spec §8.2). No API key needed."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from ..media.probe import ffprobe
from .base import Candidate, Filters

_FMT = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b"


def _ytdlp() -> str:
    exe = shutil.which("yt-dlp")
    if not exe:
        raise RuntimeError("yt-dlp not found on PATH")
    return exe


class YouTubeProvider:
    name = "youtube"

    def search(self, query: str, n: int, filters: Filters) -> list[Candidate]:
        proc = subprocess.run(
            [_ytdlp(), f"ytsearch{n}:{query}", "--flat-playlist", "-J",
             "--no-warnings"],
            capture_output=True, text=True, check=False, timeout=90,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        data = json.loads(proc.stdout)
        out: list[Candidate] = []
        for e in data.get("entries", []) or []:
            if not e or not e.get("id"):
                continue
            dur = float(e.get("duration") or 0)
            live = (e.get("live_status") in ("is_live", "is_upcoming"))
            c = Candidate(
                provider="youtube", source_id=e["id"],
                url=e.get("url") or f"https://www.youtube.com/watch?v={e['id']}",
                title=e.get("title") or "",
                duration_s=dur,
                view_count=int(e.get("view_count") or 0),
                channel=e.get("channel") or e.get("uploader") or "",
                channel_verified=bool(e.get("channel_is_verified")),
                live=live,
                thumbnail=(e.get("thumbnails") or [{}])[-1].get("url", "")
                if e.get("thumbnails") else "",
            )
            if c.duration_s and c.duration_s < filters.min_duration_s:
                continue
            out.append(c)
        return out

    def fetch(self, candidate: Candidate, dest: Path) -> dict:
        return self._download(candidate.url, dest)

    def fetch_url(self, url: str, dest: Path) -> dict:
        return self._download(url, dest)

    def _download(self, url: str, dest: Path) -> dict:
        dest.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [_ytdlp(), "-f", _FMT, "--merge-output-format", "mp4",
             "--write-auto-subs", "--write-subs", "--sub-langs", "en.*",
             "--write-info-json", "--write-thumbnail", "--no-playlist",
             "--continue", "--no-warnings",
             "-o", str(dest / "video.%(ext)s"), url],
            capture_output=True, text=True, check=False, timeout=600,
        )
        video = next((p for p in dest.glob("video.*")
                      if p.suffix in (".mp4", ".mkv", ".webm")), None)
        if video is None:
            raise RuntimeError(f"download failed: {proc.stderr[-300:]}")

        info_path = next(dest.glob("*.info.json"), None)
        info = json.loads(info_path.read_text()) if info_path else {}

        # Heatmap ("most replayed") — key moment-selection signal (§9.3).
        heatmap_path = None
        if info.get("heatmap"):
            heatmap_path = dest / "heatmap.json"
            heatmap_path.write_text(json.dumps(info["heatmap"]))

        # Subtitles → parsed JSON.
        subs_path = None
        vtt = next((p for p in dest.glob("video*.vtt")), None)
        if vtt:
            cues = _parse_vtt(vtt.read_text(errors="ignore"))
            if cues:
                subs_path = dest / "subs.json"
                subs_path.write_text(json.dumps(cues))

        thumb = next((p for p in dest.glob("video.*")
                      if p.suffix in (".jpg", ".png", ".webp")), None)

        meta = ffprobe(video)
        vstream = next((s for s in meta.get("streams", [])
                        if s.get("codec_type") == "video"), {})
        return {
            "provider": "youtube",
            "source_id": info.get("id") or video.parent.name,
            "source_url": info.get("webpage_url") or url,
            "title": info.get("title") or "",
            "duration_s": float(info.get("duration") or
                                meta.get("format", {}).get("duration") or 0),
            "width": int(vstream.get("width") or 0),
            "height": int(vstream.get("height") or 0),
            "fps": _fps(vstream.get("avg_frame_rate", "0/1")),
            "file_path": str(video),
            "subs_path": str(subs_path) if subs_path else None,
            "heatmap_path": str(heatmap_path) if heatmap_path else None,
            "thumbnail_path": str(thumb) if thumb else None,
            "size_bytes": video.stat().st_size,
        }


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return round(float(num) / float(den), 3) if float(den) else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3})")


def _t(ts: str) -> float:
    m = _TS.match(ts)
    if not m:
        return 0.0
    h, mi, s, ms = map(int, m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000.0


def _parse_vtt(text: str) -> list[dict]:
    """Minimal WebVTT → [{start,end,text}] (dedups YouTube's rolling captions)."""
    cues: list[dict] = []
    seen_last = ""
    for block in text.split("\n\n"):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        tl = next((l for l in lines if "-->" in l), None)
        if not tl:
            continue
        try:
            a, b = tl.split("-->")
            start, end = _t(a.strip()[:12]), _t(b.strip()[:12])
        except Exception:  # noqa: BLE001
            continue
        content = " ".join(l for l in lines if "-->" not in l and not l.isdigit())
        content = re.sub(r"<[^>]+>", "", content).strip()
        if not content or content == seen_last:
            continue
        seen_last = content
        cues.append({"start": round(start, 2), "end": round(end, 2), "text": content})
    return cues
