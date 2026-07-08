"""Prompt templates for the brain (spec §6.2, §7.2)."""
from __future__ import annotations

SEGMENTATION_SYSTEM = """\
You segment a spoken commentary voiceover into "beats" for a fast-cut YouTube \
commentary edit. A beat is the smallest span that deserves its OWN visual.

Rules:
1. Split when ANY of these occur: a new concrete noun/entity/reference is named; \
a punchline or exaggeration lands; the topic/subject pivots; a rhetorical \
question is posed; a list item begins; the narrator's stance/emotion shifts.
2. NEVER split inside a grammatical unit that must be heard together (e.g., \
between an adjective and its noun, mid-idiom, mid-name).
3. Beats are typically {min_words}-{max_words} words for the requested density. \
A huge laugh-line may be shorter; connective tissue may be longer.
4. Mark emphasis 0..1: 1.0 = the punchline/peak of a joke, 0.2 = setup/filler.
5. concrete_entities: only things that could literally be shown on screen.
6. visual_affinity: "literal" (show the named thing), "reactive" (show a \
reaction to what's said), "abstract" (no obvious literal visual).

Input words are numbered. Return beats as {{start_word, end_word (inclusive), \
gist, tone, emphasis, concrete_entities, visual_affinity}}. Every word must be \
covered exactly once, in order. Do not skip or reuse word indices."""

SEGMENTATION_USER = """\
Context about the video: {context}

{prev_context}Words (index:token):
{words}"""


def segmentation_prompts(min_words: int, max_words: int, context: str,
                         words_block: str, prev_context: str = "") -> tuple[str, str]:
    system = SEGMENTATION_SYSTEM.format(min_words=min_words, max_words=max_words)
    user = SEGMENTATION_USER.format(
        context=context or "(none provided)",
        prev_context=(f"Previous beats already decided (do not re-segment these, "
                      f"continue after them):\n{prev_context}\n\n" if prev_context else ""),
        words=words_block,
    )
    return system, user
