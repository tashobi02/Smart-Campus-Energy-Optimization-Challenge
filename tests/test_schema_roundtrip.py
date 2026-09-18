from __future__ import annotations
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.request import ScenarioRequest
from app.schemas.response import OptimizeResponse, directive_adapter

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"


def _load_cases():
    with open(FIXTURES) as f:
        data = json.load(f)
    return data["cases"]


def test_request_schema_parses_all_cases():
    """Every public case input must parse into a valid ScenarioRequest."""
    for case in _load_cases():
        req = ScenarioRequest(**case["input"])
        assert req.scenario_id == case["input"]["scenario_id"]


def test_response_schema_parses_all_cases():
    """Every public case expected_output must parse into a valid OptimizeResponse."""
    for case in _load_cases():
        resp = OptimizeResponse(**case["expected_output"])
        assert resp.scenario_id == case["expected_output"]["scenario_id"]


def test_every_reference_directive_parses():
    """The union must accept all six directive types as the organizer writes
    them. A union that rejects the reference answers is worse than the loose
    dict it replaced."""
    seen = set()
    for case in _load_cases():
        for directive in case["expected_output"]["directive_interpretation"]:
            parsed = directive_adapter.validate_python(directive)
            seen.add(parsed.directive_type)
    assert seen == {
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
        "no_op",
    }


# --- 4.3: shapes that the loose Dict used to accept -------------------------


def _directive(**overrides):
    base = {
        "note_index": 0,
        "applies": True,
        "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": [2, 3]},
        "explanation": "x",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "directive, why",
    [
        (
            _directive(structured_adjustment={"hours": [2, 3], "factor": 0.5}),
            "a factor on a no_charge_window",
        ),
        (
            _directive(
                directive_type="solar_reduction",
                structured_adjustment={"hours": [12]},
            ),
            "a solar_reduction with no factor",
        ),
        (
            _directive(
                directive_type="solar_reduction",
                structured_adjustment={"hours": [12], "factor": 1.4},
            ),
            "a factor above 1",
        ),
        (
            _directive(
                directive_type="minimum_battery_reserve",
                structured_adjustment={"hours": [18], "max_grid_kwh": 100},
            ),
            "a reserve carrying a grid cap instead of an energy floor",
        ),
        (
            _directive(
                directive_type="max_grid_window",
                structured_adjustment={"hours": [18], "max_grid_kwh": -5},
            ),
            "a negative grid cap",
        ),
        (
            _directive(
                directive_type="no_op",
                applies=False,
                structured_adjustment={"hours": [5]},
            ),
            "a no_op carrying an adjustment",
        ),
        (
            _directive(directive_type="no_op", applies=True),
            "a no_op that claims to apply",
        ),
        (
            _directive(applies=False),
            "a non-no_op that claims not to apply",
        ),
        (
            _directive(directive_type="solar_dimming"),
            "a directive type outside the enum",
        ),
        (
            _directive(structured_adjustment={"hours": [2, 99]}),
            "an hour outside 0..23",
        ),
    ],
)
def test_illegal_directive_shapes_are_rejected(directive, why):
    with pytest.raises(ValidationError):
        directive_adapter.validate_python(directive)
    assert why  # names the shape in the failure output
