"""M4 real-download test: source one clip, cut it, render a proxy with footage."""
import json
from pathlib import Path

from voxcut.config import settings
from voxcut.db import init_db, session_scope
from voxcut.edl_store import save_edl
from voxcut.jobs.steps.source import _source_one
from voxcut.media.render import render_proxy
from voxcut.models import Project
from voxcut.sourcing.base import Filters
from voxcut.sourcing.youtube import YouTubeProvider

init_db()

with session_scope() as db:
    p = Project(name="M4 source test",
                settings=json.dumps({"aspect": "16:9"}))
    db.add(p); db.commit(); db.refresh(p)
    pid = p.id
pdir = settings().project_dir(pid)

# Two beats → two clip events with real queries.
events = [
    {"id": "ev_a", "beat_id": "b1", "start_s": 0.0, "end_s": 4.0,
     "kind": "clip_literal", "asset_id": None, "source": None,
     "queries": ["Kevin drops the chili The Office"],
     "treatment": {}, "caption": {"text": "HE DROPPED THE CHILI", "style": "meme_bottom",
                                   "enabled": True},
     "audio": {"mode": "mute"}, "flags": ["auto"], "locked": False},
    {"id": "ev_b", "beat_id": "b2", "start_s": 4.0, "end_s": 7.0,
     "kind": "caption_card", "asset_id": None, "source": None, "queries": [],
     "treatment": {}, "caption": {"text": "a tragedy for the whole office",
                                  "style": "subtitle", "enabled": True},
     "audio": {"mode": "mute"}, "flags": ["auto"], "locked": False},
]

provider = YouTubeProvider()
print("sourcing clip event (downloading)…")
asset_id, source, cands = _source_one(pid, events[0], provider, Filters())
print("  asset_id:", asset_id)
print("  source:", source)
print("  candidates:", len(cands))
assert asset_id, "sourcing failed"
events[0]["asset_id"] = asset_id
events[0]["source"] = source

edl = {"version": 0, "aspect": "16:9", "events": events}
save_edl(pid, edl)

print("rendering proxy with real footage…")
out = render_proxy(pid, edl, None, pdir, proxy=True)
print("  output:", out, out.stat().st_size, "bytes")

import subprocess
probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration:stream=codec_name,width,height",
                        "-of", "default=noprint_wrappers=1", str(out)],
                       capture_output=True, text=True)
print(probe.stdout)
# Extract a frame from the clip portion (t=1s) to confirm real footage.
subprocess.run(["ffmpeg", "-y", "-ss", "1.0", "-i", str(out), "-frames:v", "1",
                "/tmp/m4_frame.png"], capture_output=True)
print("frame → /tmp/m4_frame.png")
