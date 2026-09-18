"""Phase 1 gate — judge harness must not be too strict and not too loose.

Two directions, one file.

Direction 1 (not too strict): every one of the organizer's 10 reference
``expected_output`` schedules is fed back through the harness as if it had
just been produced. A correctly-built harness passes 10/10 — the reference
plans were authored to satisfy the rules they encode. If even one fails, the
harness is mis-specifying a rule (probably a sign error, an off-by-one, or
a tolerance that is too tight).

Direction 2 (not too loose): the harness must catch obvious rule violations
in a mutated reference plan. A passing mutation means a missing rule; a
failing mutation with the wrong message means the rule fired for the wrong
reason. Either way, the harness is unsafe.

These are independent of the live LLM service. They test the offline oracle
that the rubric uses, so a green run here means the eval channel and the
judge channel cannot drift.

PLAND4 §3; PLAND3-5 §2.9.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import pytest

from judge.replay import check_plan
from judge.report import build_ground_truth_bundle

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    with open(FIXTURES) as f:
        return json.load(f)["cases"]


def _bundle(case: dict[str, Any]) -> dict[str, Any]:
    """Build the constraint bundle from the organizer's reference directives.

    The harness does this independently of the service, which is the whole
    point: the oracle and the runtime cannot share a bug.
    """
    return build_ground_truth_bundle(
        case["input"]["hours"],
        case["expected_output"]["directive_interpretation"],
    )


# --- Direction 1: not too strict -------------------------------------------


def test_organizer_reference_schedules_pass_the_harness():
    """10/10 reference plans must replay without violations.

    If you change anything that makes this fail, the harness is wrong, not
    the reference. Investigate the assertion message before mutating either.
    """
    for case in _load_cases():
        bundle = _bundle(case)
        errors = check_plan(
            case["expected_output"]["hourly_plan"],
            case["input"]["hours"],
            case["input"]["battery"],
            bundle,
        )
        assert errors == [], f"{case['id']}: organizer reference failed harness: {errors}"


# --- Direction 2: not too loose -------------------------------------------


def _first_case() -> dict[str, Any]:
    return _load_cases()[0]


def _break_energy_balance(plan, *, hour: int) -> list[dict[str, Any]]:
    """Add 5 kWh of phantom demand by cutting solar_used in one hour."""
    mutated = deepcopy(plan)
    for entry in mutated:
        if entry["hour"] == hour:
            entry["solar_used_kwh"] = max(0.0, entry["solar_used_kwh"] - 5.0)
    return mutated


def _wrong_final_energy(plan, *, delta: float = 5.0) -> list[dict[str, Any]]:
    """Make the reported final battery energy disagree with the replayed value."""
    mutated = deepcopy(plan)
    mutated[-1]["battery_energy_after_kwh"] += delta
    return mutated


def _charge_above_max(plan, *, hour: int, max_charge: float) -> list[dict[str, Any]]:
    """Force the battery_kwh above max_charge_kwh_per_hour in one hour."""
    mutated = deepcopy(plan)
    for entry in mutated:
        if entry["hour"] == hour:
            entry["battery_action"] = "charge"
            entry["battery_kwh"] = max_charge + 5.0
    return mutated


def _solar_above_forecast(plan, *, hour: int, effective_solar: float) -> list[dict[str, Any]]:
    mutated = deepcopy(plan)
    for entry in mutated:
        if entry["hour"] == hour:
            entry["solar_used_kwh"] = effective_solar + 10.0
    return mutated


def _charge_inside_no_charge(plan, *, hour: int) -> list[dict[str, Any]]:
    """If the bundle forbids charging in `hour`, this mutation forces a charge."""
    mutated = deepcopy(plan)
    for entry in mutated:
        if entry["hour"] == hour:
            entry["battery_action"] = "charge"
            entry["battery_kwh"] = 1.0
    return mutated


def _bad_total_cost(plan) -> list[dict[str, Any]]:
    """Doesn't break hourly_plan, but is the canonical case for check_totals."""
    return deepcopy(plan)


# Parametrize so a failure names the missing rule clearly in the report.
@pytest.mark.parametrize(
    "mutation,why",
    [
        (lambda p, c: _break_energy_balance(p, hour=12), "energy balance broken in one hour"),
        (lambda p, c: _wrong_final_energy(p), "E_after[23] off by 5 kWh"),
        (lambda p, c: _charge_above_max(p, hour=18, max_charge=c["input"]["battery"]["max_charge_kwh_per_hour"]),
         "charge above max_charge_kwh_per_hour"),
        (lambda p, c: _solar_above_forecast(
            p, hour=12,
            effective_solar=_bundle(c)["effective_solar"][12]),
         "solar_used > effective_solar"),
        (lambda p, c: _charge_inside_no_charge(p, hour=2), "charge in a no_charge_window"),
    ],
)
def test_mutations_are_caught_by_the_harness(mutation: Callable[..., Any], why: str):
    case = _first_case()
    bundle = _bundle(case)
    mutated = mutation(deepcopy(case["expected_output"]["hourly_plan"]), case)
    errors = check_plan(
        mutated,
        case["input"]["hours"],
        case["input"]["battery"],
        bundle,
    )
    assert errors, f"harness did not catch: {why}"


# --- Direction 1.5: harness is symmetric across pydantic-or-dict inputs ----
# check_plan is documented to accept both. The service hands it models, the
# harness hands it dicts. If a path uses the wrong accessor it would only
# fail on one path.


def test_harness_accepts_plain_dicts():
    """The reference test above already exercises dicts, but spell it out."""
    case = _first_case()
    bundle = _bundle(case)
    plan = deepcopy(case["expected_output"]["hourly_plan"])
    errors = check_plan(plan, case["input"]["hours"], case["input"]["battery"], bundle)
    assert errors == []
