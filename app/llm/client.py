"""LLM transport (PLAND2 3.5, 4.5, 4.6, 5.1).

Callers use complete() and never look behind it. Provider, retries, model
fallback, timeouts and caching all live here.
"""
from __future__ import annotations

import asyncio
import logging
import random

import httpx

from app.config import (
    LLM_API_KEY,
    LLM_FALLBACK_MODEL,
    LLM_MODEL,
    LLM_PROVIDER,
    LLM_RETRY_JITTER_SECONDS,
    LLM_STUB_MODE,
    LLM_TIMEOUT_SECONDS,
)
from app.llm import cache

logger = logging.getLogger(__name__)

# Both speak the OpenAI chat-completions shape, so one _post covers them.
_PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


class LLMUnavailable(Exception):
    """Raised when retries and fallback models are exhausted.

    Callers treat this as "no model answer this time" and fall back to their
    own deterministic path. The message is deliberately thin: it must never
    carry the provider URL, the prompt or the Authorization header, all of
    which httpx errors are happy to include (rubric: secret safety).
    """


# Canned payload for the stub transport. The record count is FIXED — it does
# not track the number of operator notes in the prompt. Reconciling the count
# against the notes is the guardrail's job (PLAND1 2.1, repair-don't-raise).
_CANNED = """[
  {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
   "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
   "explanation": "Canned stub directive."},
  {"note_index": 1, "applies": true, "directive_type": "no_charge_window",
   "structured_adjustment": {"hours": [2, 3, 4]},
   "explanation": "Canned stub directive."},
  {"note_index": 2, "applies": false, "directive_type": "no_op",
   "structured_adjustment": null,
   "explanation": "Canned stub distractor."}
]"""

# Deliberately broken payload: code fence, leading prose, an unknown enum
# value, an out-of-range hour, an unsorted duplicate hour list, a factor above
# 1, and a no_op carrying an adjustment. Exercises every guardrail repair path
# without needing a live provider to misbehave.
_MALFORMED = """Sure! Here is the interpretation you asked for:

```json
[
  {"note_index": 0, "applies": true, "directive_type": "solar_dimming",
   "structured_adjustment": {"hours": [13, 12, 12, 25], "factor": 1.4},
   "explanation": "Malformed stub directive."},
  {"note_index": 1, "applies": false, "directive_type": "no_op",
   "structured_adjustment": {"hours": [5], "max_grid_kwh": -10},
   "explanation": "Malformed stub no_op carrying an adjustment."}
]
```

Let me know if you need anything else!"""

# One client for the process (PLAND2 5.1). The old per-call AsyncClient paid a
# TCP and TLS handshake on every request. Built lazily so importing this
# module stays network-free and /health comes up inside its 60s window (5.3).
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS)
    return _client


async def aclose() -> None:
    """Close the shared client. For app shutdown and for tests."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def complete(
    system: str,
    user: str,
    *,
    json_schema: dict | None = None,
) -> str:
    """Return the raw assistant content for a chat completion.

    Raises LLMUnavailable once retries and fallback models are exhausted.

    Failure ladder (PLAND2 4.5), inside the timeout budget of 4.6:
      primary model -> retry once with jitter -> secondary model -> raise.
    The rungs below this one — a deterministic parser, then all no_op with a
    valid unconstrained schedule — belong to the caller, because only the
    caller knows how many notes it asked about.
    """
    stub = _stub_response()
    if stub is not None:
        return stub

    if LLM_PROVIDER not in _PROVIDER_URLS:
        raise LLMUnavailable(
            f"unknown LLM_PROVIDER {LLM_PROVIDER!r}; "
            f"expected one of {sorted(_PROVIDER_URLS)}"
        )

    key = cache.make_key("complete", LLM_MODEL, system, user, json_schema)
    cached = cache.get(key)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for model, delay in _attempts():
        if delay:
            await asyncio.sleep(delay)
        try:
            content = await _post(model, system, user, json_schema)
        except Exception as exc:
            last_error = exc
            # Type and model only. Never the response body, the URL or the
            # prompt — any of them can carry the key or the scenario.
            logger.warning(
                "model call failed: model=%s error=%s",
                model,
                type(exc).__name__,
            )
            continue
        cache.put(key, content)
        return content

    raise LLMUnavailable(
        f"all model attempts failed ({type(last_error).__name__})"
    ) from last_error


def _stub_response() -> str | None:
    """The canned transport, or None when the live provider should be used."""
    if LLM_STUB_MODE == "malformed":
        return _MALFORMED
    if LLM_STUB_MODE == "canned":
        return _CANNED
    if LLM_STUB_MODE == "auto" and not LLM_API_KEY:
        return _CANNED
    return None


def _attempts() -> list[tuple[str, float]]:
    """(model, delay-before-this-attempt) in ladder order."""
    ladder = [
        (LLM_MODEL, 0.0),
        (LLM_MODEL, random.uniform(0.0, LLM_RETRY_JITTER_SECONDS)),
    ]
    if LLM_FALLBACK_MODEL and LLM_FALLBACK_MODEL != LLM_MODEL:
        ladder.append((LLM_FALLBACK_MODEL, 0.0))
    return ladder


async def _post(
    model: str,
    system: str,
    user: str,
    json_schema: dict | None,
) -> str:
    url = _PROVIDER_URLS[LLM_PROVIDER]
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": _with_schema_instruction(system, json_schema)},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
    }
    if json_schema is not None:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "directive_interpretation",
                "strict": True,
                "schema": json_schema,
            },
        }

    resp = await _get_client().post(url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _with_schema_instruction(system: str, json_schema: dict | None) -> str:
    """Belt and braces for structured output (PLAND2 3.5).

    response_format is sent whenever a schema is given, but providers vary in
    whether they honour it, and the failure is silent. Restating the contract
    in the system prompt costs a few tokens and covers the case where it is
    ignored. D1's parser is defensive either way.
    """
    if json_schema is None:
        return system
    return (
        f"{system}\n\n"
        "Respond with a single JSON value and nothing else. No prose, no "
        "markdown code fences, no trailing commentary."
    )


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Backwards-compatible single-shot call against the configured model.

    Kept for the Phase 0 seam. New code should use complete(), which adds the
    cache, the retry ladder and the model fallback.
    """
    return await _post(LLM_MODEL, system_prompt, user_prompt, None)
