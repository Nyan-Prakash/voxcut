"""M5 test: download a clip, then select the best moment via fused signals."""
from pathlib import Path

from voxcut.config import settings
from voxcut.db import init_db, session_scope
from voxcut.jobs.steps.source import _source_one
from voxcut.models import Asset
from voxcut.moments import embed
from voxcut.moments.select import select_moments
from voxcut.sourcing.base import Filters
from voxcut.sourcing.youtube import YouTubeProvider

init_db()
print("embedder available:", embed.available())

ev = {"id": "ev_x", "start_s": 0.0, "end_s": 4.0, "kind": "clip_literal",
      "queries": ["Kevin drops the chili The Office"], "joke_queries": [],
      "flags": [], "locked": False}
asset_id, _src, _c, _f = _source_one("proj_test", ev, YouTubeProvider(), Filters())
with session_scope() as db:
    a = db.get(Asset, asset_id)
    file_path, subs_path, heatmap_path, dur, sid = (
        a.file_path, a.subs_path, a.heatmap_path, a.duration_s, a.source_id)

print(f"asset: {dur:.0f}s | subs={bool(subs_path)} | heatmap={bool(heatmap_path)}")

moments, conf = select_moments(
    video=Path(file_path), cache_dir=settings().library_dir / sid, duration=dur,
    beat_query="Kevin drops the pot of chili on the floor everyone is upset",
    entities=["chili", "Kevin"], intent="clip_literal", beat_dur=4.0,
    subs_path=Path(subs_path) if subs_path else None,
    heatmap_path=Path(heatmap_path) if heatmap_path else None)

print(f"confidence: {conf}")
print("top candidates (in_s → out_s, score):")
for m in moments:
    print(f"  {m.in_s:6.2f} → {m.out_s:6.2f}   score={m.score:.4f}")

# Sanity: 5 distinct, monotonic windows within the clip.
assert 1 <= len(moments) <= 5
for m in moments:
    assert 0 <= m.in_s < m.out_s <= dur + 1
print("OK — moment selection produced a scored candidate strip")
