"""Interpretation cache (PLAND2 5.2).

The judge issues repeated hidden cases and near-identical notes are likely,
so a hit here removes the single slowest thing in the request path.

**The key is the whole model input, not just the note text.** PLAND2 warns
that a key missing the battery parameters returns a SAMPLE-03-style stale
reserve for a different battery: "50% of the battery capacity" is 100 kWh on
a 200 kWh pack and 125 kWh on a 250 kWh one, from identical note text. Keying
on the full prompt makes that class of bug unreachable — whatever the model
was told is what the key covers, so two inputs share an entry only when the
model would genuinely have produced the same answer.

Used by app.llm.client at the transport seam. D1 can also use make_key()
directly for a per-note cache inside the interpreter; the API is the same.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

from app.config import LLM_CACHE_ENABLED, LLM_CACHE_MAX_ENTRIES

_entries: OrderedDict[str, Any] = OrderedDict()
_hits = 0
_misses = 0


def make_key(*parts: Any) -> str:
    """Hash any combination of inputs into a stable cache key.

    Values are serialised with sorted keys so two equal dicts built in a
    different order produce the same key.
    """
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get(key: str) -> Any | None:
    """Return the cached value, or None on a miss."""
    global _hits, _misses
    if not LLM_CACHE_ENABLED or key not in _entries:
        _misses += 1
        return None
    _entries.move_to_end(key)
    _hits += 1
    return _entries[key]


def put(key: str, value: Any) -> None:
    """Store a value, evicting the least recently used entry when full."""
    if not LLM_CACHE_ENABLED:
        return
    _entries[key] = value
    _entries.move_to_end(key)
    while len(_entries) > LLM_CACHE_MAX_ENTRIES:
        _entries.popitem(last=False)


def stats() -> dict[str, int]:
    return {"entries": len(_entries), "hits": _hits, "misses": _misses}


def clear() -> None:
    global _hits, _misses
    _entries.clear()
    _hits = _misses = 0
