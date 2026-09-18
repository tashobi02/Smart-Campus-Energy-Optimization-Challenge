from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openrouter")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
LLM_MODEL: str = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
PORT: int = _int("PORT", 8000)

# Transport stub control — lets the team work without a provider or an API key.
#   "auto"      live provider when LLM_API_KEY is set, canned payload otherwise
#   "canned"    always return the canned well-formed payload
#   "malformed" always return a deliberately broken payload (guardrail testing)
#   "off"       always call the live provider; fail loudly if the key is missing
LLM_STUB_MODE: str = os.getenv("LLM_STUB_MODE", "auto")

# --- Timeout budget (PLAND2 4.6) -------------------------------------------
# The judge allows 30s per request. The old 60s client timeout meant a request
# was already a failure before the client gave up. Worst case now:
#   8s primary + 0.5s jitter + 8s retry + 8s secondary model = 24.5s,
# leaving headroom inside the 30s ceiling. Tune here, not in code.
LLM_TIMEOUT_SECONDS: float = _float("LLM_TIMEOUT_SECONDS", 8.0)
LLM_RETRY_JITTER_SECONDS: float = _float("LLM_RETRY_JITTER_SECONDS", 0.5)
LLM_FALLBACK_MODEL: str = os.getenv("LLM_FALLBACK_MODEL", "")

# Budget for the LP. The solver is D1's; this is the number they should
# enforce against, kept here so it is tunable without a code edit.
SOLVER_TIMEOUT_SECONDS: float = _float("SOLVER_TIMEOUT_SECONDS", 1.0)

# --- Cache (PLAND2 5.2) ----------------------------------------------------
LLM_CACHE_ENABLED: bool = os.getenv("LLM_CACHE_ENABLED", "1") != "0"
LLM_CACHE_MAX_ENTRIES: int = _int("LLM_CACHE_MAX_ENTRIES", 512)
