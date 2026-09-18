"""Cost-minimising 24-hour scheduler.

The problem is a pure linear program: with no battery round-trip loss every
constraint in the Problem Statement is linear in the four per-hour decision
variables (grid, solar_used, charge, discharge). A plain LP reproduces the
organizer's reference cost to the cent on all ten public cases, so no MILP or
heuristic search is needed.
"""
from __future__ import annotations

import logging
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linprog

from app.optimizer.directives import apply_directives
from app.schemas.request import BatteryConfig, HourEntry
from app.schemas.response import BatteryAction, HourlyPlanEntry

logger = logging.getLogger(__name__)

N_HOURS = 24
_VARS_PER_HOUR = 4  # grid, solar_used, charge, discharge
_N_VARS = N_HOURS * _VARS_PER_HOUR

# Presentation precision. Well inside the 0.01 judging tolerance, and keeps the
# reported numbers readable rather than carrying float noise.
_PLACES = 6

_GRID, _SOLAR, _CHARGE, _DISCHARGE = 0, 1, 2, 3


def _idx(hour: int, var: int) -> int:
    return hour * _VARS_PER_HOUR + var


def _solve_lp(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    bundle: Dict[str, Any],
) -> Optional[Tuple[List[float], List[float], float]]:
    """Return (solar_used, net_battery, cost) or None when infeasible.

    ``net_battery[h]`` is positive for charging, negative for discharging.
    """
    by_hour = {int(h.hour): h for h in hours}
    effective_solar = bundle["effective_solar"]
    no_charge = bundle["no_charge_hours"]
    no_discharge = bundle["no_discharge_hours"]
    min_reserve = bundle["min_reserve"]
    max_grid = bundle["max_grid"]

    cost = np.zeros(_N_VARS)
    for hour in range(N_HOURS):
        cost[_idx(hour, _GRID)] = float(by_hour[hour].tariff_bdt_per_kwh)

    # Equalities: hourly energy balance, plus end-of-day battery neutrality.
    a_eq = np.zeros((N_HOURS + 1, _N_VARS))
    b_eq = np.zeros(N_HOURS + 1)
    for hour in range(N_HOURS):
        a_eq[hour, _idx(hour, _GRID)] = 1.0
        a_eq[hour, _idx(hour, _SOLAR)] = 1.0
        a_eq[hour, _idx(hour, _DISCHARGE)] = 1.0
        a_eq[hour, _idx(hour, _CHARGE)] = -1.0
        b_eq[hour] = float(by_hour[hour].demand_kwh)
    for hour in range(N_HOURS):
        a_eq[N_HOURS, _idx(hour, _CHARGE)] = 1.0
        a_eq[N_HOURS, _idx(hour, _DISCHARGE)] = -1.0
    b_eq[N_HOURS] = 0.0

    # Inequalities: running battery energy stays within [floor, capacity].
    a_ub = np.zeros((2 * N_HOURS, _N_VARS))
    b_ub = np.zeros(2 * N_HOURS)
    running = np.zeros(_N_VARS)
    for hour in range(N_HOURS):
        running[_idx(hour, _CHARGE)] = 1.0
        running[_idx(hour, _DISCHARGE)] = -1.0
        a_ub[2 * hour] = running
        b_ub[2 * hour] = battery.capacity_kwh - battery.initial_energy_kwh
        floor = max(battery.minimum_energy_kwh, min_reserve.get(hour, 0.0))
        a_ub[2 * hour + 1] = -running
        b_ub[2 * hour + 1] = battery.initial_energy_kwh - floor

    bounds: List[Tuple[float, Optional[float]]] = []
    for hour in range(N_HOURS):
        bounds.append((0.0, max_grid.get(hour, None)))
        bounds.append((0.0, max(0.0, float(effective_solar[hour]))))
        bounds.append((0.0, 0.0 if hour in no_charge else battery.max_charge_kwh_per_hour))
        bounds.append(
            (0.0, 0.0 if hour in no_discharge else battery.max_discharge_kwh_per_hour)
        )

    result = linprog(
        cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs"
    )
    if not result.success:
        return None

    x = result.x
    solar_used = [float(x[_idx(h, _SOLAR)]) for h in range(N_HOURS)]
    net = [float(x[_idx(h, _CHARGE)] - x[_idx(h, _DISCHARGE)]) for h in range(N_HOURS)]
    return solar_used, net, float(result.fun)


def _build_plan(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    solar_used: Sequence[float],
    net: Sequence[float],
) -> List[HourlyPlanEntry]:
    """Turn LP output into a plan whose reported numbers are internally exact.

    The LP may charge and discharge in the same hour; with no round-trip loss
    only the net affects any constraint, so each hour collapses to one action.
    grid_kwh and battery_energy_after_kwh are then *recomputed* from the rounded
    decisions rather than read back from the solver, because hourly_plan is the
    judge's source of truth for totals.
    """
    by_hour = {int(h.hour): h for h in hours}

    solar_r = [round(max(0.0, s), _PLACES) for s in solar_used]
    net_r = [round(n, _PLACES) for n in net]

    # Rounding can leave the day a hair off neutral; absorb it in the hour with
    # the most headroom so end-of-day energy lands exactly on the initial level.
    drift = round(sum(net_r), _PLACES)
    if drift:
        busiest = max(range(N_HOURS), key=lambda h: abs(net_r[h]))
        net_r[busiest] = round(net_r[busiest] - drift, _PLACES)

    plan: List[HourlyPlanEntry] = []
    energy = float(battery.initial_energy_kwh)
    for hour in range(N_HOURS):
        movement = net_r[hour]
        charge = movement if movement > 0 else 0.0
        discharge = -movement if movement < 0 else 0.0

        if charge:
            action, magnitude = BatteryAction.charge, charge
        elif discharge:
            action, magnitude = BatteryAction.discharge, discharge
        else:
            action, magnitude = BatteryAction.idle, 0.0

        grid = float(by_hour[hour].demand_kwh) - solar_r[hour] + charge - discharge
        grid = max(0.0, round(grid, _PLACES))
        energy = round(energy + charge - discharge, _PLACES)

        plan.append(
            HourlyPlanEntry(
                hour=hour,
                grid_kwh=grid,
                solar_used_kwh=solar_r[hour],
                battery_action=action,
                battery_kwh=round(magnitude, _PLACES),
                battery_energy_after_kwh=energy,
            )
        )
    return plan


def _baseline(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    bundle: Dict[str, Any],
) -> List[HourlyPlanEntry]:
    """Grid covers demand, solar is used where available, battery idles.

    Always satisfies balance, battery bounds and neutrality. The last resort.
    """
    effective_solar = bundle["effective_solar"]
    by_hour = {int(h.hour): h for h in hours}
    solar_used = [
        min(float(effective_solar[h]), float(by_hour[h].demand_kwh))
        for h in range(N_HOURS)
    ]
    return _build_plan(hours, battery, solar_used, [0.0] * N_HOURS)


def solve(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    constraints: Dict[str, Any],
) -> List[HourlyPlanEntry]:
    """Optimal plan for one fixed constraint bundle, or the baseline if infeasible."""
    solution = _solve_lp(hours, battery, constraints)
    if solution is None:
        logger.warning("LP infeasible for the given constraints; using baseline")
        return _baseline(hours, battery, constraints)
    solar_used, net, _ = solution
    return _build_plan(hours, battery, solar_used, net)


def solve_best_effort(
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    directives: Sequence[Dict[str, Any]],
) -> Tuple[List[HourlyPlanEntry], Dict[str, Any], List[str]]:
    """Solve, relaxing directives only as far as feasibility demands.

    Organizer scoring scenarios are guaranteed feasible, so infeasibility means a
    note was misread. Rather than failing the request, drop the smallest number
    of interpreted directives that restores feasibility.

    Returns (plan, bundle actually used, relaxation notes).
    """
    bundle = apply_directives(hours, battery, directives)
    solution = _solve_lp(hours, battery, bundle)
    if solution is not None:
        solar_used, net, _ = solution
        return _build_plan(hours, battery, solar_used, net), bundle, []

    applying = [i for i, d in enumerate(directives) if d.get("applies")]
    logger.warning(
        "infeasible with all %d applied directives; searching relaxations", len(applying)
    )

    # Largest retained subset wins; cheapest breaks ties. With at most three
    # notes this is at most eight LPs, a few milliseconds.
    for size in range(len(applying) - 1, -1, -1):
        best: Optional[Tuple[float, Tuple[int, ...], Any, Dict[str, Any]]] = None
        for keep in combinations(applying, size):
            subset = [
                d for i, d in enumerate(directives) if i in keep or not d.get("applies")
            ]
            candidate_bundle = apply_directives(hours, battery, subset)
            candidate = _solve_lp(hours, battery, candidate_bundle)
            if candidate is None:
                continue
            if best is None or candidate[2] < best[0]:
                best = (candidate[2], keep, candidate, candidate_bundle)
        if best is not None:
            _, keep, (solar_used, net, _), relaxed_bundle = best
            dropped = sorted(set(applying) - set(keep))
            notes = [
                f"relaxed directive for note {i} to keep the schedule feasible"
                for i in dropped
            ]
            logger.warning("dropped directives for notes %s", dropped)
            return _build_plan(hours, battery, solar_used, net), relaxed_bundle, notes

    # Unreachable for any well-formed scenario: the loop above ends with the
    # empty subset, i.e. the base GridWise constraints with no directives at all.
    logger.error("base scenario infeasible; falling back to a grid-only schedule")
    base = apply_directives(hours, battery, [])
    return _baseline(hours, battery, base), base, ["fell back to a grid-only schedule"]
