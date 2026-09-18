"""Deterministic guardrails over untrusted LLM interpretation output.

Governing rule: **repair, don't raise**. A ValueError here becomes a 500, which
forfeits API, schema and reliability credit on top of the affected case. Any
directive that cannot be repaired into a legal shape degrades to no_op — one
note is lost, the response stays valid.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Sequence

from app.schemas.request import BatteryConfig

logger = logging.getLogger(__name__)

_ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}

# Required structured_adjustment keys per directive type, per Problem Statement §04.
_REQUIRED_KEYS = {
    "solar_reduction": ("hours", "factor"),
    "minimum_battery_reserve": ("hours", "minimum_energy_kwh"),
    "no_charge_window": ("hours",),
    "no_discharge_window": ("hours",),
    "max_grid_window": ("hours", "max_grid_kwh"),
}


def _no_op(note_index: int, explanation: str) -> Dict[str, Any]:
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": explanation,
    }


def _number(value: Any) -> Optional[float]:
    """Coerce to a finite float, or None if that is not possible."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _clean_hours(value: Any) -> List[int]:
    """Unique integers 0..23 in ascending order. Junk is dropped, not fatal."""
    if not isinstance(value, (list, tuple, set)):
        return []
    hours: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            continue
        try:
            hour = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= hour <= 23:
            hours.add(hour)
    return sorted(hours)


def _repair(raw: Any, note_index: int, battery: BatteryConfig) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        logger.warning("note %d: interpretation was not an object", note_index)
        return _no_op(note_index, "Interpretation could not be read; treated as no_op.")

    explanation = raw.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        explanation = "No explanation supplied."
    explanation = explanation.strip()[:500]

    kind = raw.get("directive_type")
    if kind not in _ALLOWED_TYPES:
        logger.warning("note %d: unsupported directive_type %r", note_index, kind)
        return _no_op(note_index, "Unsupported directive type; treated as no_op.")

    if kind == "no_op":
        return _no_op(note_index, explanation)

    adjustment = raw.get("structured_adjustment")
    if not isinstance(adjustment, dict):
        logger.warning("note %d: %s without a structured_adjustment", note_index, kind)
        return _no_op(note_index, "Directive had no structured adjustment; treated as no_op.")

    hours = _clean_hours(adjustment.get("hours"))
    if not hours:
        logger.warning("note %d: %s with no usable hours", note_index, kind)
        return _no_op(note_index, "Directive listed no valid hours; treated as no_op.")

    clean: Dict[str, Any] = {"hours": hours}

    if kind == "solar_reduction":
        factor = _number(adjustment.get("factor"))
        if factor is None:
            logger.warning("note %d: solar_reduction with unusable factor", note_index)
            return _no_op(note_index, "Solar factor was unusable; treated as no_op.")
        if not 0.0 <= factor <= 1.0:
            logger.warning("note %d: clamped factor %s into [0,1]", note_index, factor)
            factor = min(1.0, max(0.0, factor))
        clean["factor"] = factor

    elif kind == "minimum_battery_reserve":
        reserve = _number(adjustment.get("minimum_energy_kwh"))
        if reserve is None or reserve < 0:
            logger.warning("note %d: unusable battery reserve %r", note_index, reserve)
            return _no_op(note_index, "Reserve value was unusable; treated as no_op.")
        if reserve > battery.capacity_kwh:
            # §08: reserve may not exceed capacity. Clamping keeps the operator's
            # intent (hold as much as possible) instead of discarding the note.
            logger.warning(
                "note %d: clamped reserve %s to capacity %s",
                note_index, reserve, battery.capacity_kwh,
            )
            reserve = float(battery.capacity_kwh)
        clean["minimum_energy_kwh"] = reserve

    elif kind == "max_grid_window":
        cap = _number(adjustment.get("max_grid_kwh"))
        if cap is None or cap < 0:
            logger.warning("note %d: unusable grid cap %r", note_index, cap)
            return _no_op(note_index, "Grid cap was unusable; treated as no_op.")
        clean["max_grid_kwh"] = cap

    # Drop any key the directive type does not define, so the response shape is exact.
    clean = {key: clean[key] for key in _REQUIRED_KEYS[kind]}

    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": kind,
        "structured_adjustment": clean,
        "explanation": explanation,
    }


def validate_directives(
    raw_directives: Any,
    num_notes: int,
    battery: BatteryConfig,
) -> List[Dict[str, Any]]:
    """Return exactly ``num_notes`` legal directive records in note_index order.

    Never raises: missing entries are synthesised as no_op, duplicates and
    out-of-range indices are dropped, and malformed adjustments are repaired or
    degraded.
    """
    by_index: Dict[int, Any] = {}
    if isinstance(raw_directives, Sequence) and not isinstance(raw_directives, (str, bytes)):
        for position, raw in enumerate(raw_directives):
            index = raw.get("note_index") if isinstance(raw, dict) else None
            if not isinstance(index, int) or isinstance(index, bool):
                index = position
            if 0 <= index < num_notes and index not in by_index:
                by_index[index] = raw
    else:
        logger.warning("interpretation payload was not a list; treating all notes as no_op")

    directives: List[Dict[str, Any]] = []
    for note_index in range(num_notes):
        if note_index in by_index:
            directives.append(_repair(by_index[note_index], note_index, battery))
        else:
            logger.warning("note %d: no interpretation returned", note_index)
            directives.append(
                _no_op(note_index, "No interpretation was returned for this note.")
            )
    return directives
