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
                    "franchise": {
                        "type": "string",
                        "description": "franchise/show/creator the clip is "
                                       "from (e.g. 'SpongeBob', 'The Office'),"
                                       " or '' if none/unclear"},
                },
                "required": ["index", "relevance", "reason", "franchise"],
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
- COMPILATIONS ("TOP 100 MEMES", "best of…", "funny moments", "try not to
  laugh"): score <= 0.4. Finding the wanted two seconds inside a 10-minute
  grab-bag rarely works, and compilation frames look recycled. Exception:
  the narration itself is about montages/compilations.
- FRANCHISE FATIGUE: you are told which franchises this video already uses.
  A franchise already used 2+ times scores <= 0.4 unless this candidate is
  uniquely perfect — three SpongeBob cutaways in one video reads lazy.
- Name each pick's "franchise" (show/film/creator, e.g. 'SpongeBob',
  'The Office'); use '' when it's stock/generic/unclear.
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
Franchises already used in this video: {franchises}

Results:
{results}"""


FRAME_JUDGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "windows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer"},
                    "seen": {"type": "string",
                             "description": "what is literally visible across "
                                            "the three frames, <= 15 words"},
                    "has_subtitles_or_small_text": {
                        "type": "boolean",
                        "description": "subtitles, captions, small labels, "
                                       "credits or corner text over real "
                                       "footage (not a hard fail)"},
                    "has_small_corner_watermark": {
                        "type": "boolean",
                        "description": "a small creator/app/clip-channel "
                                       "mark on otherwise usable footage "
                                       "(not a hard fail)"},
                    "is_title_card_or_text_overlay": {
                        "type": "boolean",
                        "description": "HARD FAIL: text larger than a subtitle "
                                       "that titles, numbers, labels or captions "
                                       "the clip (title/number/chapter/list "
                                       "cards, a numeral plus a word over "
                                       "letterbox bars, end screens, comparison "
                                       "grids, meme text panels, thumbnails), "
                                       "even with footage visible behind it"},
                    "is_logo_or_watermark_or_branding": {
                        "type": "boolean",
                        "description": "HARD FAIL: branding that marks the "
                                       "footage as documentary, nature/science, "
                                       "news, educational, corporate or stock "
                                       "content, a lower-third, a sponsor or "
                                       "intro/outro card, or a mark big enough "
                                       "to pull the eye from the action. FALSE "
                                       "for a network bug or creator/app/clip "
                                       "mark on comedy, cartoon, movie, viral "
                                       "or meme footage — that goes in "
                                       "has_small_corner_watermark"},
                    "is_talking_head": {
                        "type": "boolean",
                        "description": "HARD FAIL: a real person presenting, "
                                       "explaining, vlogging, podcasting, "
                                       "anchoring or being interviewed is the "
                                       "content. FALSE for reactions, freakouts, "
                                       "scripted scenes, cartoons"},
                    "nothing_readable": {
                        "type": "boolean",
                        "description": "HARD FAIL: a viewer could not tell "
                                       "within a second what is on screen "
                                       "(blur, dark, transition, texture, "
                                       "smear, empty frame)"},
                    "is_product_or_toy_demo": {
                        "type": "boolean",
                        "description": "toys, products or merchandise being "
                                       "handled, unboxed, demonstrated or "
                                       "displayed (capped, not zeroed)"},
                    "shows_named_subject": {
                        "type": "boolean",
                        "description": "the thing/event the narration names is "
                                       "actually visible (not implied)"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["index", "seen", "has_subtitles_or_small_text",
                             "has_small_corner_watermark",
                             "is_title_card_or_text_overlay",
                             "is_logo_or_watermark_or_branding",
                             "is_talking_head", "nothing_readable",
                             "is_product_or_toy_demo",
                             "shows_named_subject", "score", "reason"],
            },
        },
        "ranking": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "every window index exactly once, best-first",
        },
    },
    "required": ["windows", "ranking"],
}

# Any of these true → the code scores the window 0.0 regardless of `score`.
# They are the failure classes a lenient scorer kept waving through: a
# "7." list card, a branded documentary close-up, a presenter at a desk, a
# blur. Making them booleans the model must answer, instead of hoping the
# score reflects them, is what makes the gate enforceable.
HARD_FAIL_FLAGS = ("is_title_card_or_text_overlay",
                   "is_logo_or_watermark_or_branding",
                   "is_talking_head", "nothing_readable")
# Soft fail: a product shot can be the "literal" thing in a lenient reading
# ("toy animals go in a trailer" for a line about separating animals) and
# still be dead footage — it never gets to compete with real moments. Capped
# below ESCALATE_BELOW in moment.py so the tournament looks elsewhere.
SOFT_FAIL_CAPS = {"is_product_or_toy_demo": 0.4}
# Reported, never enforced: a place for the model to note a subtitle or a
# corner mark without reaching for the hard-fail flag.
INFO_FLAGS = ("has_subtitles_or_small_text", "has_small_corner_watermark")

FRAME_SYSTEM = """\
You verify the exact MOMENT inside a downloaded video for a commentary edit.
The clip plays MUTED under the narrator's voiceover, so judge every window
as silent footage a viewer sees for a couple of seconds.

Each numbered image is one candidate WINDOW shown as three frames tiled in
time order (top→bottom, or left→right for vertical video): the start, the
middle and the end of the window. Read all three — they tell you what the
window DOES, not what one lucky frame looks like.

STEP 1 — flags, answered for every window.

First the two REPORT-ONLY flags. They never fail a window; they exist so
you can note a mark without reaching for a hard fail:
- has_subtitles_or_small_text: subtitle-sized text — subtitles, captions,
  small labels, credits or corner text — sits over real footage. Text
  that is bigger than a subtitle and names, numbers or titles the clip is
  not "small": that is the hard-fail flag below.
- has_small_corner_watermark: a small creator, clip-channel, app or
  meme-site mark sits in a corner of otherwise usable footage. A TV
  comedy, a cartoon, a movie, a viral clip or a meme with a corner mark is
  still that footage — report the mark here and nowhere else.

Then the HARD-FAIL flags. Any true one makes the window unusable; the
editor will score it 0 no matter what. They are for footage that is
unusable, not merely imperfect:
- is_title_card_or_text_overlay: text or graphics ARE the content, or a
  text panel/bar takes a large share of the frame — title, number, chapter
  or list cards, a numeral plus a word over letterbox bars (compilation
  item cards), end screens, caption cards, side-by-side comparison grids,
  meme templates whose text panels are the joke, thumbnails. It counts
  even when footage is visible behind or beside the text.
- is_logo_or_watermark_or_branding: branding marks the footage as
  documentary, news, educational, corporate or stock content — a nature or
  science channel logo on a close-up, a news bug or lower-third, a sponsor
  or intro/outro card — or a watermark big enough to pull the eye away
  from the action.
- is_talking_head: a real person presenting, explaining, vlogging,
  podcasting, anchoring or being interviewed is the content, delivered
  calmly to a camera or mic. NOT an exaggerated facial reaction, a
  freakout, a famous reaction moment, characters in a scripted scene, or
  animated/cartoon characters — those are footage, even when they speak.
- nothing_readable: a viewer could not tell within a second what they are
  looking at — blurry, dark, washed out, mid-transition, a patch of ground
  or texture, motion smear, a mostly empty frame.
Then the SOFT-FAIL flag, which caps the score at 0.4 (the editor looks for
another source) instead of zeroing it:
- is_product_or_toy_demo: toys, products or merchandise being handled,
  unboxed, demonstrated or displayed — a product shot can match the words
  of a line and still be dead footage under it.
Also record shows_named_subject: is the thing or event the narration names
literally visible in the frames (not implied, not a category cousin)?

STEP 2 — score 0..1 for how well the window plays as the visual for this
narration, muted. Calibration — hold to it:
- 0.8–1.0: THE exact gag. The frames show precisely the named thing/event
  happening, the recognizable meme moment, or physical comedy / an
  exaggerated reaction that reads instantly with no sound and fits the
  line. Rare — usually at most one window in a video earns this.
- 0.6–0.8: clearly shows the named thing or is clearly funny, but it is not
  the peak moment (the approach, the aftermath, a milder take). When the
  narration quotes or references a specific show, scene or meme and the
  video title says this IS that scene, a clear frame of that scene with
  its recognizable characters is the literal thing — at least 0.6 even
  when the frame itself is calm.
- 0.2–0.5: where MOST windows should land — related footage that is
  generic, static, needs context to connect, or shows the subject while
  nothing happens. "That is sort of the topic" is a 0.3, not a 0.7.
- 0.0–0.2: unrelated, or any hard-fail flag.
Generic means generic: compilation-style recycled footage, toys or
products being handled or unboxed, stock b-roll of the category,
educational close-ups, people vaguely doing things. Cap those in the
0.2–0.5 band unless the exact action the line calls for is visible. A
window whose subject is only a cousin of what the line names (same
category, different thing) is 0.5 at best.
Energy: if the narration is marked [PUNCHLINE], chaotic high-energy moments
win; if marked [setup], calm clear moments win. Mismatched energy loses
points.

Return one entry per window index — every index exactly once — and a forced
"ranking" of ALL indices best-first: even when scores tie, decide which one
you would cut to. Scores must be consistent with the ranking."""

FRAME_USER = """\
Narration beat: "{beat_text}"
Visual intent: {intent}
Video title: {video_title}
{n} candidate windows; image i is window i, three frames start→middle→end."""


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


HIGHLIGHT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start_beat": {"type": "integer"},
                    "end_beat": {"type": "integer"},
                    "title": {"type": "string",
                              "description": "short punchy caption/title for "
                                             "the clip (<= 8 words)"},
                    "hook": {"type": "string",
                             "description": "the opening line that stops the "
                                            "scroll, quoted from the narration"},
                    "reason": {"type": "string"},
                    "score": {"type": "number"},
                },
                "required": ["start_beat", "end_beat", "title", "hook",
                             "reason", "score"],
            },
        }
    },
    "required": ["clips"],
}

HIGHLIGHT_SYSTEM = """\
You scout a FINISHED fast-cut commentary video for TikTok-worthy clips.

You are given the video's narration as numbered beats (each with its start
time and duration) and, when available, one frame per beat showing what plays
on screen during it. Propose up to 6 clips, each a contiguous beat range
[start_beat..end_beat], that would work as standalone TikToks.

What makes a clip TikTok-worthy:
- HOOK FIRST: the opening beat must grab in under 2 seconds — a bold claim,
  a question, a punchline setup, chaos on screen. If a viewer needs the
  previous 30 seconds to get it, it is NOT a clip.
- SELF-CONTAINED: the range tells a complete micro-story — setup and payoff
  both inside the range. Never start mid-reference ("and THAT'S why...") and
  never end on an unresolved setup.
- END ON THE PAYOFF: cut right after the punchline/reveal lands. Trailing
  wind-down beats kill rewatch value.
- LENGTH: 8–60 seconds total. 15–35s is the sweet spot.
- VISUALS COUNT: when frames are provided, favor ranges whose footage is
  expressive and readable on a phone (action, reactions, memes) over ranges
  of generic b-roll.

Scoring (0..1, be harsh):
- 0.8+: you would bet on this clip — killer hook, tight arc, strong visuals.
- 0.5–0.8: solid, worth posting.
- below 0.5: don't return it.
Fewer great clips beat many mediocre ones. Zero clips is a valid answer when
nothing stands alone. Clips may not overlap. Order best-first."""

HIGHLIGHT_USER = """\
Video duration: {total_s:.0f}s, {n} beats.

Beats (index: [tone] "narration" — starts at Xs, lasts Ys):
{lines}"""


def judge_highlights(beats: list[dict],
                     frames: list[str | None] | None = None) -> list[dict]:
    """beats: beat dicts (start_s, end_s, text, tone) in timeline order.
    frames: optional per-beat data-URL frame from the finished edit (None
    where extraction failed). Returns raw clip proposals with beat indices;
    the caller snaps/validates. Raises BrainError on failure."""
    lines = "\n".join(
        f'{i}: [{b.get("tone", "")}] "{b.get("text", "")}" — starts at '
        f'{b["start_s"]:.1f}s, lasts {b["end_s"] - b["start_s"]:.1f}s'
        for i, b in enumerate(beats))
    images = [(f"Frame for beat {i}:", url)
              for i, url in enumerate(frames or []) if url]
    total_s = beats[-1]["end_s"] if beats else 0.0
    out = structured(
        HIGHLIGHT_SYSTEM,
        HIGHLIGHT_USER.format(total_s=total_s, n=len(beats), lines=lines),
        HIGHLIGHT_SCHEMA, schema_name="highlight_scout", temperature=0.3,
        max_tokens=2500, images=images or None)
    return out.get("clips", [])


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


def judge_frames_detailed(beat_text: str, intent: str, video_title: str,
                          frames: list[str], *, detail: str = "high",
                          ) -> list[dict]:
    """frames: list of data-URL images, one per candidate window (in order;
    normally a three-frame tile from moments.frames.sample_window_tiles).
    detail: vision detail — "high" for the verification that gates
    escalation, "low" for the coarse whole-video scan.

    Returns one record per window, in window order:
      score   0..1 after enforcement (hard-fail flags → 0.0; soft-fail
              flags cap it; scores capped so they never contradict the
              model's own forced ranking)
      raw_score  the model's unenforced score, for calibration reports
      flags   the hard-fail flags the model raised
      soft_flags / info  the capping flags and the report-only flags raised
      rank    0 = the window the model would cut to
      seen / reason / shows_named_subject  diagnostics
    Raises BrainError on failure."""
    images = [(f"Window {i}:", url) for i, url in enumerate(frames)]
    out = structured(
        FRAME_SYSTEM,
        FRAME_USER.format(beat_text=beat_text, intent=intent,
                          video_title=video_title, n=len(frames)),
        FRAME_JUDGE_SCHEMA, schema_name="frame_judge", temperature=0.1,
        max_tokens=3000, images=images, image_detail=detail)
    n = len(frames)
    recs = [{"index": i, "score": 0.0, "raw_score": 0.0, "flags": [],
             "soft_flags": [], "info": [], "rank": None, "seen": "",
             "reason": "", "shows_named_subject": False} for i in range(n)]
    for w in out.get("windows", []):
        i = w.get("index")
        if not isinstance(i, int) or not 0 <= i < n:
            continue
        raw = max(0.0, min(1.0, float(w.get("score", 0.0))))
        flags = [f for f in HARD_FAIL_FLAGS if w.get(f)]
        soft = [f for f in SOFT_FAIL_CAPS if w.get(f)]
        score = 0.0 if flags else raw
        for f in soft:
            score = min(score, SOFT_FAIL_CAPS[f])
        recs[i].update(
            raw_score=raw, score=score, flags=flags, soft_flags=soft,
            info=[f for f in INFO_FLAGS if w.get(f)],
            seen=str(w.get("seen", "")), reason=str(w.get("reason", "")),
            shows_named_subject=bool(w.get("shows_named_subject")))

    # Forced ranking: the model's stated order, de-duplicated, with any
    # window it forgot appended in score order. Then walk down it and cap
    # each usable window at the score of the usable window above it, so a
    # window the model ranked lower can never outscore one it ranked higher.
    # Hard-failed windows are already 0 and neither move nor lower the cap.
    order: list[int] = []
    for i in out.get("ranking", []):
        if isinstance(i, int) and 0 <= i < n and i not in order:
            order.append(i)
    for i in sorted(range(n), key=lambda j: -recs[j]["score"]):
        if i not in order:
            order.append(i)
    usable = [i for i in order if not recs[i]["flags"]]
    failed = [i for i in order if recs[i]["flags"]]
    cap = 1.0
    for i in usable:
        recs[i]["score"] = round(min(recs[i]["score"], cap), 3)
        cap = recs[i]["score"]
    for rank, i in enumerate(usable + failed):
        recs[i]["rank"] = rank
    return recs


def judge_frames(beat_text: str, intent: str, video_title: str,
                 frames: list[str], *, detail: str = "high") -> list[float]:
    """frames: list of data-URL images, one per candidate window (in order).
    Returns an enforced score 0..1 per window — see judge_frames_detailed.
    Raises BrainError on failure."""
    return [r["score"] for r in
            judge_frames_detailed(beat_text, intent, video_title, frames,
                                  detail=detail)]


def judge_candidates(beat_text: str, intent: str, queries: list[str],
                     candidates: list[dict],
                     franchise_counts: dict[str, int] | None = None,
                     ) -> list[tuple[int, float, str]]:
    """Returns [(candidate_index, relevance, franchise)] best-first, only
    relevance >= 0.5. Raises BrainError if the LLM is unavailable/fails
    (caller falls back).

    Candidates may include a 'thumbnail' URL — shown to the model so it judges
    what the footage LOOKS like, not just what the title claims."""
    results = "\n".join(
        f"{i}: {c['title']!r} | channel: {c.get('channel','?')} | "
        f"{int(c.get('duration_s') or 0)}s | {c.get('views', 0)} views"
        for i, c in enumerate(candidates))
    images = [(f"Thumbnail for result {i}:", c["thumbnail"])
              for i, c in enumerate(candidates)
              if c.get("thumbnail", "").startswith("http")]
    franchises = ", ".join(f"{k} ×{v}" for k, v in (franchise_counts or {}).items()
                           if v) or "(none yet)"
    out = structured(
        JUDGE_SYSTEM,
        JUDGE_USER.format(beat_text=beat_text, intent=intent,
                          queries=", ".join(queries), results=results,
                          franchises=franchises),
        JUDGE_SCHEMA, schema_name="source_judge", temperature=0.2,
        max_tokens=2000, images=images or None)
    picks = [(p["index"], float(p["relevance"]), (p.get("franchise") or "").strip())
             for p in out.get("picks", [])
             if 0 <= p["index"] < len(candidates) and p["relevance"] >= 0.5]
    picks.sort(key=lambda t: t[1], reverse=True)
    return picks
