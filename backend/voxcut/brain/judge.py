"""LLM relevance judge for sourcing (quality gate).

The heuristic ranker orders search results, but title similarity can't tell
"actually contains this footage" from "vaguely mentions it". This judge shows
the LLM the beat's narration + the candidate list and asks which videos would
genuinely contain matching footage. Irrelevant results get rejected — a caption
card beats a random clip.
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
  the editor falls back to a stylish caption card, which beats a random clip.

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
