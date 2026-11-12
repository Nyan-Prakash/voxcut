"""JSON schemas for structured LLM output (strict mode)."""
from __future__ import annotations

SEGMENTATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start_word": {"type": "integer"},
                    "end_word": {"type": "integer"},
                    "gist": {"type": "string"},
                    "tone": {"type": "string",
                             "enum": ["deadpan", "hype", "sarcastic", "absurd",
                                      "neutral", "serious"]},
                    "emphasis": {"type": "number"},
                    "concrete_entities": {"type": "array", "items": {"type": "string"}},
                    "visual_affinity": {"type": "string",
                                        "enum": ["literal", "reactive", "abstract"]},
                    "rhythm": {"type": "string",
                               "enum": ["list_item", "escalation", "flow"]},
                },
                "required": ["start_word", "end_word", "gist", "tone", "emphasis",
                             "concrete_entities", "visual_affinity", "rhythm"],
            },
        }
    },
    "required": ["beats"],
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "beat_id": {"type": "string"},
                    "mode": {"type": "string",
                             "enum": ["literal", "absurd", "reaction",
                                      "exaggerated", "deadpan"],
                             "description": "the playbook relation mode"},
                    "gag": {"type": "string",
                            "description": "written BEFORE the queries: one "
                                           "line saying what the viewer SEES "
                                           "and why it lands under these "
                                           "exact words"},
                    "source": {"type": "string",
                               "description": "the NAMED origin of this footage "
                                              "(show, film, anime, game, "
                                              "creator, event, known viral "
                                              "clip) or 'stock'"},
                    "kind": {"type": "string",
                             "enum": ["clip_literal", "clip_reaction",
                                      "meme_image", "broll"]},
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "joke_gag": {"type": "string",
                                 "description": "one line for the OTHER "
                                                "angle: different footage "
                                                "from a different source "
                                                "than gag"},
                    "joke_source": {"type": "string",
                               "description": "the NAMED origin of the other angle's footage "
                                              "(show, film, anime, game, "
                                              "creator, event, known viral "
                                              "clip) or 'stock'"},
                    "joke_queries": {
                        "type": "array", "items": {"type": "string"},
                        "description": "queries for the OTHER comedic angle "
                                       "(tournament candidate)"},
                    "audio_mode": {"type": "string",
                                   "enum": ["mute", "duck", "keep"]},
                    "punch_word": {"type": "string",
                                   "description": "the ONE word, copied "
                                                  "exactly from this beat's "
                                                  "narration, the visual "
                                                  "should land on; '' when "
                                                  "the visual just "
                                                  "illustrates the whole "
                                                  "line"},
                    "visual_need": {"type": "string",
                                    "enum": ["high", "medium", "low"],
                                    "description": "how much this line "
                                                   "needs footage rather "
                                                   "than the speaker on "
                                                   "camera"},
                },
                "required": ["beat_id", "mode", "gag", "source", "kind",
                             "queries", "joke_gag", "joke_source",
                             "joke_queries", "audio_mode", "punch_word",
                             "visual_need"],
            },
        }
    },
    "required": ["items"],
}


# A single beat re-split (validation repair for over-long beats).
SPLIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "boundaries": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "word indices where new beats START (excluding the first)",
        }
    },
    "required": ["boundaries"],
}
