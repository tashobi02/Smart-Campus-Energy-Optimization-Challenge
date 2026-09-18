"""Operator-note interpretation: the LLM step, plus a deterministic backup.

The language model is the interpreter on the happy path. The regex parser at the
bottom runs ONLY when the provider is unreachable — phrase matching as the sole
interpreter is explicitly non-compliant with the challenge rules.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from app.llm.prompts import RESPONSE_JSON_SCHEMA, SYSTEM_PROMPT, build_user_prompt
from app.schemas.request import BatteryConfig

logger = logging.getLogger(__name__)

try:  # The frozen transport seam, owned by D2.
    from app.llm.client import LLMUnavailable, complete  # type: ignore
except ImportError:  # pragma: no cover - until D2 lands `complete`
    from app.llm.client import call_llm

    class LLMUnavailable(RuntimeError):
        """Raised when the model could not be reached or returned nothing."""

    async def complete(
        system: str, user: str, *, json_schema: Optional[Dict[str, Any]] = None
    ) -> str:
        try:
            return await call_llm(system, user)
        except Exception as exc:  # noqa: BLE001 - transport detail stays internal
            raise LLMUnavailable("language model request failed") from exc


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def _extract_json(text: str) -> Optional[Any]:
    """Pull a JSON value out of model output that may carry fences or prose."""
    if not text:
        return None

    candidates: List[str] = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(text.strip())

    # Last resort: the outermost brace or bracket span.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if 0 <= start < end:
            candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


def _as_entries(payload: Any) -> List[Any]:
    """Accept either the wrapped object or a bare list of entries."""
    if isinstance(payload, dict):
        for key in ("directive_interpretation", "directives", "interpretation"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return []
    if isinstance(payload, list):
        return payload
    return []


async def interpret_notes(
    operator_notes: Sequence[str],
    battery: BatteryConfig,
) -> List[Dict[str, Any]]:
    """Interpret every note in one model call.

    Returns raw, still-untrusted directive records. They are only legal once
    app.guardrails.validator has repaired them.
    """
    if not operator_notes:
        return []

    user_prompt = build_user_prompt(operator_notes, battery)
    try:
        raw = await complete(
            SYSTEM_PROMPT, user_prompt, json_schema=RESPONSE_JSON_SCHEMA
        )
    except LLMUnavailable:
        logger.warning("model unavailable; using the deterministic backup parser")
        return fallback_parse(operator_notes, battery)

    entries = _as_entries(_extract_json(raw))
    if not entries:
        logger.warning("model returned no usable JSON; using the backup parser")
        return fallback_parse(operator_notes, battery)
    return entries


# --------------------------------------------------------------------------
# Deterministic backup parser — outage path only, never the happy path.
# --------------------------------------------------------------------------

_WORD_HOURS = {
    "midnight": 0, "noon": 12, "midday": 12,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_FRACTIONS = {
    "half": 0.5, "one-half": 0.5, "a half": 0.5,
    "a third": 1 / 3, "one-third": 1 / 3,
    "a quarter": 0.25, "one-quarter": 0.25,
    "a fifth": 0.2, "one-fifth": 0.2,
}
_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?"
_RANGE = re.compile(
    rf"(?:from\s+|between\s+)?(?:{_CLOCK}|(midnight|noon|midday|one|two|three|four|five|"
    rf"six|seven|eight|nine|ten|eleven|twelve))\s*(?:to|until|till|through|and|[-–—])\s*"
    rf"(?:{_CLOCK}|(midnight|noon|midday|one|two|three|four|five|six|seven|eight|nine|"
    rf"ten|eleven|twelve))",
    re.IGNORECASE,
)


def _to_hour(digits: Optional[str], meridiem: Optional[str], word: Optional[str]) -> Optional[int]:
    if word:
        return _WORD_HOURS.get(word.lower())
    if digits is None:
        return None
    hour = int(digits)
    if meridiem:
        pm = meridiem.lower().startswith("p")
        hour = hour % 12 + (12 if pm else 0)
    return hour if 0 <= hour <= 23 else None


def _window(text: str) -> List[int]:
    """Hours for the first time range in the note, start-inclusive/end-exclusive."""
    match = _RANGE.search(text)
    if not match:
        return []
    start = _to_hour(match.group(1), match.group(3), match.group(4))
    end = _to_hour(match.group(5), match.group(7), match.group(8))
    if start is None or end is None:
        return []

    # "from one until three" in an afternoon note means 13-15, not 01-03.
    if not match.group(3) and not match.group(7) and re.search(r"\bpm\b|afternoon|evening", text, re.I):
        if start < 12:
            start += 12
        if end < 12:
            end += 12
    if end <= start:
        end += 12
    return [h for h in range(start, min(end, 24))]


def _factor(text: str) -> Optional[float]:
    percent = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", text, re.I)
    reducing = re.search(r"reduc|cut|drop\s+by|decreas|loss|down\s+by", text, re.I)
    if percent:
        value = float(percent.group(1)) / 100.0
        # Round away binary noise: 1.0 - 0.8 is 0.19999999999999996, not 0.2.
        return round(max(0.0, 1.0 - value), 6) if reducing else round(value, 6)
    for phrase, value in _FRACTIONS.items():
        if phrase in text.lower():
            return value
    return None


def _quantity(text: str) -> Optional[float]:
    match = re.search(r"(\d+(?:\.\d+)?)\s*kwh", text, re.I)
    return float(match.group(1)) if match else None


def _no_op(index: int, reason: str) -> Dict[str, Any]:
    return {
        "note_index": index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": reason,
    }


def fallback_parse(
    operator_notes: Sequence[str], battery: BatteryConfig
) -> List[Dict[str, Any]]:
    """Best-effort rule-based reading used only when the model is unavailable.

    Deliberately conservative: anything it is not confident about becomes no_op
    rather than an invented constraint.
    """
    results: List[Dict[str, Any]] = []
    for index, note in enumerate(operator_notes):
        text = note.lower()
        hours = _window(note)
        explanation = "Backup parser: model unavailable."

        if not hours:
            results.append(_no_op(index, explanation))
            continue

        solar = re.search(r"solar|pv|photovoltaic|panel|inverter", text)
        charging = re.search(r"charg", text)
        discharging = re.search(r"discharg", text)
        grid = re.search(r"grid|feeder|substation|transformer|import|intake", text)
        reserve = re.search(r"reserve|at least|no lower than|minimum|keep", text)
        blocked = re.search(
            r"not?\s|isolat|unavailab|disabl|prohibit|forbid|offline|out of service", text
        )

        if solar and _factor(note) is not None:
            adjustment = {"hours": hours, "factor": _factor(note)}
            entry = ("solar_reduction", adjustment)
        elif discharging and blocked:
            entry = ("no_discharge_window", {"hours": hours})
        elif charging and blocked:
            entry = ("no_charge_window", {"hours": hours})
        elif grid and _quantity(note) is not None:
            entry = ("max_grid_window", {"hours": hours, "max_grid_kwh": _quantity(note)})
        elif reserve:
            amount = _quantity(note)
            if amount is None:
                percent = re.search(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", note)
                if percent:
                    amount = float(percent.group(1)) / 100.0 * battery.capacity_kwh
            if amount is None:
                results.append(_no_op(index, explanation))
                continue
            entry = ("minimum_battery_reserve", {"hours": hours, "minimum_energy_kwh": amount})
        else:
            results.append(_no_op(index, explanation))
            continue

        results.append(
            {
                "note_index": index,
                "applies": True,
                "directive_type": entry[0],
                "structured_adjustment": entry[1],
                "explanation": explanation,
            }
        )
    return results
