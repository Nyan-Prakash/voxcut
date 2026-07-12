"""LLM relevance judge for sourcing (quality gate).

The heuristic ranker orders search results, but title similarity can't tell
"actually contains this footage" from "vaguely mentions it". This judge shows
the LLM the beat's narration + the candidate list and asks which videos would
genuinely contain matching footage. Irrelevant results get rejected — a gap
held by the neighboring clip beats a random clip.
"""
from __future__ import annotations

from .client import structured

JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "relevance": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "relevance", "reason"],
            },
        }
    },
    "required": ["picks"],
}

JUDGE_SYSTEM = """\
You vet YouTube search results for a fast-cut commentary video editor.

You are given: the narration line (beat) the clip will play under, the visual
intent, and numbered search results (title / channel / duration / views).

Score each result 0..1 for how likely its VIDEO CONTENT actually contains
footage that fits the narration and intent — not just keyword overlap.

You are also shown each result's THUMBNAIL. The thumbnail is strong evidence of
what the footage looks like — a desk-and-microphone thumbnail means a muted
talking head (reject); an expressive face, mid-action shot, or recognizable
meme frame means good silent footage. Trust the thumbnail over the title when
they disagree.

CRITICAL — clips play MUTED under the narrator's voiceover. Judge every result
as if watched with the sound OFF:
- HIGH: visible action, physical comedy, exaggerated facial reactions,
  freakouts, fails, dramatic zooms, animated scenes, anything that reads
  instantly without audio.
- LOW: talking heads, interviews, podcasts, news anchors at desks, press
  conferences, commentary/explainer videos — a muted person talking is dead
  air on screen, even when the topic matches perfectly.
- A topically-weaker but visually-expressive clip beats a topically-perfect
  video of someone talking.

Judging rules:
- FUNNY BEATS ACCURATE. A winning candidate is either (a) clearly the literal
  thing, (b) a comically exaggerated version of it, or (c) an obviously absurd
  gag clip that commits to the bit. REJECT the mediocre middle — footage that
  is only somewhat related and not funny is the worst outcome, worse than no
  clip at all.
- ENERGY: if the narration is marked [PUNCHLINE], prefer chaotic high-energy
  footage (crashes, freakouts, sprints); if marked [setup], prefer calm/medium
  footage. Mismatched energy (chaos under a setup line) scores lower.
- clip_literal: the video must literally SHOW the named thing/event happening.
  A video that merely discusses or reacts to it scores low.
- clip_reaction / meme_image: prefer the canonical meme/reaction clip everyone
  knows; short clips from clip channels score high, long essays score low.
- Podcasts, hour-long discussions, news recaps, lyric videos, and tutorials are
  almost never good b-roll for a joke — score them low unless the beat is
  literally about them.
- Compilations are acceptable only if the wanted moment is clearly the subject.
- TONE: this is a comedy edit. Reject footage of real tragedy — death, violent
  crime, accidents, disasters, grief — regardless of relevance. A funny beat
  cut against someone's real misfortune reads as offensive, not funny.
- When in doubt, score low. Returning zero good picks is a valid answer —
  the editor lets the neighboring clip hold through the gap, which beats a
  random clip.

Return picks ONLY for results scoring >= 0.5, ordered best-first."""

JUDGE_USER = """\
Narration beat: "{beat_text}"
Visual intent: {intent}
Search queries used: {queries}

Results:
{results}"""


FRAME_SYSTEM = """\
You pick the exact MOMENT inside a downloaded video for a fast-cut commentary
edit. The clip plays MUTED under the narrator's voiceover.

You see numbered frames, each sampled from the middle of one candidate window.
Score each frame 0..1 for how well its window would play as the visual for the
narration:
- HIGH: the frame shows the named thing/action actually happening, an
  exaggerated facial reaction, physical comedy mid-action, an obviously absurd
  gag that commits to the bit, or the recognizable meme moment. Expressive and
  instantly readable with no sound. If the narration is marked [PUNCHLINE],
  chaotic high-energy moments win; if [setup], calm clear moments win.
- LOW: someone merely talking at the camera, static shots where nothing
  happens, title cards, intros/outros, channel branding, black/blurry
  transition frames, unrelated content.
Score every frame index exactly once. Be harsh: 0.8+ means "this exact moment
is the gag". If nothing fits, low scores everywhere are the right answer."""

FRAME_USER = """\
Narration beat: "{beat_text}"
Visual intent: {intent}
Video: {video_title}
{n} candidate windows; frame i is from the middle of window i."""


QC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string",
                                "enum": ["literal", "joke", "middle"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "verdict", "reason"],
            },
        }
    },
    "required": ["verdicts"],
}

QC_SYSTEM = """\
You audit the FINISHED edit of a fast-cut comedy commentary video. For each
numbered frame (one per clip, taken from the exact moment that plays) and the
narration line it plays under, apply the one law of this style:

Every clip must be either CLEARLY THE THING or CLEARLY A JOKE — never the
mediocre middle. Judge as muted footage (the narrator's voice plays over it).

- literal: the frame clearly SHOWS the specific thing the narration names.
  Plain and calm is fine — setup lines want literal grounding. What matters
  is that a viewer instantly goes "that's the thing he just said."
- joke: the frame clearly reads as a gag — absurd non-sequitur, exaggerated
  or chaotic version, expressive reaction, ironic understatement, or a
  recognizable meme moment. A viewer instantly gets that it's a bit.
- middle: the failure mode. Semi-related generic footage that neither shows
  the named thing nor lands as a joke: thematically-adjacent stock, a scene
  whose connection needs explaining, people vaguely doing things, footage
  that would make a viewer think "…that's not anything." Be harsh — when
  torn between joke and middle, ask whether the humor is actually VISIBLE
  in the frame; if you have to assume context, it's middle.

One verdict per frame index, each index exactly once, with a one-sentence
reason a video editor could act on."""

QC_USER = """\
{n} clips. For each, the narration line it plays under:
{lines}"""


def judge_qc(entries: list[tuple[str, str, str]]) -> list[dict | None]:
    """entries: (beat_text, kind, frame_data_url) per clip, in order.
    Returns per-entry {verdict, reason} (None where the judge skipped one).
    Raises BrainError on failure."""
    lines = "\n".join(f'{i}: [{kind}] "{text}"'
                      for i, (text, kind, _u) in enumerate(entries))
    images = [(f"Frame {i}:", url) for i, (_t, _k, url) in enumerate(entries)]
    out = structured(
        QC_SYSTEM, QC_USER.format(n=len(entries), lines=lines),
        QC_SCHEMA, schema_name="qc_audit", temperature=0.2,
        max_tokens=2500, images=images)
    verdicts: list[dict | None] = [None] * len(entries)
    for v in out.get("verdicts", []):
        if 0 <= v["index"] < len(entries):
            verdicts[v["index"]] = {"verdict": v["verdict"],
                                    "reason": v["reason"]}
    return verdicts


def judge_frames(beat_text: str, intent: str, video_title: str,
                 frames: list[str]) -> list[float]:
    """frames: list of data-URL jpegs, one per candidate window (in order).
    Returns a score 0..1 per frame. Raises BrainError on failure."""
    images = [(f"Frame {i}:", url) for i, url in enumerate(frames)]
    out = structured(
        FRAME_SYSTEM,
        FRAME_USER.format(beat_text=beat_text, intent=intent,
                          video_title=video_title, n=len(frames)),
        JUDGE_SCHEMA, schema_name="frame_judge", temperature=0.2,
        max_tokens=1500, images=images)
    scores = [0.0] * len(frames)
    for p in out.get("picks", []):
        if 0 <= p["index"] < len(frames):
            scores[p["index"]] = max(0.0, min(1.0, float(p["relevance"])))
    return scores


def judge_candidates(beat_text: str, intent: str, queries: list[str],
                     candidates: list[dict]) -> list[tuple[int, float]]:
    """Returns [(candidate_index, relevance)] best-first, only relevance >= 0.5.
    Raises BrainError if the LLM is unavailable/fails (caller falls back).

    Candidates may include a 'thumbnail' URL — shown to the model so it judges
    what the footage LOOKS like, not just what the title claims."""
    results = "\n".join(
        f"{i}: {c['title']!r} | channel: {c.get('channel','?')} | "
        f"{int(c.get('duration_s') or 0)}s | {c.get('views', 0)} views"
        for i, c in enumerate(candidates))
    images = [(f"Thumbnail for result {i}:", c["thumbnail"])
              for i, c in enumerate(candidates)
              if c.get("thumbnail", "").startswith("http")]
    out = structured(
        JUDGE_SYSTEM,
        JUDGE_USER.format(beat_text=beat_text, intent=intent,
                          queries=", ".join(queries), results=results),
        JUDGE_SCHEMA, schema_name="source_judge", temperature=0.2,
        max_tokens=2000, images=images or None)
    picks = [(p["index"], float(p["relevance"])) for p in out.get("picks", [])
             if 0 <= p["index"] < len(candidates) and p["relevance"] >= 0.5]
    picks.sort(key=lambda t: t[1], reverse=True)
    return picks
