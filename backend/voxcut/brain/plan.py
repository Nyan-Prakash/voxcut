"""Edit planner — the edit brain (spec §7).

Per scene-group of beats, decides what's on screen: clip vs caption card vs
b-roll, with search queries for sourcing (used in M4+). Produces EDL events
(timing carried from beats). Heuristic fallback writes a caption card per beat.
"""
from __future__ import annotations

from ulid import ULID

from .client import BrainError, is_available, structured
from .schemas import PLAN_SCHEMA

PLAN_SYSTEM = """\
You are the edit brain for a fast-cut no-face commentary video. For each beat, \
decide what's on screen. Visual types:
- clip_literal: footage of the named thing (needs: search queries)
- clip_reaction: a reaction moment matching the tone (needs: reaction queries)
- meme_image: a well-known meme/image (needs: image queries)
- caption_card: full-screen styled text (write the text; <=7 words)
- broll: generic thematic footage (needs: queries)

Rules:
0. Clips play MUTED under the narrator's voice, so every clip must work as a
SILENT visual: physical action, exaggerated facial reactions, freakouts,
fails, memes, animated moments. NEVER plan footage of people just talking
(interviews, podcasts, news anchors, explainers) — muted talking looks broken.
When you want a meme or reaction, NAME THE EXACT well-known clip — a query a
YouTube search can resolve to one canonical video ("Pinocchio nose growing
scene", "Michael Scott no god please no", "confused math lady", "side eye Chloe")
— NEVER a generic pattern like "<emotion> meme" or "<situation> meme"; generic
meme queries return random low-quality uploads. The quoted names above are
FORMAT examples only — do NOT default to them; draw from the full breadth of
internet culture and pick whatever canonical clip genuinely fits THIS beat.
Never use the same meme twice in one video. Stay anchored to the video's
actual subject and story: at least one query per beat should reference its
concrete entities or the video subject, so the edit doesn't drift into
unrelated stock memes.
1. COMEDY PLAYBOOK — pick ONE relation mode per beat (measured mix from the \
reference editor in parentheses; roughly match it across the video):
   a. LITERAL (~30%): plain footage of the thing being said. Use for setup \
beats that ground the story ("fire drill" → hand pulling a fire alarm).
   b. ABSURD NON-SEQUITUR (~20%): for connective/filler beats, a clearly \
unrelated but tonally-matched ridiculous clip (a lizard wearing clothes, an \
owl on a couch) beats a strained literal match. It must be OBVIOUSLY a joke — \
commit to the bit. Query the funny thing itself, not the beat's topic.
   c. REACTION (~20%): a person/character visibly reacting the way the \
narrator feels. Use for opinions and judgments.
   d. EXAGGERATED-LITERAL (~15%): the most over-the-top available version of \
the thing said ("had to run" → speedster with lightning effects). Reserve the \
craziest versions for punchlines.
   e. IRONIC DEADPAN (~15%): for dark or serious topics, comic understatement \
(lockdown drill → toy dart gun). The mismatch is the joke.
NEVER the mediocre middle: a clip that is only somewhat related and not funny \
is the worst outcome — go clearly literal or clearly joke, never in between.
2. ENERGY MATCHING: match visual energy to the beat's emphasis. Setup beats \
(emphasis <0.5) want calm/medium footage; punchlines (>=0.7) want chaotic \
high-energy visuals (crashes, explosions, freakouts, sprinting). Do not put \
chaos under setup lines or calm stock under a punchline.
3. FLAVOR MIX (measured): movie/TV scenes + viral clips are the backbone \
(~half the video), stock footage/photos ~20% (deliberately cheap or \
watermarked stock allowed as an ironic gag), anime + cartoons ~15% as \
seasoning. Named memes are a spice, not the meal — most beats want real \
footage matched to the words, not a meme. Never 3 consecutive beats of the \
same flavor; never 3 consecutive reactions. Punctuate runs of clips with a \
caption_card roughly every 6-10 beats.
4. Write 2-3 search queries per sourcing beat, ordered best-first. Queries must \
be what a human would type into YouTube to find EXACTLY this footage — include \
names, events, "meme", "scene", "interview", "moment" as appropriate. Be \
SPECIFIC: never a lone generic word ("school", "phone"); anchor every query to \
the concrete entity, the named person/show/event, or the video's subject — \
EXCEPT for absurd non-sequitur beats, where the query names the funny footage \
itself ("iguana in a dress mirror"). If the beat names nothing concrete and no \
non-sequitur fits, choose caption_card instead of a vague query.
5. Respect the avoid-list: {avoid}.
6. Captions are OFF by default — clips play clean, no subtitles. Write one \
only when a JOKE or LABEL genuinely adds a gag (at most ~1 in 4 beats), and \
never use the "subtitle" style over a clip; use meme_top/meme_bottom/label.
7. Source audio: mute by default; "keep" only when the source's own audio IS \
the joke.

Return one plan item per beat, in order, referencing beat_id."""

PLAN_USER = """\
Context: {context}
Avoid: {avoid}

Beats:
{beats_block}"""


def _default_treatment() -> dict:
    return {"layout": "fullscreen", "zoom": {"start": 1.0, "end": 1.06},
            "transition_in": "cut", "fit": "cover"}


def _event(beat: dict, kind: str, caption_text: str = "", caption_enabled: bool = False,
           caption_style: str = "meme_top", queries: list[str] | None = None,
           audio_mode: str = "mute") -> dict:
    flags = ["auto"]
    if kind != "caption_card" and not queries:
        flags.append("gap_unfilled")
    return {
        "id": f"ev_{ULID()}",
        "beat_id": beat["id"],
        "start_s": beat["start_s"],
        "end_s": beat["end_s"],
        "kind": kind,
        "asset_id": None,
        "source": None,
        "queries": queries or [],
        "treatment": _default_treatment(),
        "caption": {"text": caption_text, "style": caption_style,
                    "enabled": caption_enabled},
        "audio": {"mode": audio_mode, "duck_db": -18},
        "flags": flags,
        "locked": False,
    }


def plan(beats: list[dict], brief: dict, aspect: str = "16:9",
         use_llm: bool | None = None) -> dict:
    if use_llm is None:
        use_llm = is_available()
    events: list[dict]
    if use_llm and beats:
        try:
            events = _llm_plan(beats, brief)
        except BrainError:
            events = _heuristic_plan(beats)
    else:
        events = _heuristic_plan(beats)
    return {"version": 1, "aspect": aspect, "events": events}


def _llm_plan(beats: list[dict], brief: dict) -> list[dict]:
    from .steps_helpers import brief_summary  # lazy to avoid cycle
    avoid = ", ".join(brief.get("avoid", [])) or "(none)"
    context = brief_summary(brief)
    KINDS = {"clip_literal", "clip_reaction", "meme_image", "caption_card", "broll"}

    beats_block = "\n".join(
        f"{b['id']} | emph={b['emphasis']} | affinity={b['visual_affinity']} | "
        f"entities={b.get('concrete_entities')} | {b['text']}" for b in beats)
    system = PLAN_SYSTEM.format(avoid=avoid)
    user = PLAN_USER.format(context=context, avoid=avoid, beats_block=beats_block)
    out = structured(system, user, PLAN_SCHEMA, schema_name="edit_plan",
                     temperature=0.5, max_tokens=6000)

    by_id = {b["id"]: b for b in beats}
    items = {it["beat_id"]: it for it in out.get("items", [])}
    events: list[dict] = []
    for b in beats:
        it = items.get(b["id"])
        if not it:
            events.append(_event(b, "caption_card", b["gist"][:60], True))
            continue
        kind = it["kind"] if it["kind"] in KINDS else "caption_card"
        cap = it.get("caption") or {}
        style = cap.get("style", "meme_top")
        # Captions-off default: no subtitle-style text burned over clips.
        if kind != "caption_card" and style == "subtitle":
            style = "meme_bottom"
        events.append(_event(
            b, kind,
            caption_text=cap.get("text", ""),
            caption_enabled=bool(cap.get("enabled")) or kind == "caption_card",
            caption_style=style,
            queries=it.get("queries", []),
            audio_mode=it.get("audio_mode", "mute"),
        ))
    return events


def _heuristic_plan(beats: list[dict]) -> list[dict]:
    """No LLM: every beat becomes a caption card of its own text (a lyric-video
    style baseline). Proves the render pipeline end-to-end."""
    events = []
    for b in beats:
        text = (b.get("gist") or b["text"])[:80]
        events.append(_event(b, "caption_card", caption_text=text,
                             caption_enabled=True, caption_style="subtitle"))
    return events
