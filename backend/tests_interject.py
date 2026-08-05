"""Ripple-core invariant test for the Interject tool (no LLM, no network).

Runs against an isolated VOXCUT_DATA_DIR. Builds a synthetic project (tone VO,
words, beats, silences, waveform, tiled EDL), then exercises insert_time,
resize_gap, remove_time, and undo, asserting after every step that the world
stays consistent: duration_s ≡ VO file length ≡ last event end, events tile
the timeline with no holes or overlaps, and undo restores everything.

Run:  cd backend && VOXCUT_DATA_DIR=/tmp/voxcut_ij_test .venv/bin/python tests_interject.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

if not os.environ.get("VOXCUT_DATA_DIR"):
    os.environ["VOXCUT_DATA_DIR"] = tempfile.mkdtemp(prefix="voxcut_ij_")

from voxcut.config import settings  # noqa: E402
from voxcut.db import init_db, session_scope  # noqa: E402
from voxcut.edl_store import load_edl, save_edl  # noqa: E402
from voxcut.models import Project, Word  # noqa: E402
from voxcut.timeline_ops import (insert_time, make_interject_event,  # noqa: E402
                                 remove_time, resize_gap)

init_db()
DUR = 12.0


def vo_len(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    return float(out.stdout.strip())


def check_invariants(pid: str, tag: str) -> None:
    edl = load_edl(pid)
    with session_scope() as db:
        p = db.get(Project, pid)
        dur, vo = p.duration_s, p.voiceover_path
    events = sorted(edl["events"], key=lambda e: e["start_s"])
    assert events, f"[{tag}] no events"
    assert abs(events[0]["start_s"]) < 0.01, f"[{tag}] first event starts late"
    for a, b in zip(events, events[1:]):
        gap = b["start_s"] - a["end_s"]
        assert abs(gap) < 0.02, (f"[{tag}] hole/overlap {gap:.3f}s between "
                                 f"{a['id']} and {b['id']}")
    assert abs(events[-1]["end_s"] - dur) < 0.02, \
        f"[{tag}] last event ends {events[-1]['end_s']} vs duration {dur}"
    real_vo = vo_len(vo)
    assert abs(real_vo - dur) < 0.08, \
        f"[{tag}] VO file {real_vo:.3f}s vs duration_s {dur:.3f}s"
    beats = json.loads((settings().project_dir(pid) / "beats.json").read_text())
    for bt in beats["beats"]:
        assert bt["end_s"] > bt["start_s"], f"[{tag}] inverted beat {bt['id']}"
    wf = json.loads((settings().project_dir(pid) / "waveform.json").read_text())
    wf_len = len(wf["peaks"]) / wf["buckets_per_s"]
    assert abs(wf_len - dur) < 0.5, f"[{tag}] waveform {wf_len:.1f}s vs {dur:.1f}s"
    print(f"  ✓ [{tag}] dur={dur:.3f} vo={real_vo:.3f} events={len(events)}")


# ---------------------------------------------------------------- fixture
with session_scope() as db:
    p = Project(name="interject ripple test")
    db.add(p); db.commit(); db.refresh(p)
    pid = p.id
pdir = settings().project_dir(pid)

master = pdir / "voiceover_master.m4a"
subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                f"sine=frequency=440:duration={DUR}", "-ar", "48000",
                "-c:a", "aac", "-b:a", "192k", str(master)],
               capture_output=True, check=True)

with session_scope() as db:
    p = db.get(Project, pid)
    p.voiceover_path = str(master)
    p.duration_s = DUR
    db.add(p)
    for i in range(24):  # one word every 0.5s
        db.add(Word(project_id=pid, idx=i, text=f"w{i}",
                    start_s=i * 0.5, end_s=i * 0.5 + 0.4))
    db.commit()

beats = [{"id": f"bt_{k}", "start_s": k * 3.0, "end_s": (k + 1) * 3.0,
          "word_start_idx": k * 6, "word_end_idx": k * 6 + 5,
          "text": f"beat {k}", "gist": f"beat {k}", "tone": "neutral",
          "emphasis": 0.5, "concrete_entities": [], "visual_affinity": "literal",
          "rhythm": "flow", "locked": False} for k in range(4)]
(pdir / "beats.json").write_text(json.dumps({"version": 1, "beats": beats}))
(pdir / "silences.json").write_text(json.dumps({"silences": [[5.9, 6.1]]}))
(pdir / "waveform.json").write_text(json.dumps(
    {"version": 1, "buckets_per_s": 20, "peaks": [0.5] * int(DUR * 20)}))

events = [{"id": f"ev_{k}", "beat_id": f"bt_{k}", "start_s": k * 3.0,
           "end_s": (k + 1) * 3.0, "kind": "broll", "asset_id": None,
           "source": None, "queries": [f"q{k}"], "joke_queries": [],
           "treatment": {}, "audio": {"mode": "mute", "duck_db": -18},
           "flags": ["auto"], "locked": False} for k in range(4)]
save_edl(pid, {"version": 0, "aspect": "16:9", "events": events})

with session_scope() as db:
    p = db.get(Project, pid)
    p.settings = json.dumps({"music": {"enabled": True, "volume_db": -25,
                                       "regions": [{"id": "mr1", "file": "x.mp3",
                                                    "start_s": 2.0, "end_s": 10.0,
                                                    "gain_db": 0}]}})
    db.add(p); db.commit()

check_invariants(pid, "baseline")

# ------------------------------------------------- 1. insert mid-event (4.4s)
ph = make_interject_event(4.4, 6.4)
res = insert_time(pid, 4.4, 2.0, new_event=ph)
gap1 = res["new_event_id"]
check_invariants(pid, "insert@4.4")
edl = load_edl(pid)
g = next(e for e in edl["events"] if e["id"] == gap1)
assert abs(g["start_s"] - 4.4) < 0.01 and abs(g["end_s"] - 6.4) < 0.01
host_halves = [e for e in edl["events"] if e["beat_id"] in ("bt_1",) or
               (e.get("flags") and "user_cut" in e["flags"])]
assert any("user_cut" in (e.get("flags") or []) for e in edl["events"]), "no split tail"
with session_scope() as db:
    w9 = db.exec(__import__("sqlmodel").select(Word).where(
        Word.project_id == pid, Word.idx == 9)).one()
    assert abs(w9.start_s - 6.5) < 0.01, f"word 9 not shifted: {w9.start_s}"
    p = db.get(Project, pid)
    music = json.loads(p.settings)["music"]["regions"][0]
    assert abs(music["end_s"] - 12.0) < 0.01, f"music region not extended: {music}"

# ------------------------------------------------- 2. resize the gap 2.0→3.2
resize_gap(pid, gap1, 3.2)
check_invariants(pid, "resize-grow")
edl = load_edl(pid)
g = next(e for e in edl["events"] if e["id"] == gap1)
assert abs((g["end_s"] - g["start_s"]) - 3.2) < 0.01

# ------------------------------------------------- 3. resize down 3.2→1.5
resize_gap(pid, gap1, 1.5)
check_invariants(pid, "resize-shrink")
edl = load_edl(pid)
g = next(e for e in edl["events"] if e["id"] == gap1)
assert abs((g["end_s"] - g["start_s"]) - 1.5) < 0.01

# ------------------------------------------------- 4. insert at event boundary
ph2 = make_interject_event(3.0, 5.0)
res2 = insert_time(pid, 3.0, 2.0, new_event=ph2)
check_invariants(pid, "insert@boundary")

# ------------------------------------------------- 5. delete gap2 (ripple remove)
edl = load_edl(pid)
g2 = next(e for e in edl["events"] if e["id"] == res2["new_event_id"])
remove_time(pid, g2["start_s"], g2["end_s"])
check_invariants(pid, "remove-gap2")
edl = load_edl(pid)
assert all(e["id"] != res2["new_event_id"] for e in edl["events"]), "gap2 survived"

# ------------------------------------------------- 6. undo everything
from voxcut.api.edl import undo  # noqa: E402
for i in range(6):
    try:
        undo(pid)
    except Exception:
        break
    check_invariants(pid, f"undo-{i}")

with session_scope() as db:
    p = db.get(Project, pid)
    assert abs(p.duration_s - DUR) < 0.01, f"duration not restored: {p.duration_s}"
    assert p.voiceover_path == str(master), f"VO pointer not restored: {p.voiceover_path}"
    w9 = db.exec(__import__("sqlmodel").select(Word).where(
        Word.project_id == pid, Word.idx == 9)).one()
    assert abs(w9.start_s - 4.5) < 0.01, f"word 9 not restored: {w9.start_s}"
edl = load_edl(pid)
assert len(edl["events"]) == 4, f"events not restored: {len(edl['events'])}"
beats = json.loads((pdir / "beats.json").read_text())["beats"]
assert len(beats) == 4, f"beats not restored: {len(beats)}"

print("\nALL RIPPLE INVARIANTS PASS 🎉")
print("data dir:", os.environ["VOXCUT_DATA_DIR"])
sys.exit(0)
