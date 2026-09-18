from __future__ import annotations
from typing import Any, Dict, List, Sequence

from app.schemas.request import BatteryConfig, HourEntry


def _hours_of(adjustment: Dict[str, Any]) -> List[int]:
    return [int(h) for h in adjustment.get("hours", [])]


def apply_directives(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    directives: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """Turn validated directives into the constraint bundle the solver consumes.

    The bundle shape is a frozen interface: the solver, the in-service replay,
    and the offline judge harness all read it.
    """
    # Index by hour value, not list position: the bundle is addressed by hour
    # and the request is not guaranteed to arrive sorted.
    base_solar: Dict[int, float] = {int(h.hour): float(h.solar_kwh) for h in hours}
    effective_solar: List[float] = [base_solar[h] for h in range(24)]
    no_charge_hours: set[int] = set()
    no_discharge_hours: set[int] = set()
    min_reserve: Dict[int, float] = {}
    max_grid: Dict[int, float] = {}

    for directive in directives:
        if not directive.get("applies"):
            continue

        kind = directive.get("directive_type")
        adjustment = directive.get("structured_adjustment") or {}

        if kind == "solar_reduction":
            factor = float(adjustment["factor"])
            for hour in _hours_of(adjustment):
                # Two reductions on one hour: keep the most restrictive rather
                # than compounding them — factor is an absolute remaining
                # fraction of the original forecast, not a multiplier to stack.
                effective_solar[hour] = min(
                    effective_solar[hour], base_solar[hour] * factor
                )

        elif kind == "minimum_battery_reserve":
            reserve = float(adjustment["minimum_energy_kwh"])
            for hour in _hours_of(adjustment):
                min_reserve[hour] = max(min_reserve.get(hour, 0.0), reserve)

        elif kind == "no_charge_window":
            no_charge_hours.update(_hours_of(adjustment))

        elif kind == "no_discharge_window":
            no_discharge_hours.update(_hours_of(adjustment))

        elif kind == "max_grid_window":
            cap = float(adjustment["max_grid_kwh"])
            for hour in _hours_of(adjustment):
                max_grid[hour] = min(max_grid.get(hour, cap), cap)

    return {
        "effective_solar": effective_solar,
        "no_charge_hours": no_charge_hours,
        "no_discharge_hours": no_discharge_hours,
        "min_reserve": min_reserve,
        "max_grid": max_grid,
    }
