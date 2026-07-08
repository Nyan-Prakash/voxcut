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
                },
                "required": ["start_word", "end_word", "gist", "tone", "emphasis",
                             "concrete_entities", "visual_affinity"],
            },
        }
    },
    "required": ["beats"],
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
