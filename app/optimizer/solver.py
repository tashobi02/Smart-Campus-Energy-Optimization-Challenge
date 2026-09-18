from __future__ import annotations
from typing import Any, Dict, List

from app.schemas.request import BatteryConfig, HourEntry
from app.schemas.response import BatteryAction, HourlyPlanEntry


def solve(
    hours: List[HourEntry],
    battery: BatteryConfig,
    constraints: Dict[str, Any],
) -> List[HourlyPlanEntry]:
    """Produce a 24-hour energy plan.

    Phase 0: naive always-feasible baseline.
    Grid covers all demand every hour, all solar is used, battery stays idle.
    Trivially satisfies energy balance and end-of-day neutrality.
    """
    effective_solar: List[float] = constraints["effective_solar"]
    plan: List[HourlyPlanEntry] = []
    battery_energy = battery.initial_energy_kwh

    for i, h in enumerate(hours):
        solar_used = min(effective_solar[i], h.demand_kwh)
        grid = h.demand_kwh - solar_used

        plan.append(
            HourlyPlanEntry(
                hour=h.hour,
                grid_kwh=grid,
                solar_used_kwh=solar_used,
                battery_action=BatteryAction.idle,
                battery_kwh=0.0,
                battery_energy_after_kwh=battery_energy,
            )
        )

    return plan
