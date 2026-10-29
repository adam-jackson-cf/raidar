"""The one pricing table: LiteLLM's, for every harness's spend.

Harbor prices codex from ``litellm.cost_per_token``. A harness's self-reported cost (claude's
``total_cost_usd``) is another table's figure, so no harness cost is read from the harness: each
turn's tokens are priced here, per model, from the same table. A model the table does not know, or
token counts a record does not give, make the cost ``None`` (unavailable), never a mix of sources
or zero.
"""

from __future__ import annotations

from typing import Any


def price_tokens(
    model: str, *, prompt: int, completion: int, cache_read: int, cache_write: int
) -> float | None:
    """USD for one model's tokens (``prompt`` includes both cache counts), or None if unpriced."""
    try:
        import litellm

        kwargs: dict[str, Any] = {
            "model": model,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
        }
        prompt_cost, completion_cost = litellm.cost_per_token(**kwargs)
    except Exception:  # noqa: BLE001 - an unknown model or an absent table is "not priced"
        return None
    return float(prompt_cost + completion_cost)
