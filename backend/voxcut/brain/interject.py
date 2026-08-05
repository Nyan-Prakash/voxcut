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
        "ideas": {
            "type": "array",
            "description": "4-6 DISTINCT interjection ideas, each from a "
                           "different franchise/source",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "queries": {
                        "type": "array", "items": {"type": "string"},
                        "description": "1-2 YouTube searches resolving to "
                                       "this idea's canonical clip"},
                    "comedic_intent": {
                        "type": "string",
                        "description": "one line: the bit this interjection "
                                       "lands (shown to the operator)"},
                    "min_s": {"type": "number"},
                    "max_s": {"type": "number"},
                },
                "required": ["queries", "comedic_intent", "min_s", "max_s"],
            },
        }
    },
    "required": ["ideas"],
}

INTERJECT_PLAN_SYSTEM = """\
You plan a comedic INTERJECTION for a fast-cut commentary video: the narrator
STOPS, a clip plays fullscreen WITH ITS OWN AUDIO for a couple of seconds,
then the narration resumes. The clip's AUDIO is the joke.

Favor: famous reaction lines, deadpan one-liners, iconic quoted deliveries,
recognizable meme sound moments — and only sparingly a scream/freakout with
recognizable CHARACTER (a known scene, a known voice). The payload must be
INTELLIGIBLE and recognizable on first listen: a clear line, a famous
delivery, a known sound. Random shouting, crowd noise, or generic loudness
is the failure mode — loud is not funny. The exact opposite of muted b-roll:
a clip whose payoff is silent is equally WRONG here.

Rules:
- NAME THE EXACT canonical clip: each query combines the SOURCE (the show,
  film, anime, game, streamer, athlete, or person) with the SPECIFIC moment
  or quoted line, so a YouTube search resolves to one canonical video. Never
  a generic pattern like "<emotion> meme" or "funny scream clip".
- VARIETY IS THE POINT. Draw from the FULL breadth of internet culture — TV,
  film, anime, cartoons, sports, gaming, streamers, vines, news bloopers,
  ads, music moments — and pick whatever genuinely fits THIS beat of THIS
  video. Do not fall back on an all-purpose famous reaction: if a clip would
  fit under almost any narration, it is too generic — find the one that fits
  THIS narration specifically. Never pick anything on the already-used list,
  any other upload of the same scene, or (unless uniquely perfect) the same
  franchise.
- Return 4-6 DISTINCT ideas, ORDERED BEST-FIRST — the editor tries them in
  order until one sources cleanly. Each idea must come from a DIFFERENT
  franchise/source AND a different comedic register — spread across: a
  literal riff on what was just said, an absurd non-sequitur, a deadpan
  one-liner, an iconic quoted delivery. Every idea must stand alone as a
  great interjection for this exact moment — no throwaway filler ideas.
- min_s/max_s per idea: the natural length of that audio payload (setup
  optional, punchline mandatory), within 1.0-4.0 seconds.
- The interjection interrupts THIS moment of the narration — it must land as
  a response to what was just said, or a perfectly-timed non-sequitur."""

INTERJECT_PLAN_USER = """\
Video context: {context}
Avoid: {avoid}
Already used in this video (never these again, nor other uploads of the same
scene, nor their franchises): {used}

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
- HIGH: the canonical meme/reaction clip everyone knows, short
  isolated-scene uploads whose TITLE quotes the line — the title is strong
  evidence the wanted payload is the clip's centerpiece. Talking heads and
  spoken punchlines are GOOD here — the audio plays.
- LOW: music-only uploads, ambience, lyric videos, tutorials, podcasts and
  essays (the moment is buried), anything whose payoff is visual-only, and
  GENERIC LOUDNESS — crowd reactions, sports screaming, "loudest moments",
  people just yelling. An interjection needs a recognizable line or sound,
  not noise; score noise <= 0.2 unless the intent asks for exactly that.
- COMPILATIONS ("top 100 memes", "best of…"): score <= 0.4 — finding the
  wanted two seconds inside a grab-bag rarely works.
- ALREADY USED: you are told which clips were already interjected.
  REJECT (score 0) any result that is the same moment or scene — including a
  different upload of it under another title or channel. Repeating a
  punchline kills it.
- FRANCHISE FATIGUE, two tiers: a franchise already used in THIS video
  scores <= 0.4 unless uniquely perfect. A franchise merely RECENT in the
  editor's OTHER videos gets a mild penalty (about -0.15), NOT a rejection —
  never blanket-ban a whole show because one clip of it ran last week.
- Name each pick's "franchise" (show/film/creator), '' when unclear.
- TONE: comedy only — reject real tragedy regardless of relevance.
- When in doubt, score low — but reserve ZERO picks for genuinely unusable
  result sets. A promising candidate at 0.5-0.6 is worth passing through:
  the downstream window judge hears the actual audio and catches misses.

Return picks ONLY for results scoring >= 0.5, ordered best-first."""

INTERJECT_JUDGE_USER = """\
The interjection's intent: {intent}
Narration around the cut: "{before}" [CLIP PLAYS HERE] "{after}"
Search queries used: {queries}
Clips already interjected (reject the same moment in ANY upload): {used}
Franchises already used in THIS video: {franchises}
Franchises recently used in the editor's OTHER videos (mild penalty only):
{recent_franchises}

Results:
{results}"""

INTERJECT_FRAME_SYSTEM = """\
You pick the exact WINDOW inside a downloaded video for an UNMUTED comedic
interjection (1-4s, full audio). The narrator goes silent for it, so the
window's AUDIO must carry the joke by itself.

For each numbered window you get: one frame from its middle, its relative
audio energy (0..1), and a TRANSCRIPT of what is actually said inside it
("(no speech)" when nothing intelligible was heard). The transcript is your
primary evidence — it is what the viewer will hear.

Score each window 0..1 for being the COMPLETE, INTELLIGIBLE audio payload
the intent describes — setup optional, punchline mandatory, no dead air:
- HIGH: the transcript IS the wanted line/delivery, complete (not cut off
  mid-sentence at either end), and the frame shows the moment mid-delivery
  (the character speaking, the recognizable meme moment).
- PUNISH HARD: random yelling or crowd noise, distorted screaming with no
  recognizable content, music beds, half a line, a reaction shot WITHOUT the
  payload, title cards, intros/outros, channel branding. Loud is not funny —
  a window that is merely high-energy with a garbage transcript scores
  <= 0.2, unless the intent explicitly asks for a wordless famous scream.
- A quiet deadpan line matching the intent BEATS a loud unrelated shout.
- An EMPTY transcript with high energy may still be a wordless payload — a
  famous scream, a musical sting, a sound effect. Judge those by the frame
  and the intent; punish emptiness only when the intent needs words.
- CALIBRATION: 0.5 means "usable — a complete, intelligible payload that
  fits the intent reasonably well"; reserve 0.8+ for the exact canonical
  moment. Do NOT score a usable window below 0.4 for being merely imperfect.
This is NOT a selection — it is an exhaustive scoring pass. Return one entry
for EVERY window index from 0 to n-1, including the bad ones (give those an
explicit low score with the reason). Never omit a window."""

INTERJECT_FRAME_USER = """\
The interjection's intent: {intent}
Video: {video_title}
{n} candidate windows; frame i is from the middle of window i.
Per-window audio energy: {energies}
Per-window transcript of what is SAID:
{transcripts}"""


def plan_interject(context: str, avoid: str, before: str, after: str,
                   hint: str | None = None,
                   used_clips: list[str] | None = None) -> list[dict]:
    """One LLM call → a LIST of 4-6 distinct interjection ideas, each
    {queries, comedic_intent, min_s, max_s} (clamped to 1.0-4.0s). The
    caller samples ONE at random — server-side randomness is what actually
    breaks the model's habit of converging on its single favorite clip.
    used_clips: titles/franchises already used — never propose them again.
    Raises BrainError when the LLM is unavailable/fails."""
    hint_block = (f"\n\nOPERATOR DIRECTION — follow it; it wins over every "
                  f"rule above (including variety), but keep queries specific "
                  f"and YouTube-searchable: {hint.strip()}"
                  if hint and hint.strip() else "")
    out = structured(
        INTERJECT_PLAN_SYSTEM,
        INTERJECT_PLAN_USER.format(context=context, avoid=avoid or "(none)",
                                   used=", ".join(used_clips or []) or "(none yet)",
                                   before=before, after=after,
                                   hint_block=hint_block),
        INTERJECT_PLAN_SCHEMA, schema_name="interject_plan",
        temperature=0.8, max_tokens=2000)
    ideas = []
    for it in out.get("ideas", []):
        queries = [q for q in it.get("queries", []) if q.strip()][:2]
        if not queries:
            continue
        lo = max(1.0, min(4.0, float(it.get("min_s", 1.5))))
        hi = max(lo, min(4.0, float(it.get("max_s", 3.0))))
        ideas.append({"queries": queries,
                      "comedic_intent": (it.get("comedic_intent") or "").strip(),
                      "min_s": lo, "max_s": hi})
    return ideas


def judge_interject_candidates(intent: str, before: str, after: str,
                               queries: list[str], candidates: list[dict],
                               franchise_counts: dict[str, int] | None = None,
                               used_clips: list[str] | None = None,
                               recent_franchises: list[str] | None = None,
                               ) -> list[tuple[int, float, str]]:
    """Same contract as judge.judge_candidates, inverted rules: rewards
    audio-forward footage. used_clips: already-interjected clips — the same
    moment in any upload is rejected outright. franchise_counts: THIS video's
    franchise usage (hard fatigue); recent_franchises: cross-video recency
    (mild penalty only). Returns [(index, relevance, franchise)]."""
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
                                    used=", ".join(used_clips or []) or "(none yet)",
                                    results=results, franchises=franchises,
                                    recent_franchises=", ".join(
                                        recent_franchises or []) or "(none)"),
        JUDGE_SCHEMA, schema_name="interject_judge", temperature=0.2,
        max_tokens=2000, images=images or None)
    picks = [(p["index"], float(p["relevance"]), (p.get("franchise") or "").strip())
             for p in out.get("picks", [])
             if 0 <= p["index"] < len(candidates) and p["relevance"] >= 0.5]
    picks.sort(key=lambda t: t[1], reverse=True)
    return picks


# Dedicated exhaustive-scoring schema: the shared JUDGE_SCHEMA's "picks"
# framing reads as a SELECTION, and the model omits windows it wouldn't
# pick — omissions parsed as 0.0 produced "everything scored 0.0" runs.
FRAME_SCORE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scores": {
            "type": "array",
            "description": "exactly one entry per window index, 0..n-1",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "score", "reason"],
            },
        }
    },
    "required": ["scores"],
}


def judge_interject_frames(intent: str, video_title: str, frames: list[str],
                           energies: list[float],
                           transcripts: list[str] | None = None) -> list[float]:
    """Score candidate windows (frame + audio energy + transcript of what is
    actually said in each). Exhaustive: every window gets a score. Returns a
    0..1 score per window. Raises BrainError on failure."""
    images = [(f"Frame {i}:", url) for i, url in enumerate(frames)]
    en = ", ".join(f"{i}: {e:.2f}" for i, e in enumerate(energies))
    tr = "\n".join(f'{i}: "{(t or "(no speech)").strip()}"'
                   for i, t in enumerate(transcripts or [""] * len(frames)))
    out = structured(
        INTERJECT_FRAME_SYSTEM,
        INTERJECT_FRAME_USER.format(intent=intent, video_title=video_title,
                                    n=len(frames), energies=en,
                                    transcripts=tr),
        FRAME_SCORE_SCHEMA, schema_name="interject_frame_scores",
        temperature=0.2, max_tokens=1500, images=images)
    scores = [0.0] * len(frames)
    for p in out.get("scores", []):
        if 0 <= p["index"] < len(frames):
            scores[p["index"]] = max(0.0, min(1.0, float(p["score"])))
    return scores
