from __future__ import annotations
import httpx
from app.config import LLM_API_KEY, LLM_MODEL, LLM_PROVIDER

_PROVIDER_URLS = {
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}


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
