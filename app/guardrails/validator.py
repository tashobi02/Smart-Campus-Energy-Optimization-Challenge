from __future__ import annotations
from typing import Any, Dict, List

_ALLOWED_TYPES = {
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
}


def validate_directives(
    raw_directives: List[Dict[str, Any]],
    num_notes: int,
) -> List[Dict[str, Any]]:
    """Validate and sanitise raw LLM directive dicts.

    Phase 0: pass-through with basic type/shape assertions.
    """
    if len(raw_directives) != num_notes:
        raise ValueError(
            f"Expected {num_notes} directives, got {len(raw_directives)}"
        )

    clean: List[Dict[str, Any]] = []
    for i, d in enumerate(raw_directives):
        # Basic shape check
        if d.get("note_index") != i:
            raise ValueError(f"Directive {i}: expected note_index={i}")

        dtype = d.get("directive_type", "")
        if dtype not in _ALLOWED_TYPES:
            raise ValueError(f"Directive {i}: unknown type '{dtype}'")

        # TODO: §08 rule — if no_op, applies must be False & structured_adjustment must be None
        # TODO: §08 rule — if not no_op, applies must be True
        # TODO: §08 rule — hours must be unique ints 0..23 ascending
        # TODO: §08 rule — factor must be in [0,1] for solar_reduction
        # TODO: §08 rule — validate applies semantics per directive type

        clean.append(d)
    return clean
