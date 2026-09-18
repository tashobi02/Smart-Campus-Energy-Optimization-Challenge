"""Per-case scoring and aggregation (PLAN 1.5, and the totals half of 1.4).

The constraint bundle here is built from GROUND TRUTH, never from the
response under test and never by calling app.optimizer.directives. A plan
that faithfully applies a misread directive has to fail, and an oracle that
shares the service's directive code cannot notice that.
"""
from __future__ import annotations

from typing import Any, Callable

from judge import within
from judge.interpretation import DIMENSIONS, score_interpretation
from judge.schema import check_schema, is_number

try:  # D1 owns judge/replay.py (PLAN 1.3).
    from judge.replay import check_plan  # type: ignore[attr-defined]

    REPLAY_WIRED = True
except ImportError:  # pragma: no cover - the path disappears once D1 lands
    REPLAY_WIRED = False

    def check_plan(
        plan: list[dict[str, Any]],
        hours: list[dict[str, Any]],
        battery: dict[str, Any],
        bundle: dict[str, Any],
    ) -> list[str]:
        """Stand-in until judge/replay.py exists. Checks nothing, and says so."""
        return []


def build_ground_truth_bundle(
    hours: list[dict[str, Any]],
    directives: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the constraint bundle from ground-truth directives (PLAN 1.3).

    Same shape as app.optimizer.apply_directives, reached independently.
    Overlap rules follow PLAN 2.2: the most restrictive value wins on every
    axis -- smallest solar factor, largest reserve, smallest grid cap.
    """
    effective_solar = [float(hour["solar_kwh"]) for hour in hours]
    no_charge_hours: set[int] = set()
    no_discharge_hours: set[int] = set()
    min_reserve: dict[int, float] = {}
    max_grid: dict[int, float] = {}

    for directive in directives:
        if not directive.get("applies"):
            continue
        adjustment = directive.get("structured_adjustment") or {}
        directive_type = directive.get("directive_type")
        directive_hours = [
            h for h in adjustment.get("hours", []) if isinstance(h, int)
        ]

        if directive_type == "solar_reduction":
            factor = float(adjustment["factor"])
            for hour in directive_hours:
                effective_solar[hour] = min(
                    effective_solar[hour], float(hours[hour]["solar_kwh"]) * factor
                )
        elif directive_type == "minimum_battery_reserve":
            reserve = float(adjustment["minimum_energy_kwh"])
            for hour in directive_hours:
                min_reserve[hour] = max(min_reserve.get(hour, 0.0), reserve)
        elif directive_type == "max_grid_window":
            cap = float(adjustment["max_grid_kwh"])
            for hour in directive_hours:
                max_grid[hour] = min(max_grid.get(hour, cap), cap)
        elif directive_type == "no_charge_window":
            no_charge_hours.update(directive_hours)
        elif directive_type == "no_discharge_window":
            no_discharge_hours.update(directive_hours)

    return {
        "effective_solar": effective_solar,
        "no_charge_hours": no_charge_hours,
        "no_discharge_hours": no_discharge_hours,
        "min_reserve": min_reserve,
        "max_grid": max_grid,
    }


def recompute_totals(
    plan: list[dict[str, Any]],
    hours: list[dict[str, Any]],
) -> dict[str, float]:
    """Derive the three reported totals from hourly_plan.

    hourly_plan is the source of truth; the reported fields are a claim about
    it, checked in check_totals.
    """
    tariffs = {int(hour["hour"]): float(hour["tariff_bdt_per_kwh"]) for hour in hours}
    grid = {
        int(entry["hour"]): float(entry["grid_kwh"])
        for entry in plan
        if is_number(entry.get("grid_kwh")) and isinstance(entry.get("hour"), int)
    }
    return {
        "total_grid_kwh": sum(grid.values()),
        "total_cost_bdt": sum(
            kwh * tariffs.get(hour, 0.0) for hour, kwh in grid.items()
        ),
        "peak_grid_kwh": max(grid.values(), default=0.0),
    }


def check_totals(
    response: dict[str, Any],
    recomputed: dict[str, float],
) -> list[str]:
    """Compare the reported totals against the ones derived from the plan (PLAN 1.4)."""
    errors: list[str] = []
    for field, expected in recomputed.items():
        reported = response.get(field)
        if not is_number(reported):
            continue  # already reported by check_schema
        if not within(float(reported), expected):
            errors.append(
                f"{field} reported as {reported} but hourly_plan gives "
                f"{expected:.2f}"
            )
    return errors


def score_case(
    case: dict[str, Any],
    response: Any,
    checker: Callable[..., list[str]] | None = None,
) -> dict[str, Any]:
    """Score one case. Returns the record shape frozen in PLAND2 section 2."""
    checker = checker or check_plan
    request = case["input"]
    truth = case["expected_output"]

    errors = check_schema(response, request)
    interpretation = score_interpretation(
        response.get("directive_interpretation")
        if isinstance(response, dict)
        else None,
        truth["directive_interpretation"],
    )

    plan = response.get("hourly_plan") if isinstance(response, dict) else None
    team_cost = 0.0
    scoreable = _is_scoreable(plan)
    if scoreable:
        recomputed = recompute_totals(plan, request["hours"])
        team_cost = recomputed["total_cost_bdt"]
        errors.extend(check_totals(response, recomputed))
        bundle = build_ground_truth_bundle(
            request["hours"], truth["directive_interpretation"]
        )
        errors.extend(
            checker(plan, request["hours"], request["battery"], bundle)
        )

    optimal_cost = float(truth["total_cost_bdt"])
    return {
        "case_id": case["id"],
        "valid": not errors,
        "errors": errors,
        "interpretation": interpretation,
        "team_cost": round(team_cost, 2),
        "optimal_cost": round(optimal_cost, 2),
        "quality_ratio": (
            _quality_ratio(optimal_cost, team_cost) if scoreable else 0.0
        ),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, float]:
    """Weight the results the way the rubric does.

    Only three of the seven rubric categories are observable from a response.
    The harness reports those and stays silent about the other 40 points.
    """
    if not results:
        return {
            "interpretation": 0.0,
            "application": 0.0,
            "optimization": 0.0,
            "total": 0.0,
        }

    checks = len(results) * len(DIMENSIONS)
    passed = sum(
        1
        for result in results
        for ok in result["interpretation"].values()
        if ok
    )
    valid = sum(1 for result in results if result["valid"])
    # Optimization credit requires a valid plan. A schedule that ignores a
    # constraint is cheaper than one that respects it, so crediting quality
    # on an invalid plan rewards exactly the wrong thing.
    quality = sum(
        result["quality_ratio"] for result in results if result["valid"]
    )

    scores = {
        "interpretation": 25.0 * passed / checks,
        "application": 25.0 * valid / len(results),
        "optimization": 10.0 * quality / len(results),
    }
    scores["total"] = sum(scores.values())
    return scores


def _quality_ratio(optimal_cost: float, team_cost: float) -> float:
    """Only ever called for a plan that was actually costed.

    A zero-cost plan really is unbeatable, so it earns 1.0. A plan that could
    not be costed at all earns 0.0 and is handled by the caller -- routing it
    through here would hand a missing or shredded plan full optimization
    credit, which is the "too loose" failure the whole harness exists to
    prevent.
    """
    if team_cost <= 0:
        return 1.0
    return round(min(1.0, optimal_cost / team_cost), 4)


def _is_scoreable(plan: Any) -> bool:
    """True when hourly_plan is intact enough to cost and replay.

    Structural, not a scan of the error strings: an error message is for a
    human, and matching on its wording breaks the moment it is reworded.
    """
    if not isinstance(plan, list) or len(plan) != 24:
        return False
    entries = [entry for entry in plan if isinstance(entry, dict)]
    if len(entries) != 24:
        return False
    if {entry.get("hour") for entry in entries} != set(range(24)):
        return False
    return all(is_number(entry.get("grid_kwh")) for entry in entries)
