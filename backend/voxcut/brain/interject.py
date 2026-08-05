"""LLM surface for the Interject tool.

Deliberately separate from the muted-clip prompts (plan.py / judge.py), whose
rules are INVERTED here: an interjection plays UNMUTED in a gap carved out of
the narration, so the clip's AUDIO is the joke — famous reaction lines,
screams, deadpan one-liners, meme sound moments. The exact footage the muted
judges reject (talking heads, spoken punchlines) is what this path hunts for.
"""
from __future__ import annotations

from .client import structured
from .judge import JUDGE_SCHEMA

INTERJECT_PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "queries": {
            "type": "array", "items": {"type": "string"},
            "description": "YouTube searches for audio-forward clips, "
                           "best-first (2-4)"},
        "comedic_intent": {
            "type": "string",
            "description": "one line: the bit this interjection lands "
                           "(shown to the operator)"},
        "min_s": {"type": "number"},
        "max_s": {"type": "number"},
    },
    "required": ["queries", "comedic_intent", "min_s", "max_s"],
}

INTERJECT_PLAN_SYSTEM = """\
You plan a comedic INTERJECTION for a fast-cut commentary video: the narrator
STOPS, a clip plays fullscreen WITH ITS OWN AUDIO for a couple of seconds,
then the narration resumes. The clip's AUDIO is the joke.

Favor: famous reaction lines, screams and freakouts, deadpan one-liners,
iconic quotes, meme sound moments ("emotional damage", the Windows shutdown
sound, a perfectly-timed "bruh"). The exact opposite of muted b-roll — a clip
whose payoff is silent is WRONG here.

Rules:
- NAME THE EXACT canonical clip a YouTube search resolves to one video
  ("Michael Scott no god please no", "emotional damage meme original") —
  never a generic pattern like "<emotion> meme". Draw from the full breadth
  of internet culture; the quoted names are FORMAT examples only.
- Return 2 angles among your queries: one that riffs on the literal content
  of the surrounding narration, one absurdist contrast that commits to the
  bit. Both must be audio-payload clips.
- min_s/max_s: the natural length of the audio payload you're hunting
  (setup optional, punchline mandatory), within 1.0-4.0 seconds.
- The interjection interrupts THIS moment of the narration — it must land as
  a response to what was just said, or a perfectly-timed non-sequitur."""

INTERJECT_PLAN_USER = """\
Video context: {context}
Avoid: {avoid}

Narration BEFORE the cut: "{before}"
Narration AFTER the cut: "{after}"

The narrator goes silent between those two lines and your clip plays with
full audio.{hint_block}"""

INTERJECT_JUDGE_SYSTEM = """\
You vet YouTube search results for an UNMUTED comedic interjection: the
narrator stops and this clip plays with FULL AUDIO for 1-4 seconds. The
clip's audio is the payload — the inverse of muted b-roll judging.

Score each result 0..1 for how likely its content contains the wanted
audio moment:
- HIGH: the canonical meme/reaction clip everyone knows, short clips whose
  title quotes the line ("no god please no"), screams, freakouts, iconic
  deliveries, meme sound uploads. Talking heads and spoken punchlines are
  GOOD here — the audio plays.
- LOW: music-only uploads, ambience, lyric videos, tutorials, podcasts and
  essays (the moment is buried), anything whose payoff is visual-only.
- COMPILATIONS ("top 100 memes", "best of…"): score <= 0.4 — finding the
  wanted two seconds inside a grab-bag rarely works.
- FRANCHISE FATIGUE: a franchise already used 2+ times in this video scores
  <= 0.4 unless uniquely perfect.
- Name each pick's "franchise" (show/film/creator), '' when unclear.
- TONE: comedy only — reject real tragedy regardless of relevance.
- When in doubt, score low. Zero picks is a valid answer: no interjection
  beats a weak one.

Return picks ONLY for results scoring >= 0.5, ordered best-first."""

INTERJECT_JUDGE_USER = """\
The interjection's intent: {intent}
Narration around the cut: "{before}" [CLIP PLAYS HERE] "{after}"
Search queries used: {queries}
Franchises already used in this video: {franchises}

Results:
{results}"""

INTERJECT_FRAME_SYSTEM = """\
You pick the exact WINDOW inside a downloaded video for an UNMUTED comedic
interjection (1-4s, full audio). You see numbered frames, one per candidate
window, plus each window's relative audio energy (0..1 — how loud/active its
soundtrack is; the payload line/scream usually lives in a high-energy window).

Score each window 0..1 for containing the COMPLETE audio payload — setup
optional, punchline mandatory, no dead air on either side:
- HIGH: the frame shows the moment mid-delivery (expressive face, mouth
  open, mid-freakout, the recognizable meme moment) and the window's audio
  energy supports a spoken/sounded payload.
- LOW: title cards, intros/outros, channel branding, static aftermath shots,
  windows whose energy suggests silence.
Score every frame index exactly once. Be harsh: 0.8+ means "this exact
window IS the sound bite"."""

INTERJECT_FRAME_USER = """\
The interjection's intent: {intent}
Video: {video_title}
{n} candidate windows; frame i is from the middle of window i.
Audio energy per window: {energies}"""


def plan_interject(context: str, avoid: str, before: str, after: str,
                   hint: str | None = None) -> dict:
    """One LLM call → {queries, comedic_intent, min_s, max_s} (clamped to
    1.0-4.0s). Raises BrainError when the LLM is unavailable/fails."""
    hint_block = (f"\n\nOPERATOR DIRECTION — follow it; it wins over every "
                  f"rule above, but keep queries specific and "
                  f"YouTube-searchable: {hint.strip()}" if hint and hint.strip()
                  else "")
    out = structured(
        INTERJECT_PLAN_SYSTEM,
        INTERJECT_PLAN_USER.format(context=context, avoid=avoid or "(none)",
                                   before=before, after=after,
                                   hint_block=hint_block),
        INTERJECT_PLAN_SCHEMA, schema_name="interject_plan",
        temperature=0.7, max_tokens=800)
    lo = max(1.0, min(4.0, float(out.get("min_s", 1.5))))
    hi = max(lo, min(4.0, float(out.get("max_s", 3.0))))
    return {"queries": [q for q in out.get("queries", []) if q.strip()][:4],
            "comedic_intent": (out.get("comedic_intent") or "").strip(),
            "min_s": lo, "max_s": hi}


def judge_interject_candidates(intent: str, before: str, after: str,
                               queries: list[str], candidates: list[dict],
                               franchise_counts: dict[str, int] | None = None,
                               ) -> list[tuple[int, float, str]]:
    """Same contract as judge.judge_candidates, inverted rules: rewards
    audio-forward footage. Returns [(index, relevance, franchise)]."""
    results = "\n".join(
        f"{i}: {c['title']!r} | channel: {c.get('channel', '?')} | "
        f"{int(c.get('duration_s') or 0)}s | {c.get('views', 0)} views"
        for i, c in enumerate(candidates))
    images = [(f"Thumbnail for result {i}:", c["thumbnail"])
              for i, c in enumerate(candidates)
              if c.get("thumbnail", "").startswith("http")]
    franchises = ", ".join(f"{k} ×{v}" for k, v in (franchise_counts or {}).items()
                           if v) or "(none yet)"
    out = structured(
        INTERJECT_JUDGE_SYSTEM,
        INTERJECT_JUDGE_USER.format(intent=intent, before=before, after=after,
                                    queries=", ".join(queries),
                                    results=results, franchises=franchises),
        JUDGE_SCHEMA, schema_name="interject_judge", temperature=0.2,
        max_tokens=2000, images=images or None)
    picks = [(p["index"], float(p["relevance"]), (p.get("franchise") or "").strip())
             for p in out.get("picks", [])
             if 0 <= p["index"] < len(candidates) and p["relevance"] >= 0.5]
    picks.sort(key=lambda t: t[1], reverse=True)
    return picks


def judge_interject_frames(intent: str, video_title: str, frames: list[str],
                           energies: list[float]) -> list[float]:
    """Score candidate windows (frame + audio energy per window). Returns a
    0..1 score per window. Raises BrainError on failure."""
    images = [(f"Frame {i}:", url) for i, url in enumerate(frames)]
    en = ", ".join(f"{i}: {e:.2f}" for i, e in enumerate(energies))
    out = structured(
        INTERJECT_FRAME_SYSTEM,
        INTERJECT_FRAME_USER.format(intent=intent, video_title=video_title,
                                    n=len(frames), energies=en),
        JUDGE_SCHEMA, schema_name="interject_frame", temperature=0.2,
        max_tokens=1500, images=images)
    scores = [0.0] * len(frames)
    for p in out.get("picks", []):
        if 0 <= p["index"] < len(frames):
            scores[p["index"]] = max(0.0, min(1.0, float(p["relevance"])))
    return scores
