"""D1: LP optimality, plan consistency, and the infeasibility ladder."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.optimizer.directives import apply_directives
from app.optimizer.solver import solve_best_effort
from app.replay.final_validator import replay
from app.schemas.request import BatteryConfig, ScenarioRequest
from judge.replay import TOL, recompute_totals

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"


def _cases():
    with open(FIXTURES) as f:
        return json.load(f)["cases"]


@pytest.fixture(params=_cases(), ids=lambda c: c["id"])
def case(request):
    return request.param


def _solved(case):
    req = ScenarioRequest(**case["input"])
    directives = case["expected_output"]["directive_interpretation"]
    plan, bundle, relaxations = solve_best_effort(req.hours, req.battery, directives)
    return req, plan, bundle, relaxations


def test_matches_reference_optimal_cost(case):
    """The LP must reach the organizer's optimal cost, not merely a valid plan."""
    req, plan, _, _ = _solved(case)
    cost = recompute_totals(plan, req.hours)["total_cost_bdt"]
    assert cost == pytest.approx(case["expected_output"]["total_cost_bdt"], abs=TOL)


def test_plan_is_valid(case):
    req, plan, bundle, relaxations = _solved(case)
    valid, errors = replay(plan, req.hours, req.battery, bundle)
    assert valid, errors
    assert relaxations == [], "a feasible public case should need no relaxation"


def test_battery_returns_to_initial_energy(case):
    req, plan, _, _ = _solved(case)
    assert plan[23].battery_energy_after_kwh == pytest.approx(
        req.battery.initial_energy_kwh, abs=TOL
    )


def test_each_hour_has_a_single_consistent_action(case):
    """The LP may charge and discharge at once; normalization must collapse it."""
    _, plan, _, _ = _solved(case)
    for entry in plan:
        if entry.battery_action.value == "idle":
            assert entry.battery_kwh == 0.0
        else:
            assert entry.battery_kwh > 0.0


def test_reported_totals_derive_from_the_plan(case):
    """hourly_plan is the judge's source of truth, so totals must match it."""
    req, plan, _, _ = _solved(case)
    totals = recompute_totals(plan, req.hours)
    assert totals["total_grid_kwh"] == pytest.approx(
        sum(e.grid_kwh for e in plan), abs=TOL
    )
    assert totals["peak_grid_kwh"] == pytest.approx(
        max(e.grid_kwh for e in plan), abs=TOL
    )


def _flat_scenario(**battery_overrides):
    battery = BatteryConfig(
        capacity_kwh=200,
        initial_energy_kwh=100,
        minimum_energy_kwh=40,
        max_charge_kwh_per_hour=50,
        max_discharge_kwh_per_hour=50,
        **battery_overrides,
    )
    hours = [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0}
        for h in range(24)
    ]
    request = ScenarioRequest(
        scenario_id="TEST", operator_notes=["n"], hours=hours, battery=battery.model_dump()
    )
    return request


def _directive(kind, adjustment, index=0):
    return {
        "note_index": index,
        "applies": True,
        "directive_type": kind,
        "structured_adjustment": adjustment,
        "explanation": "test",
    }


def test_contradictory_directives_relax_instead_of_failing():
    """A reserve at full capacity while charging is banned cannot both hold."""
    req = _flat_scenario()
    directives = [
        _directive("minimum_battery_reserve", {"hours": list(range(24)), "minimum_energy_kwh": 200}, 0),
        _directive("no_charge_window", {"hours": list(range(24))}, 1),
    ]
    plan, bundle, relaxations = solve_best_effort(req.hours, req.battery, directives)

    assert relaxations, "an infeasible directive set should report a relaxation"
    valid, errors = replay(plan, req.hours, req.battery, bundle)
    assert valid, errors


def test_impossible_grid_cap_still_returns_a_valid_plan():
    """A zero grid cap all day cannot be met; the service must not fail."""
    req = _flat_scenario()
    directives = [_directive("max_grid_window", {"hours": list(range(24)), "max_grid_kwh": 0})]
    plan, bundle, _ = solve_best_effort(req.hours, req.battery, directives)

    valid, errors = replay(plan, req.hours, req.battery, bundle)
    assert valid, errors
    assert len(plan) == 24


def test_solar_reduction_caps_usable_solar():
    req = _flat_scenario()
    hours = [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 80.0, "tariff_bdt_per_kwh": 5.0}
        for h in range(24)
    ]
    req = ScenarioRequest(
        scenario_id="TEST",
        operator_notes=["n"],
        hours=hours,
        battery=req.battery.model_dump(),
    )
    directives = [_directive("solar_reduction", {"hours": [10, 11], "factor": 0.25})]
    plan, _, _ = solve_best_effort(req.hours, req.battery, directives)

    assert plan[10].solar_used_kwh <= 20.0 + TOL
    assert plan[12].solar_used_kwh == pytest.approx(80.0, abs=TOL)


def test_overlapping_reductions_take_the_most_restrictive():
    req = _flat_scenario()
    hours = [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 80.0, "tariff_bdt_per_kwh": 5.0}
        for h in range(24)
    ]
    bundle = apply_directives(
        ScenarioRequest(
            scenario_id="T", operator_notes=["n"], hours=hours,
            battery=req.battery.model_dump(),
        ).hours,
        req.battery,
        [
            _directive("solar_reduction", {"hours": [10], "factor": 0.5}, 0),
            _directive("solar_reduction", {"hours": [10], "factor": 0.25}, 1),
        ],
    )
    # 0.25 wins outright; the two are not compounded into 0.125.
    assert bundle["effective_solar"][10] == pytest.approx(20.0)
