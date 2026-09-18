"""Response schema validation (PLAN 1.1).

Shape only — whether the response is a legal GridWise response at all.
Whether it is the *right* answer is judge.interpretation's and
judge.report's problem.
"""
from __future__ import annotations

import math
from typing import Any

from judge import TOL

REQUIRED_TOP_FIELDS = (
    "scenario_id",
    "directive_interpretation",
    "hourly_plan",
    "total_grid_kwh",
    "total_cost_bdt",
    "peak_grid_kwh",
    "plan_summary",
)

PLAN_FIELDS = (
    "hour",
    "grid_kwh",
    "solar_used_kwh",
    "battery_action",
    "battery_kwh",
    "battery_energy_after_kwh",
)

INTERPRETATION_FIELDS = (
    "note_index",
    "applies",
    "directive_type",
    "structured_adjustment",
    "explanation",
)

BATTERY_ACTIONS = frozenset({"charge", "discharge", "idle"})

DIRECTIVE_TYPES = frozenset(
    {
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    }
)

# Non-negative plan quantities, per the response schema.
_NON_NEGATIVE_PLAN_FIELDS = (
    "grid_kwh",
    "solar_used_kwh",
    "battery_kwh",
    "battery_energy_after_kwh",
)


def is_number(value: Any) -> bool:
    """True for a finite real number. Rejects bool, which is an int in Python."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def check_schema(response: Any, request: dict[str, Any]) -> list[str]:
    """Return a list of schema violations. Empty list means the shape is legal."""
    if not isinstance(response, dict):
        return [f"response is {type(response).__name__}, expected an object"]

    errors: list[str] = []

    for field in REQUIRED_TOP_FIELDS:
        if field not in response:
            errors.append(f"missing required field '{field}'")

    expected_id = request.get("scenario_id")
    if "scenario_id" in response and response["scenario_id"] != expected_id:
        errors.append(
            f"scenario_id {response['scenario_id']!r} does not echo "
            f"the request's {expected_id!r}"
        )

    if "plan_summary" in response and not isinstance(
        response["plan_summary"], str
    ):
        errors.append("plan_summary is not a string")

    for field in ("total_grid_kwh", "total_cost_bdt", "peak_grid_kwh"):
        if field not in response:
            continue
        value = response[field]
        if not is_number(value):
            errors.append(f"{field} is not a finite number: {value!r}")
        elif value < -TOL:
            errors.append(f"{field} is negative: {value}")

    errors.extend(_check_plan_shape(response.get("hourly_plan")))
    errors.extend(
        _check_interpretation_shape(response.get("directive_interpretation"))
    )
    return errors


def _check_plan_shape(plan: Any) -> list[str]:
    if plan is None:
        return []
    if not isinstance(plan, list):
        return [f"hourly_plan is {type(plan).__name__}, expected a list"]

    errors: list[str] = []
    if len(plan) != 24:
        errors.append(f"hourly_plan has {len(plan)} entries, expected 24")

    seen: set[int] = set()
    for position, entry in enumerate(plan):
        where = f"hourly_plan[{position}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} is not an object")
            continue

        for field in PLAN_FIELDS:
            if field not in entry:
                errors.append(f"{where}: missing '{field}'")

        hour = entry.get("hour")
        if isinstance(hour, bool) or not isinstance(hour, int):
            errors.append(f"{where}: hour is not an integer: {hour!r}")
        else:
            where = f"hour {hour}"
            if not 0 <= hour <= 23:
                errors.append(f"{where}: outside 0..23")
            if hour in seen:
                errors.append(f"{where}: duplicate entry")
            seen.add(hour)

        for field in _NON_NEGATIVE_PLAN_FIELDS:
            if field not in entry:
                continue
            value = entry[field]
            if not is_number(value):
                errors.append(
                    f"{where}: {field} is not a finite number: {value!r}"
                )
            elif value < -TOL:
                errors.append(f"{where}: {field} is negative: {value}")

        action = entry.get("battery_action")
        if action not in BATTERY_ACTIONS:
            errors.append(f"{where}: battery_action {action!r} not in enum")
        elif action == "idle":
            battery_kwh = entry.get("battery_kwh")
            if is_number(battery_kwh) and abs(battery_kwh) > TOL:
                errors.append(
                    f"{where}: battery_action is idle but "
                    f"battery_kwh is {battery_kwh}"
                )

    missing = sorted(set(range(24)) - seen)
    if missing:
        errors.append(f"hourly_plan is missing hours: {missing}")
    return errors


def _check_interpretation_shape(entries: Any) -> list[str]:
    if entries is None:
        return []
    if not isinstance(entries, list):
        return [
            f"directive_interpretation is {type(entries).__name__}, "
            "expected a list"
        ]

    errors: list[str] = []
    for position, entry in enumerate(entries):
        where = f"directive_interpretation[{position}]"
        if not isinstance(entry, dict):
            errors.append(f"{where} is not an object")
            continue

        for field in INTERPRETATION_FIELDS:
            if field not in entry:
                errors.append(f"{where}: missing '{field}'")

        note_index = entry.get("note_index")
        if isinstance(note_index, bool) or not isinstance(note_index, int):
            errors.append(f"{where}: note_index is not an integer")

        if "applies" in entry and not isinstance(entry["applies"], bool):
            errors.append(f"{where}: applies is not a boolean")

        directive_type = entry.get("directive_type")
        if directive_type not in DIRECTIVE_TYPES:
            errors.append(
                f"{where}: directive_type {directive_type!r} not in enum"
            )

        adjustment = entry.get("structured_adjustment")
        if adjustment is not None and not isinstance(adjustment, dict):
            errors.append(
                f"{where}: structured_adjustment is "
                f"{type(adjustment).__name__}, expected an object or null"
            )

        if "explanation" in entry and not isinstance(entry["explanation"], str):
            errors.append(f"{where}: explanation is not a string")

    return errors
