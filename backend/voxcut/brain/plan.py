"""Edit planner — the edit brain (spec §7).

Per scene-group of beats, decides what's on screen: which footage to hunt for,
with search queries for sourcing (used in M4+). Produces EDL events (timing
carried from beats). No captions: every beat is footage; unsourceable beats
become gaps absorbed by neighboring clips at render time.
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
2b. QUICK-CUT BEATS: beats tagged rhythm=list_item are rapid-fire one-item \
beats — each wants an INSTANTLY-READABLE shot of exactly that item, and the \
funny version beats the plain one when it exists ("popcorn" → popcorn machine \
exploding > popcorn close-up). Put the funny version in queries and the plain \
item in joke_queries as the fallback. Plain stock is an acceptable outcome; \
confusing or slow-to-read footage is not. rhythm=escalation beats want visuals \
whose energy rises with each step.
2c. TOURNAMENT — every beat provides TWO comedic angles and the pipeline \
tests both with real footage, keeping whichever verifies funnier: \
"queries" = your primary angle; "joke_queries" = the OTHER angle. If the \
primary is literal/exaggerated-literal, joke_queries names a reaction or \
canonical meme matching the emotion; if the primary is a meme/reaction, \
joke_queries goes literal. Both angles must obey rule 4's specificity.
2d. FAMOUS + FRESH: mix recognizable iconic scenes (recognition lands the \
joke) with fresh, lesser-known finds (surprise lands the joke) — a video of \
only worn-out memes is as weak as a video of only obscure clips.
3. FLAVOR MIX (measured): movie/TV scenes + viral clips are the backbone \
(~half the video), stock footage/photos ~20% (deliberately cheap or \
watermarked stock allowed as an ironic gag), anime + cartoons ~15% as \
seasoning. Named memes are a spice, not the meal — most beats want real \
footage matched to the words, not a meme. Never 3 consecutive beats of the \
same flavor; never 3 consecutive reactions.
4. Write 2-3 search queries per beat, ordered best-first. Queries must \
be what a human would type into YouTube to find EXACTLY this footage — include \
names, events, "meme", "scene", "interview", "moment" as appropriate. Be \
SPECIFIC: never a lone generic word ("school", "phone"); anchor every query to \
the concrete entity, the named person/show/event, or the video's subject — \
EXCEPT for absurd non-sequitur beats, where the query names the funny footage \
itself ("iguana in a dress mirror"). If the beat names nothing concrete, an \
absurd non-sequitur or a tonally-matched reaction is ALWAYS available — every \
beat gets real queries.
5. Respect the avoid-list: {avoid}.
6. Source audio: mute by default; "keep" only when the source's own audio IS \
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


def _event(beat: dict, kind: str, queries: list[str] | None = None,
           audio_mode: str = "mute", joke_queries: list[str] | None = None) -> dict:
    flags = ["auto"]
    if not queries:
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
        "joke_queries": joke_queries or [],
        "treatment": _default_treatment(),
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


KINDS = {"clip_literal", "clip_reaction", "meme_image", "broll"}


def _llm_plan(beats: list[dict], brief: dict) -> list[dict]:
    from .steps_helpers import brief_summary  # lazy to avoid cycle
    avoid = ", ".join(brief.get("avoid", [])) or "(none)"
    context = brief_summary(brief)

    beats_block = "\n".join(
        f"{b['id']} | emph={b['emphasis']} | rhythm={b.get('rhythm', 'flow')} | "
        f"affinity={b['visual_affinity']} | "
        f"entities={b.get('concrete_entities')} | {b['text']}" for b in beats)
    system = PLAN_SYSTEM.format(avoid=avoid)
    user = PLAN_USER.format(context=context, avoid=avoid, beats_block=beats_block)
    out = structured(system, user, PLAN_SCHEMA, schema_name="edit_plan",
                     temperature=0.5, max_tokens=6000)

    items = {it["beat_id"]: it for it in out.get("items", [])}
    events: list[dict] = []
    for b in beats:
        it = items.get(b["id"])
        if not it:
            events.append(_heuristic_event(b))
            continue
        events.append(_item_to_event(b, it))
    return events


def _item_to_event(beat: dict, it: dict) -> dict:
    kind = it["kind"] if it["kind"] in KINDS else "broll"
    return _event(
        beat, kind,
        queries=it.get("queries", []),
        audio_mode=it.get("audio_mode", "mute"),
        joke_queries=it.get("joke_queries", []),
    )


def plan_one(beat: dict, brief: dict, avoid_extra: list[str] | None = None,
             hint: str | None = None) -> dict:
    """Re-plan a single beat (per-clip reroll). Returns a fresh event for the
    beat with new queries/kind. An optional operator hint steers the plan.
    Raises BrainError when the LLM is unavailable."""
    from .steps_helpers import brief_summary  # lazy to avoid cycle
    avoid = ", ".join((brief.get("avoid") or []) + (avoid_extra or [])) or "(none)"
    context = brief_summary(brief)
    beats_block = (
        f"{beat['id']} | emph={beat['emphasis']} | rhythm={beat.get('rhythm', 'flow')} | "
        f"affinity={beat.get('visual_affinity', 'literal')} | "
        f"entities={beat.get('concrete_entities')} | {beat.get('text', beat.get('gist', ''))}")
    user = PLAN_USER.format(context=context, avoid=avoid, beats_block=beats_block)
    if hint and hint.strip():
        user += (f"\n\nOPERATOR DIRECTION for this beat — follow it; it wins over "
                 f"every playbook rule above, but keep queries SPECIFIC and "
                 f"YouTube-searchable: {hint.strip()}")
    system = PLAN_SYSTEM.format(avoid=avoid)
    out = structured(system, user, PLAN_SCHEMA, schema_name="edit_plan",
                     temperature=0.8, max_tokens=1200)
    items = out.get("items") or []
    if not items:
        raise BrainError("planner returned no item for beat")
    return _item_to_event(beat, items[0])


def _heuristic_event(b: dict) -> dict:
    """No LLM: hunt broll for the beat's gist/entities. Weak queries beat
    burned text — captions are gone by design."""
    entities = [e for e in b.get("concrete_entities", []) if e]
    queries = []
    if entities:
        queries.append(" ".join(entities[:3]))
    gist = (b.get("gist") or b.get("text") or "").strip()
    if gist:
        queries.append(gist[:80])
    return _event(b, "broll", queries=queries)


def _heuristic_plan(beats: list[dict]) -> list[dict]:
    return [_heuristic_event(b) for b in beats]
