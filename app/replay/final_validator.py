from __future__ import annotations
from typing import List, Tuple

from app.schemas.response import HourlyPlanEntry


def replay(
    plan: List[HourlyPlanEntry],
) -> Tuple[bool, List[str]]:
    """Replay the plan and check basic validity.

    Phase 0: checks 24 unique hours and non-negative values.
    Real constraint replay (energy balance, battery bounds, end-of-day
    neutrality) is a Phase-1+ task.
    """
    errors: List[str] = []

    # Check exactly 24 entries
    if len(plan) != 24:
        errors.append(f"Expected 24 hourly entries, got {len(plan)}")
        return False, errors

    # Check unique hours 0..23
    seen_hours = set()
    for entry in plan:
        if entry.hour < 0 or entry.hour > 23:
            errors.append(f"Hour {entry.hour} out of range [0, 23]")
        if entry.hour in seen_hours:
            errors.append(f"Duplicate hour {entry.hour}")
        seen_hours.add(entry.hour)

    if seen_hours != set(range(24)):
        missing = set(range(24)) - seen_hours
        errors.append(f"Missing hours: {sorted(missing)}")

    # Check non-negative values
    for entry in plan:
        if entry.grid_kwh < 0:
            errors.append(f"Hour {entry.hour}: negative grid_kwh")
        if entry.solar_used_kwh < 0:
            errors.append(f"Hour {entry.hour}: negative solar_used_kwh")
        if entry.battery_kwh < 0:
            errors.append(f"Hour {entry.hour}: negative battery_kwh")
        if entry.battery_energy_after_kwh < 0:
            errors.append(f"Hour {entry.hour}: negative battery_energy_after_kwh")

    return len(errors) == 0, errors
