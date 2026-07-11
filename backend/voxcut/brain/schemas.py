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
                    "kind": {"type": "string",
                             "enum": ["clip_literal", "clip_reaction",
                                      "meme_image", "caption_card", "broll"]},
                    "queries": {"type": "array", "items": {"type": "string"}},
                    "audio_mode": {"type": "string",
                                   "enum": ["mute", "duck", "keep"]},
                    "caption": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "style": {"type": "string",
                                      "enum": ["meme_top", "meme_bottom",
                                               "subtitle", "label"]},
                            "enabled": {"type": "boolean"},
                        },
                        "required": ["text", "style", "enabled"],
                    },
                },
                "required": ["beat_id", "kind", "queries", "audio_mode", "caption"],
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
