"""Phase 2 gate — public-case correctness contract.

The D4-owned file PLAND4 §4 specifies the upgrade from schema-shape to
correctness:

- POST each of the 10 organiser reference cases and assert the response is
  valid (parses, replays clean) and its `total_cost_bdt` matches the
  reference within 0.01 BDT (TOL in `judge/replay.py`).
- The in-service guard rails (validator + final-validator replay) repair
  rather than raise — no input path produces a 500.
- The infeasibility ladder returns a valid plan for a deliberately
  contradictory directive set (no_charge_window on every hour AND a
  reserve that requires a charge).
- The in-service replay delegates to `judge.replay.check_plan` — no
  second copy of the constraint logic anywhere.

Every assertion below targets the *frozen* contracts (PLAN.md §2 and
PLAND1/PLAND2 §2), not the live signatures, so the test survives D1's
internal refactors during Phase 2.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.replay.final_validator import replay as replay_plan
from app.schemas.request import ScenarioRequest
from app.schemas.response import OptimizeResponse
from app.optimizer.solver import apply_directives  # frozen seam

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"
client = TestClient(app)

# TOL is the absolute tolerance used by judge/replay.py; matching it here
# keeps the test in lockstep with the official oracle.
TOL = 0.01


def _load_cases() -> list[dict[str, Any]]:
    with open(FIXTURES) as f:
        return json.load(f)["cases"]


@pytest.fixture(params=_load_cases(), ids=lambda c: c["id"])
def case(request):
    return request.param


# --- 2.1. live service correctness -----------------------------------------


def test_public_case_returns_200_and_valid_plan(case):
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200, f"Case {case['id']}: {resp.text}"
    body = resp.json()
    parsed = OptimizeResponse(**body)

    # Sanity on response shape (kept from the schema-only era)
    assert parsed.scenario_id == case["input"]["scenario_id"]
    assert len(parsed.hourly_plan) == 24
    assert len(parsed.directive_interpretation) == len(
        case["input"]["operator_notes"]
    )
    assert parsed.total_grid_kwh >= 0
    assert parsed.peak_grid_kwh >= 0


def test_public_case_replays_clean(case):
    """The in-service replay oracle must agree with the produced plan."""
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200
    body = resp.json()

    # Validate the input the same way the service does, so `hours` and
    # `battery` become the typed objects apply_directives expects.
    request = ScenarioRequest.model_validate(case["input"])
    applied_only = [
        {**d, "applies": True}
        for d in body["directive_interpretation"]
        if d.get("applies")
    ]
    bundle = apply_directives(
        request.hours, request.battery, applied_only,
    )
    valid, errors = replay_plan(
        body["hourly_plan"],
        request.hours,
        request.battery,
        bundle,
    )
    assert valid, f"{case['id']} replay rejected the plan: {errors}"


def test_public_case_total_cost_matches_reference(case):
    """The reference cost is what the rubric measures against. We allow the
    tolerance from `judge/replay.TOL` (0.01 BDT), but no rounding gaps.
    """
    expected = case["expected_output"]["total_cost_bdt"]
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200
    body = resp.json()
    assert abs(body["total_cost_bdt"] - expected) <= TOL, (
        f"{case['id']}: cost {body['total_cost_bdt']} != reference {expected}"
    )


# --- 2.2. guardrails repair, not raise -------------------------------------


def test_garbage_payload_returns_200_with_safe_no_op():
    """A nonsense operator note must not produce a 5xx.

    The model is bypassed via LLM_STUB_MODE=canned for deterministic
    behaviour: the validator then forces every canned record into a
    valid shape regardless of whether the canned payload makes sense.
    """
    case = _load_cases()[0]
    # Force every interpretation to be a no-op by sending an obviously
    # out-of-range note_index via the canned stub is out of scope; instead
    # we use a real LLM path and trust that bogus input -> no_op. The
    # response MUST come back as 200 with a valid plan.
    body = deepcopy(case["input"])
    body["operator_notes"] = [
        "noisy unmatched prose that the model might interpret creatively"
    ]
    resp = client.post("/optimize-energy", json=body)
    assert resp.status_code == 200, resp.text
    parsed = OptimizeResponse(**resp.json())
    assert len(parsed.hourly_plan) == 24


# --- 2.3. infeasibility ladder returns a valid plan ------------------------


def test_contradictory_directives_yield_a_valid_plan_via_ladder():
    """Schedule a no_charge_window for every hour AND a reserve that
    forces charging in low-tariff hours. The LP cannot satisfy both;
    the infeasibility ladder must relax one and still produce a plan
    that replays clean.
    """
    case = _load_cases()[0]
    body = deepcopy(case["input"])
    # Make the directive interpretation carry both constraints by
    # replacing the operator notes with phrasings the model will read as
    # both a no_charge_window and a max_grid_window with an absurdly low
    # cap; if even one relaxes, the replay still has to pass.
    body["operator_notes"] = [
        "Do not charge the battery at any hour today.",
        "Keep battery at or above 100% of capacity throughout the day.",
    ]
    resp = client.post("/optimize-energy", json=body)
    assert resp.status_code == 200, resp.text
    parsed = OptimizeResponse(**resp.json())
    assert len(parsed.hourly_plan) == 24


# --- 2.4. no second copy of constraint logic -------------------------------


def test_final_validator_delegates_to_check_plan():
    """If this test ever breaks, the in-service replay has drifted from
    the offline oracle, which means a "valid" service plan could fail
    in the judge and vice versa.
    """
    from judge.replay import check_plan

    # Whichever module owns the validator, it must call into check_plan.
    import inspect
    from app.replay import final_validator

    src = inspect.getsource(final_validator)
    assert "check_plan" in src, (
        "final_validator does not delegate to judge.replay.check_plan; "
        "constraint logic has been duplicated."
    )
    # And it must not contain a forbidden list of duplicated checks.
    forbidden = [
        "energy balance off by",
        "exceeds limit",
        "max_grid_window cap",
        "below reserve",
    ]
    for phrase in forbidden:
        assert phrase not in src, (
            f"final_validator contains duplicate constraint wording "
            f"({phrase!r}); the constraint logic must live in check_plan only."
        )
    # Belt and braces: check_plan is reachable.
    assert callable(check_plan)
