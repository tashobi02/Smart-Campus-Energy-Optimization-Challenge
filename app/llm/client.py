from __future__ import annotations
import httpx
from app.config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER, LLM_STUB_MODE

_PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


class LLMUnavailable(Exception):
    """Raised when retries and fallback models are exhausted.

    Callers treat this as "no model answer this time" and fall back to their
    own deterministic path. They never see the provider, the retry count or
    the failure detail.
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


async def complete(
    system: str,
    user: str,
    *,
    json_schema: dict | None = None,
) -> str:
    """Return the raw assistant content for a chat completion.

    Raises LLMUnavailable once retries and fallback models are exhausted.
    Everything behind this — provider, retries, timeouts, model fallback,
    caching — is invisible to callers.

    T+0:00 transport stub. Retries, model fallback and the timeout budget
    land in Phases 4.5/4.6; structured output (json_schema) in Phase 3.5.
    """
    if LLM_STUB_MODE == "malformed":
        return _MALFORMED
    if LLM_STUB_MODE == "canned":
        return _CANNED
    if LLM_STUB_MODE == "auto" and not LLM_API_KEY:
        return _CANNED

    try:
        return await call_llm(system, user)
    except Exception as exc:
        # Detail stays server-side; the message must never carry the provider
        # URL, the prompt or the Authorization header (rubric: secret safety).
        raise LLMUnavailable(
            f"model call failed: {type(exc).__name__}"
        ) from exc


async def call_llm(system_prompt: str, user_prompt: str) -> str:
    """Send a chat-completion request and return the assistant message content.

    Phase 0: this function is not called yet; interpreter returns a stub.
    """
    url = _PROVIDER_URLS.get(LLM_PROVIDER)
    if url is None:
        raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
