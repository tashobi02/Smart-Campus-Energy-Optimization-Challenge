from __future__ import annotations
from typing import Any, Dict, List

from app.schemas.request import BatteryConfig, HourEntry


def apply_directives(
    hours: List[HourEntry],
    battery: BatteryConfig,
    directives: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Apply validated directives to produce solver constraints.

    Phase 0: identity function — no directive effects applied yet.
    Returns unmodified effective solar and empty constraint sets.
    """
    effective_solar = [h.solar_kwh for h in hours]

    # Constraint containers — populated in Phase 1+
    no_charge_hours: set[int] = set()
    no_discharge_hours: set[int] = set()
    min_reserve: Dict[int, float] = {}  # hour -> minimum battery energy
    max_grid: Dict[int, float] = {}  # hour -> max grid draw

    # TODO: loop over directives and populate constraints
    # TODO: apply solar_reduction factor to effective_solar
    # TODO: apply minimum_battery_reserve to min_reserve
    # TODO: apply no_charge_window to no_charge_hours
    # TODO: apply no_discharge_window to no_discharge_hours
    # TODO: apply max_grid_window to max_grid

    return {
        "effective_solar": effective_solar,
        "no_charge_hours": no_charge_hours,
        "no_discharge_hours": no_discharge_hours,
        "min_reserve": min_reserve,
        "max_grid": max_grid,
    }
