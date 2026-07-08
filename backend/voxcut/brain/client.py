"""LLM brain — OpenAI chat completions with JSON-schema structured output.

Reads the key + model from the Settings table (never env files). Exposes a
`structured()` call that guarantees schema-valid JSON, with one repair retry.
`is_available()` lets pipeline steps fall back to deterministic heuristics when
no key is configured.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from sqlmodel import select

from ..db import session_scope
from ..models import Setting

DEFAULT_MODEL = "gpt-4o"


@dataclass
class BrainConfig:
    api_key: str | None
    model: str


def load_config() -> BrainConfig:
    with session_scope() as db:
        rows = {r.key: r.value for r in db.exec(select(Setting)).all()}
    return BrainConfig(
        api_key=rows.get("openai_api_key") or None,
        model=rows.get("openai_model") or DEFAULT_MODEL,
    )


def is_available() -> bool:
    return load_config().api_key is not None


class BrainError(RuntimeError):
    pass


def structured(system: str, user: str, schema: dict, *,
               schema_name: str = "response", temperature: float = 0.4,
               max_tokens: int = 4096) -> dict:
    """Call the model and return schema-valid JSON (dict). Raises BrainError."""
    cfg = load_config()
    if not cfg.api_key:
        raise BrainError("No OpenAI API key configured (Settings → openai_api_key).")

    from openai import OpenAI
    client = OpenAI(api_key=cfg.api_key)

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": True},
    }
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    last_err = ""
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=cfg.model,
                messages=messages,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            # On a strict-schema failure, retry once with an explicit nudge.
            messages.append({"role": "user",
                             "content": "Your previous reply was invalid. Return "
                                        "ONLY JSON matching the schema exactly."})
    raise BrainError(f"LLM call failed after retry: {last_err}")
