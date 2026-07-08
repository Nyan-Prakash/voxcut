"""Standalone checks for the segmentation engine (no LLM, no server)."""
from voxcut.brain.segment import W, segment

# Build synthetic words: 40 words, 0.4s each, sentence breaks every ~8 words.
SENT = "Okay so this man really did fry the mixtape.".split()
words = []
t = 0.0
for i in range(40):
    tok = SENT[i % len(SENT)]
    words.append(W(idx=i, text=tok, start_s=round(t, 2), end_s=round(t + 0.3, 2)))
    t += 0.4
# Inject a couple of silences.
silences = [(3.2, 3.6), (12.5, 13.1)]
duration = words[-1].end_s + 0.2

beats = segment(words, silences, context="test video", density="normal",
                duration=duration, use_llm=False)

assert beats, "no beats produced"

# 1. Full coverage, contiguous, monotonic in word indices.
assert beats[0]["word_start_idx"] == 0, beats[0]
assert beats[-1]["word_end_idx"] == 39, beats[-1]
for a, b in zip(beats, beats[1:]):
    assert b["word_start_idx"] == a["word_end_idx"] + 1, (a, b)

# 2. Times monotonic and within bounds.
prev = -1.0
for bt in beats:
    assert bt["start_s"] < bt["end_s"], bt
    assert bt["start_s"] >= prev - 1e-6, (prev, bt)
    prev = bt["end_s"]
assert beats[0]["start_s"] == 0.0
assert beats[-1]["end_s"] <= duration + 1e-6

# 3. Min beat length enforced (allow the very last remainder to be short).
for bt in beats[:-1]:
    assert bt["end_s"] - bt["start_s"] >= 0.8 - 1e-6, bt

# 4. Every beat has required fields.
for bt in beats:
    for f in ("id", "gist", "tone", "emphasis", "visual_affinity",
              "concrete_entities", "locked"):
        assert f in bt, (f, bt)

print(f"OK — {len(beats)} beats, coverage 0..39, all invariants hold")
for bt in beats:
    print(f"  [{bt['word_start_idx']:2d}-{bt['word_end_idx']:2d}] "
          f"{bt['start_s']:5.2f}-{bt['end_s']:5.2f}  emph={bt['emphasis']}  {bt['text'][:50]}")
