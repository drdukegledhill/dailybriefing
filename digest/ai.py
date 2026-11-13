"""The editing step: send the day's request to Claude and get the briefing back as JSON.

Default model is Claude Sonnet 5.5 (about 5p a day). Set CLAUDE_MODEL in .env to change it,
e.g. claude-haiku-5-5 (under 1p a day).
"""

from __future__ import annotations

import json
import logging

import anthropic

from .config import Settings
from .editorial import SCHEMA

log = logging.getLogger(__name__)

# Rough prices in US dollars per million tokens (input, output), for the cost line in the log.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

# Models that support the API's automatic fallback when a safety classifier declines.
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5"}


def edit_with_claude(request: str, settings: Settings, model: str | None = None) -> dict:
    """Return the briefing dict. Raises on any failure so the caller can fall back to a headline list."""
    model = model or settings.model
    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY (loaded from .env)

    params = dict(
        model=model,
        max_tokens=32000,
        messages=[{"role": "user", "content": request}],
        thinking={"type": "adaptive"},
        output_config={
            "effort": settings.effort,
            "format": {"type": "json_schema", "schema": SCHEMA},
        },
    )
    if model in FALLBACK_MODELS:
        # If a safety classifier declines (news about AI can trip one), the API retries
        # on a suitable fallback model within the same call. Haiku has no server-side fallback.
        with client.beta.messages.stream(
            **params, betas=["server-side-fallback-2026-07-01"], fallbacks="default"
        ) as stream:
            message = stream.get_final_message()
        if message.model != model:
            log.info("%s declined; %s wrote the briefing instead", model, message.model)
    else:
        with client.messages.stream(**params) as stream:
            message = stream.get_final_message()

    usage = message.usage
    price_in, price_out = PRICES.get(model, (0, 0))
    cost = (usage.input_tokens * price_in + usage.output_tokens * price_out) / 1e6
    log.info("%s: %d tokens in, %d out (about $%.4f)", model, usage.input_tokens, usage.output_tokens, cost)

    if message.stop_reason == "refusal":
        raise RuntimeError(f"{model} declined to write the briefing")
    if message.stop_reason == "max_tokens":
        raise RuntimeError(f"{model} ran out of output tokens")

    text = "".join(b.text for b in message.content if b.type == "text")
    return json.loads(text)
