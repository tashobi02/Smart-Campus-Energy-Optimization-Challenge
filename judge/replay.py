"""Deterministic replay of a 24-hour plan against the GridWise rules.

Owned by D1. Both the offline judge harness and the in-service final validator
call ``check_plan``, so the oracle and the service cannot drift apart.

Accessors tolerate either pydantic models (the service) or plain dicts (the
harness reading the public case pack), so one implementation serves both.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

# Absolute tolerance from the Problem Statement, §11.5.
TOL = 0.01


def _f(obj: Any, key: str) -> Any:
    """Read a field from either a mapping or an object."""
    if isinstance(obj, dict):
        return obj[key]
    return getattr(obj, key)


def _num(obj: Any, key: str) -> float:
    return float(_f(obj, key))


def _action(entry: Any) -> str:
    """battery_action as a plain string, whether or not it is an Enum."""
    value = _f(entry, "battery_action")
    return getattr(value, "value", value)


def check_plan(
    plan: Sequence[Any],
    hours: Sequence[Any],
    battery: Any,
    bundle: Dict[str, Any],
) -> List[str]:
    """Return a list of violation strings. An empty list means the plan is valid.

    ``bundle`` is the constraint bundle produced by ``apply_directives`` — built
    from the team's directives in the service, and from organizer ground truth
    in the harness.
    """
    errors: List[str] = []

    if len(plan) != 24:
        return [f"expected 24 hourly entries, got {len(plan)}"]

    plan_hours = [int(_f(e, "hour")) for e in plan]
    if sorted(plan_hours) != list(range(24)):
        errors.append(f"hourly_plan hours are not exactly 0..23: {sorted(plan_hours)}")
        return errors

    capacity = _num(battery, "capacity_kwh")
    initial = _num(battery, "initial_energy_kwh")
    base_min = _num(battery, "minimum_energy_kwh")
    max_charge = _num(battery, "max_charge_kwh_per_hour")
    max_discharge = _num(battery, "max_discharge_kwh_per_hour")

    effective_solar: Sequence[float] = bundle["effective_solar"]
    no_charge_hours = bundle["no_charge_hours"]
    no_discharge_hours = bundle["no_discharge_hours"]
    min_reserve: Dict[int, float] = bundle["min_reserve"]
    max_grid: Dict[int, float] = bundle["max_grid"]

    demand = {int(_f(h, "hour")): _num(h, "demand_kwh") for h in hours}

    by_hour = {int(_f(e, "hour")): e for e in plan}
    energy = initial

    for hour in range(24):
        entry = by_hour[hour]
        grid = _num(entry, "grid_kwh")
        solar_used = _num(entry, "solar_used_kwh")
        action = _action(entry)
        magnitude = _num(entry, "battery_kwh")
        reported_after = _num(entry, "battery_energy_after_kwh")

        for name, value in (
            ("grid_kwh", grid),
            ("solar_used_kwh", solar_used),
            ("battery_kwh", magnitude),
            ("battery_energy_after_kwh", reported_after),
        ):
            if not math.isfinite(value):
                errors.append(f"hour {hour}: {name} is not finite")
            elif value < -TOL:
                errors.append(f"hour {hour}: {name} is negative ({value})")

        if action not in ("charge", "discharge", "idle"):
            errors.append(f"hour {hour}: invalid battery_action {action!r}")
            continue

        charge = magnitude if action == "charge" else 0.0
        discharge = magnitude if action == "discharge" else 0.0

        if action == "idle" and abs(magnitude) > TOL:
            errors.append(f"hour {hour}: idle action with battery_kwh {magnitude}")

        # §9.5 energy balance
        residual = grid + solar_used + discharge - demand[hour] - charge
        if abs(residual) > TOL:
            errors.append(
                f"hour {hour}: energy balance off by {residual:.4f} "
                f"(grid {grid} + solar {solar_used} + discharge {discharge} "
                f"!= demand {demand[hour]} + charge {charge})"
            )

        # §9.4 solar usage
        if solar_used > effective_solar[hour] + TOL:
            errors.append(
                f"hour {hour}: solar_used {solar_used} exceeds effective solar "
                f"{effective_solar[hour]}"
            )

        # §9.3 hourly rate limits
        if charge > max_charge + TOL:
            errors.append(f"hour {hour}: charge {charge} exceeds limit {max_charge}")
        if discharge > max_discharge + TOL:
            errors.append(
                f"hour {hour}: discharge {discharge} exceeds limit {max_discharge}"
            )

        # §9.1 battery state transition
        energy = energy + charge - discharge
        if abs(energy - reported_after) > TOL:
            errors.append(
                f"hour {hour}: battery_energy_after_kwh {reported_after} "
                f"!= replayed {energy:.4f}"
            )

        # §9.2 bounds, raised by any active reserve directive
        floor = max(base_min, min_reserve.get(hour, 0.0))
        if reported_after < floor - TOL:
            errors.append(
                f"hour {hour}: battery energy {reported_after} below reserve {floor}"
            )
        if reported_after > capacity + TOL:
            errors.append(
                f"hour {hour}: battery energy {reported_after} above capacity {capacity}"
            )

        # Operator directives applied to the schedule
        if hour in no_charge_hours and charge > TOL:
            errors.append(f"hour {hour}: charged {charge} inside a no_charge_window")
        if hour in no_discharge_hours and discharge > TOL:
            errors.append(
                f"hour {hour}: discharged {discharge} inside a no_discharge_window"
            )
        if hour in max_grid and grid > max_grid[hour] + TOL:
            errors.append(
                f"hour {hour}: grid {grid} exceeds max_grid_window cap {max_grid[hour]}"
            )

    # §9.6 end-of-day battery neutrality
    final_after = _num(by_hour[23], "battery_energy_after_kwh")
    if abs(final_after - initial) > TOL:
        errors.append(
            f"end-of-day battery {final_after} != initial {initial} (neutrality)"
        )

    return errors


def recompute_totals(plan: Sequence[Any], hours: Sequence[Any]) -> Dict[str, float]:
    """Derive the reported totals from hourly_plan alone.

    hourly_plan is the judge's source of truth for totals, so these are the
    values the response must carry.
    """
    tariff = {int(_f(h, "hour")): _num(h, "tariff_bdt_per_kwh") for h in hours}
    grid_by_hour = {int(_f(e, "hour")): _num(e, "grid_kwh") for e in plan}

    total_grid = sum(grid_by_hour.values())
    total_cost = sum(kwh * tariff[hour] for hour, kwh in grid_by_hour.items())
    peak_grid = max(grid_by_hour.values()) if grid_by_hour else 0.0

    return {
        "total_grid_kwh": total_grid,
        "total_cost_bdt": total_cost,
        "peak_grid_kwh": peak_grid,
    }
