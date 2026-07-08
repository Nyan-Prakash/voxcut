"""Local (user-uploaded) media provider (spec §8.3)."""
from __future__ import annotations

import shutil
from pathlib import Path

from ..media.probe import ffprobe
from .base import Candidate, Filters


class LocalProvider:
    name = "local"

    def search(self, query: str, n: int, filters: Filters) -> list[Candidate]:
        # Local assets are matched via the library index, not a live search.
        return []

    def fetch(self, candidate: Candidate, dest: Path) -> dict:  # pragma: no cover
        raise NotImplementedError

    def fetch_url(self, url: str, dest: Path) -> dict:  # pragma: no cover
        raise NotImplementedError

    def ingest(self, src: Path, dest: Path, source_id: str) -> dict:
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / f"video{src.suffix.lower()}"
        shutil.copy2(src, target)
        meta = ffprobe(target)
        v = next((s for s in meta.get("streams", [])
                  if s.get("codec_type") == "video"), {})
        return {
            "provider": "local",
            "source_id": source_id,
            "source_url": str(src),
            "title": src.stem,
            "duration_s": float(meta.get("format", {}).get("duration") or 0),
            "width": int(v.get("width") or 0),
            "height": int(v.get("height") or 0),
            "fps": 0.0,
            "file_path": str(target),
            "subs_path": None,
            "heatmap_path": None,
            "size_bytes": target.stat().st_size,
        }
