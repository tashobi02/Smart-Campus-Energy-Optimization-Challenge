from __future__ import annotations
import json
from pathlib import Path

from app.schemas.request import ScenarioRequest
from app.schemas.response import OptimizeResponse

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
