from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
PORT: int = int(os.getenv("PORT", "8000"))

# Transport stub control — lets the team work without a provider or an API key.
#   "auto"      live provider when LLM_API_KEY is set, canned payload otherwise
#   "canned"    always return the canned well-formed payload
#   "malformed" always return a deliberately broken payload (guardrail testing)
#   "off"       always call the live provider; fail loudly if the key is missing
LLM_STUB_MODE: str = os.getenv("LLM_STUB_MODE", "auto")
