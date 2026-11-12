"""Offline tests for the trim step and facecam planning/rendering.
Run: cd backend && uv run python tests_facecam.py   (needs ffmpeg; no network, no LLM)
"""
import json
import subprocess
import tempfile
from pathlib import Path

from voxcut import facecam, trim
from voxcut.media import ingest, render, trim_edit
from voxcut.media.probe import duration_s, ffprobe

# ---- cuts -------------------------------------------------------------------
c = trim.normalize_cuts([[5.01, 6.0], [1.0, 2.0], [1.5, 2.5], [9.0, 9.01], [-1, 0.5], [19.5, 99]], 20.0)
assert c == [(0.0, 0.5), (1.0, 2.5), (5.0, 6.0), (19.5, 20.0)], c   # snapped, merged, sliver dropped
assert all(abs(a * 30 - round(a * 30)) < 1e-6 and abs(b * 30 - round(b * 30)) < 1e-6 for a, b in c)
assert trim.keep_ranges(c, 20.0) == [(0.5, 1.0), (2.5, 5.0), (6.0, 19.5)]
assert trim.cut_total(c) == 3.5
assert trim.remap(0.75, c) == 0.25 and trim.remap(2.0, c) == 0.5     # inside a cut → the splice
assert trim.remap(10.0, c) == 7.0
assert trim.normalize_cuts([], 20.0) == [] and trim.normalize_cuts([["x", 1]], 20.0) == []

words = [{"idx": i, "text": t, "start_s": s, "end_s": s + 0.3}
         for i, (t, s) in enumerate([("a", 0.6), ("b", 1.2), ("c", 2.0), ("d", 3.0), ("e", 7.0)])]
rw = trim.remap_words(words, c)
assert [w["text"] for w in rw] == ["a", "d", "e"] and [w["idx"] for w in rw] == [0, 1, 2]
assert [w["start_s"] for w in rw] == [0.1, 1.0, 4.0]
assert trim.remap_silences([[1.0, 2.5], [3.4, 3.9]], c) == [[1.4, 1.9]]

# ---- suggestions ------------------------------------------------------------
assert trim.pause_cuts([[1.0, 1.4], [5.0, 6.0]], min_len=0.6, keep=0.25) == [[5.125, 5.875]]


def W(text, gap=0.0, t0=0.0):
    out, t = [], t0
    for tok in text.split():
        if tok == "|":
            t += gap
            continue
        out.append({"idx": len(out), "text": tok, "start_s": round(t, 2), "end_s": round(t + 0.25, 2)})
        t += 0.3
    return out


r = trim.retake_cuts(W("so the thing is so the thing is that it works"))
assert len(r) == 1 and r[0]["reason"] == "repeat" and r[0]["text"] == "so the thing is", r
assert r[0]["start_s"] == 0.0 and r[0]["end_s"] == 1.2
r = trim.retake_cuts(W("I think that we | I think that we should go home now", gap=0.6))
assert len(r) == 1 and r[0]["text"] == "I think that we", r
assert trim.retake_cuts(W("this is a normal sentence with no repeats at all")) == []
assert trim.retake_cuts(W("no no no")) == []                 # under 3 words: not flagged

# ---- real media: audio and video lose exactly the same time -------------------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / "cam.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=640x360:r=24:d=12", "-f", "lavfi", "-i",
                    "sine=f=440:d=12", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    snd = td / "voice.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "sine=f=440:d=3", str(snd)], check=True)
    assert ingest.has_video(src) and not ingest.has_video(snd)
    norm = ingest.normalize(src, td)
    cam = Path(norm["facecam"])
    v = next(s for s in ffprobe(cam)["streams"] if s["codec_type"] == "video")
    assert v["r_frame_rate"] == "30/1" and not any(
        s["codec_type"] == "audio" for s in ffprobe(cam)["streams"])

    cuts = trim.normalize_cuts([[1.0, 2.0], [4.1, 4.9], [7.0, 9.5]] + [[10 + i * .2, 10.1 + i * .2] for i in range(5)], 12.0)
    keep = trim.keep_ranges(cuts, 12.0)
    want = 12.0 - trim.cut_total(cuts)
    a_out, v_out = td / "a.m4a", td / "v.mp4"
    trim_edit.cut_audio(Path(norm["master"]), keep, a_out, ["-ar", "48000", "-c:a", "aac"])
    trim_edit.cut_video(cam, keep, v_out)
    da, dv = duration_s(a_out), duration_s(v_out)
    assert abs(dv - want) < 0.04, (dv, want)
    assert abs(da - want) < 0.06, (da, want)       # AAC frame padding only
    assert abs(da - dv) < 0.06, (da, dv)
    print(f"trim media OK: {want:.2f}s wanted, audio {da:.3f}s, video {dv:.3f}s")

    # facecam segment renders from the right time at the output size
    seg = td / "seg"; seg.mkdir()
    out = render.render_event_segment(
        {"id": "ev1", "start_s": 0, "end_s": 2.0, "facecam": True, "flags": ["facecam"]},
        seg, 640, 360, True, facecam=(v_out, 1.0))
    sv = next(s for s in ffprobe(out)["streams"] if s["codec_type"] == "video")
    assert (sv["width"], sv["height"]) == (640, 360) and abs(duration_s(out) - 2.0) < 0.08

# ---- face vs clip -----------------------------------------------------------
def E(dur, need, i, asset=None):
    return {"id": f"e{i}", "start_s": i * 10.0, "end_s": i * 10.0 + dur, "visual_need": need,
            "flags": ["auto"] + ([] if asset else ["gap_unfilled"]), "asset_id": asset}


evs = [E(4, "low", 0), E(4, "high", 1), E(4, "low", 2), E(4, "medium", 3), E(4, "high", 4), E(4, "low", 5)]
facecam.assign_facecam(evs, 0.5)
assert [facecam.is_facecam(e) for e in evs] == [True, False, True, False, False, True]
assert facecam.face_share_of(evs) == 0.5
assert all("gap_unfilled" not in e["flags"] for e in evs if facecam.is_facecam(e))
assert all("facecam" in e["flags"] for e in evs if facecam.is_facecam(e))
facecam.assign_facecam(evs, 0.0); assert not any(facecam.is_facecam(e) for e in evs)
facecam.assign_facecam(evs, 1.0); assert all(facecam.is_facecam(e) for e in evs)
# re-running is idempotent and never leaves stale flags behind
facecam.assign_facecam(evs, 0.5)
assert sum(e["flags"].count("facecam") for e in evs) == 3
# a one-second flash of face between two clips is smoothed away
evs = [E(5, "high", 0), E(1.0, "low", 1), E(5, "high", 2), E(6, "low", 3)]
facecam.assign_facecam(evs, 0.45)
assert [facecam.is_facecam(e) for e in evs] == [False, False, False, True]
# toggling back to a clip with nothing sourced marks the gap; with footage it doesn't
e = E(3, "low", 0); facecam.set_facecam(e, True); facecam.set_facecam(e, False)
assert "gap_unfilled" in e["flags"] and "facecam" not in e["flags"] and "facecam" not in e
e = E(3, "low", 0, asset="as_1"); facecam.set_facecam(e, True); facecam.set_facecam(e, False)
assert "gap_unfilled" not in e["flags"]

# gaps next to a face shot are absorbed by it, and it starts early to cover a leading gap
gap = {"id": "g", "start_s": 0.0, "end_s": 2.0, "flags": ["gap_unfilled"], "asset_id": None}
face = {"id": "f", "start_s": 2.0, "end_s": 5.0, "flags": ["facecam"], "facecam": True, "asset_id": None}
spans = render._absorb_gap_spans([gap, face])
assert [(ev["id"], s, d) for ev, s, d in spans] == [("f", 0.0, 5.0)]
assert render._absorb_gaps([gap, face]) == [(face, 5.0)]
assert not render._is_gap(dict(face, flags=["gap_unfilled", "facecam"]))

from voxcut.brain import plan
from voxcut.brain.schemas import PLAN_SCHEMA
props = PLAN_SCHEMA["properties"]["items"]["items"]
assert set(props["required"]) == set(props["properties"]) and "visual_need" in props["required"]
bs = [{"id": f"b{i}", "start_s": i * 3.0, "end_s": i * 3.0 + 3, "text": "x", "gist": "x",
       "concrete_entities": []} for i in range(4)]
doc = plan.plan(bs, {}, use_llm=False, face_share=0.5)
assert facecam.face_share_of(doc["events"]) == 0.5
assert not any(facecam.is_facecam(e) for e in plan.plan(bs, {}, use_llm=False)["events"])
print("ALL OK")
