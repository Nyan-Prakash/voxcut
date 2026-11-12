"""Offline checks for the clip-selection plumbing — no network, no LLM.

Run from backend/:  uv run python tests_selection.py
"""
import numpy as np

from voxcut.brain import judge, plan
from voxcut.brain.schemas import PLAN_SCHEMA
from voxcut.brain.steps_helpers import beat_neighbors
from voxcut.jobs.steps import moment, source
from voxcut.moments.select import score_at
from voxcut.moments.timing import place_in_point
from voxcut.sourcing.base import Candidate, Filters

# ---- in-point placement -----------------------------------------------------
# Nothing notable → window stays put.
assert place_in_point(10.0, 3.0, "middle", [], 60.0) == 10.0
assert place_in_point(10.0, 3.0, "none", [], 60.0) == 10.0
# Payoff on the last frame → slide later so it lands mid-clip.
assert place_in_point(10.0, 3.0, "end", [], 60.0) == 10.9
# … but never past the end of the file.
assert place_in_point(58.0, 3.0, "end", [], 60.0) == 57.0
# A cut 0.2s after the in-point → start just past the cut.
assert place_in_point(10.0, 3.0, "middle", [10.2], 60.0) == 10.24
# A cut 0.2s before the out-point → end just before it.
assert place_in_point(10.0, 3.0, "middle", [12.8], 60.0) == 9.76
# In-point already on a cut → untouched.
assert place_in_point(10.0, 3.0, "start", [10.0], 60.0) == 10.0
# Unknown asset duration must not clamp to zero.
assert place_in_point(10.0, 3.0, "end", [], 0.0) == 10.9
print("timing OK")

# ---- signal credit for vision-found windows --------------------------------
curve = {"starts": [0.0, 1.0, 2.0, 3.0],
         "fused": np.array([0.1, 0.9, 0.4, 0.0], dtype=np.float32), "win": 2.0}
assert abs(score_at(curve, 1.2) - 0.9) < 1e-6
assert score_at({}, 5.0) == 0.0
print("score_at OK")

# ---- ranking: each candidate against the query that found it ---------------
def cand(sid, title):
    return Candidate(provider="youtube", source_id=sid, url="u", title=title,
                     duration_s=60, view_count=1000, channel="c",
                     channel_verified=False, live=False, thumbnail="")

prim = [cand(f"p{i}", f"fire alarm pulled in school hallway take {i}")
        for i in range(16)]
jokes = [cand(f"j{i}", f"cat knocks glass off table clip {i}") for i in range(5)]
found = {c.source_id: "fire alarm pulled school hallway" for c in prim}
found.update({c.source_id: "cat knocks glass off table" for c in jokes})
angle = {c.source_id: "primary" for c in prim}
angle.update({c.source_id: "joke" for c in jokes})
top = source._rank_by_own_query(prim + jokes, found, angle, Filters(), 14)
n_joke = sum(1 for c in top if angle[c.source_id] == "joke")
assert len(top) == 14, len(top)
assert n_joke >= source.JOKE_MIN_KEEP, n_joke
assert [c.score for c in top] == sorted((c.score for c in top), reverse=True)
print(f"ranking OK ({n_joke} joke-angle candidates reach the judge)")

# ---- context + gag reach the judges ----------------------------------------
beats = [{"id": "a", "start_s": 0, "text": "first"},
         {"id": "b", "start_s": 1, "text": "second"},
         {"id": "c", "start_s": 2, "text": "third"}]
nb = beat_neighbors(beats)
assert nb["a"] == ("", "second") and nb["b"] == ("first", "third")
extra = judge._judge_extra("lizard in a suit", nb["b"])
assert "first" in extra and "third" in extra and "lizard in a suit" in extra
assert judge._judge_extra("", None) == ""
# Prompt templates still format (a stray brace would raise here).
judge.JUDGE_USER.format(beat_text="x", intent="broll", queries="q",
                        franchises="-", results="r", extra=extra)
judge.FRAME_USER.format(beat_text="x", intent="broll", video_title="t", n=3,
                        extra="")
plan.PLAN_SYSTEM.format(avoid="(none)")
props = PLAN_SCHEMA["properties"]["items"]["items"]
assert set(props["required"]) == set(props["properties"])  # strict mode
fj = judge.FRAME_JUDGE_SCHEMA["properties"]["windows"]["items"]
assert set(fj["required"]) == set(fj["properties"])
ev = plan._item_to_event(
    {"id": "b", "start_s": 0.0, "end_s": 2.0},
    {"kind": "broll", "queries": ["q"], "joke_queries": [], "gag": " g ", "joke_gag": "",
     "mode": "absurd", "audio_mode": "mute"})
assert ev["gag"] == "g" and ev["mode"] == "absurd"
print("prompts + schemas OK")

# ---- frame-judge parsing: action_at survives enforcement -------------------
judge.structured = lambda *a, **k: {
    "windows": [
        {"index": 0, "seen": "", "score": 0.9, "reason": "", "action_at": "end",
         "shows_named_subject": True},
        {"index": 1, "seen": "", "score": 0.8, "reason": "",
         "action_at": "middle", "is_talking_head": True,
         "shows_named_subject": False}],
    "ranking": [0, 1]}
recs = judge.judge_frames_detailed("x", "broll", "t", ["u0", "u1"])
assert recs[0]["score"] == 0.9 and recs[0]["action_at"] == "end"
assert recs[1]["score"] == 0.0  # hard-fail flag still zeroes
assert moment._source_for({"in_s": 3.0, "out_s": 5.0, "judged_in_s": 2.4}) == {
    "in_s": 3.0, "out_s": 5.0, "chosen_rank": 1, "judged_in_s": 2.4}
assert moment.FINALIST_SKIP_AT == 0.8
print("frame judge parsing OK")
print("ALL OK")

# ---- queries carry their named source --------------------------------------
assert plan._anchor_queries(["disgusted face reaction", "Office Jim cringe"],
                            "The Office") == [
    "The Office disgusted face reaction", "Office Jim cringe"]
assert plan._anchor_queries(["hand pulling fire alarm"], "stock") == [
    "hand pulling fire alarm"]
assert plan._anchor_queries(["x"], "") == ["x"]
print("source anchoring OK")

# ---- the action lands on the punch word ------------------------------------
from voxcut.moments.timing import place_in_point as _pip, punch_offset

_words = [(" the", 10.0), (" dog", 10.4), (" ate", 11.0), (" my", 11.6),
          (" Homework.", 12.2)]
assert punch_offset("homework", _words, 10.0) == 2.2          # case/punct
assert punch_offset("cat", _words, 10.0) is None              # paraphrased
assert punch_offset("", _words, 10.0) is None
assert punch_offset("ate my", _words, 10.0) == 1.0            # phrase → 1st word
assert punch_offset("the", [(" the", 1.0), (" the", 2.0)], 1.0) == 1.0  # last

# 4s clip, action in the middle (2.0s in), word spoken 1.0s in → start 1.0s
# later so the action plays as the word is said.
assert _pip(20.0, 4.0, "middle", [], 100.0, punch_off=1.0) == 21.0
# action at the start (0.6s in), word at 2.0s → start earlier, capped at 35%.
assert _pip(20.0, 4.0, "start", [], 100.0, punch_off=2.0) == 18.6
# word at the very end: the action still gets room to play out (lands 3.0s in).
assert _pip(20.0, 4.0, "middle", [], 100.0, punch_off=3.9) == 19.0
# nothing to line up: unlocated action, no punch word, or a very short clip.
assert _pip(20.0, 4.0, "throughout", [], 100.0, punch_off=1.0) == 20.0
assert _pip(20.0, 4.0, "middle", [], 100.0) == 20.0
assert _pip(20.0, 1.2, "middle", [], 100.0, punch_off=0.1) == 20.0
# without a punch word the old late-payoff rule still applies.
assert _pip(20.0, 4.0, "end", [], 100.0) == 21.2
# never before the start of the file.
assert _pip(0.5, 4.0, "start", [], 100.0, punch_off=3.0) == 0.0

from voxcut.jobs.steps.moment import _punch_off
_beat = {"start_s": 10.0, "end_s": 13.0, "words": _words}
assert _punch_off({"start_s": 10.0, "end_s": 13.0, "punch_word": "ate"}, _beat) == 1.0
assert _punch_off({"start_s": 10.0, "end_s": 11.5, "punch_word": "ate"}, _beat) is None
assert "punch_word" in plan._item_to_event(
    {"id": "b", "start_s": 0, "end_s": 1},
    {"kind": "broll", "queries": ["x"], "punch_word": " ate "}) and True
print("punch-word alignment OK")
