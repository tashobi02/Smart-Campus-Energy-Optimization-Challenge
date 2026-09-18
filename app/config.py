from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
PORT: int = int(os.getenv("PORT", "8000"))
