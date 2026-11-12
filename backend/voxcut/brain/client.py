"""LLM brain — OpenAI chat completions with JSON-schema structured output.

Reads the key + model from the Settings table (never env files). Exposes a
`structured()` call that guarantees schema-valid JSON, with one repair retry.
`is_available()` lets pipeline steps fall back to deterministic heuristics when
no key is configured.
"""
from __future__ import annotations

import json
import logging
import threading
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


IMAGE_DETAILS = ("low", "high", "auto")
REQUEST_TIMEOUT_S = 120.0


log = logging.getLogger(__name__)

# Spend meter. USD per 1M tokens (input, output) — list prices, an ESTIMATE:
# edit when OpenAI's pricing changes. Unknown models fall back to gpt-4o's.
PRICE_PER_M = {"gpt-4o": (2.50, 10.00), "gpt-4o-mini": (0.15, 0.60)}
_usage_lock = threading.Lock()
_usage: dict[str, dict] = {}


def _record_usage(tag: str, model: str, usage) -> None:  # noqa: ANN001
    if usage is None:
        return
    p_in, p_out = PRICE_PER_M.get(model, PRICE_PER_M["gpt-4o"])
    tin, tout = usage.prompt_tokens or 0, usage.completion_tokens or 0
    cost = (tin * p_in + tout * p_out) / 1e6
    with _usage_lock:
        row = _usage.setdefault(tag, {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
        row["calls"] += 1
        row["in"] += tin
        row["out"] += tout
        row["usd"] += cost
        total = sum(r["usd"] for r in _usage.values())
    log.info("[spend] %s in=%d out=%d ~$%.4f (running total ~$%.2f)",
             tag, tin, tout, cost, total)


def usage_summary(reset: bool = False) -> dict[str, dict]:
    """Estimated spend per call type since start (or the last reset)."""
    with _usage_lock:
        out = {k: dict(v) for k, v in _usage.items()}
        if reset:
            _usage.clear()
    return out


def structured(system: str, user: str, schema: dict, *,
               schema_name: str = "response", temperature: float = 0.4,
               max_tokens: int = 4096,
               images: list[tuple[str, str]] | None = None,
               image_detail: str = "low") -> dict:
    """Call the model and return schema-valid JSON (dict). Raises BrainError.

    images: optional [(label, url)] shown to the model after the text.
    image_detail: vision detail for every image in this call. "low" is a
    fixed 512px budget (~85 tokens/image) — right for thumbnails and the
    coarse whole-video scan. "high" sends full-resolution tiles (~1k
    tokens for a 640px three-frame tile) — right for final verification,
    where reading a title card, a logo or a face is the whole point.
    """
    if image_detail not in IMAGE_DETAILS:
        raise ValueError(f"image_detail must be one of {IMAGE_DETAILS}")
    cfg = load_config()
    if not cfg.api_key:
        raise BrainError("No OpenAI API key configured (Settings → openai_api_key).")

    from openai import OpenAI
    # A vision call with a dozen tiles finishes in well under a minute; a
    # socket that goes quiet must fail (the SDK retries) rather than hold
    # a pipeline worker forever — one hung frame-judge call stalled a
    # calibration run for a day before this was set.
    client = OpenAI(api_key=cfg.api_key, timeout=REQUEST_TIMEOUT_S)

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": schema_name, "schema": schema, "strict": True},
    }
    if images:
        content: list[dict] = [{"type": "text", "text": user}]
        for label, url in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image_url",
                            "image_url": {"url": url, "detail": image_detail}})
        user_msg: dict = {"role": "user", "content": content}
    else:
        user_msg = {"role": "user", "content": user}
    messages = [
        {"role": "system", "content": system},
        user_msg,
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
            _record_usage(schema_name, cfg.model, getattr(resp, "usage", None))
            content = resp.choices[0].message.content or "{}"
            return json.loads(content)
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            if images and any(url.startswith("http") for _l, url in images):
                # A dead thumbnail URL fails the whole call — retry text-only.
                messages = [{"role": "system", "content": system},
                            {"role": "user", "content": user}]
                images = None
            else:
                # Inline (data-URL) frames can't be the failure, and a frame
                # judge without its frames would score footage it cannot
                # see — keep the images and nudge the schema instead.
                # On a strict-schema failure, retry once with an explicit nudge.
                messages.append({"role": "user",
                                 "content": "Your previous reply was invalid. Return "
                                            "ONLY JSON matching the schema exactly."})
    raise BrainError(f"LLM call failed after retry: {last_err}")
