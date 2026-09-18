from __future__ import annotations
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas.response import OptimizeResponse

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"
client = TestClient(app)


def _load_cases():
    with open(FIXTURES) as f:
        data = json.load(f)
    return data["cases"]


@pytest.fixture(params=_load_cases(), ids=lambda c: c["id"])
def case(request):
    return request.param


def test_public_case_returns_valid_schema(case):
    """POST each public case and assert the response validates against the
    response schema. Correctness (optimal cost, correct directives) is NOT
    checked — that's Phase 1+."""
    resp = client.post("/optimize-energy", json=case["input"])
    assert resp.status_code == 200, f"Case {case['id']}: {resp.text}"

    body = resp.json()
    # Must parse without error
    parsed = OptimizeResponse(**body)

    # Basic sanity
    assert parsed.scenario_id == case["input"]["scenario_id"]
    assert len(parsed.hourly_plan) == 24
    assert len(parsed.directive_interpretation) == len(
        case["input"]["operator_notes"]
    )
    assert parsed.total_grid_kwh >= 0
    assert parsed.total_cost_bdt >= 0
    assert parsed.peak_grid_kwh >= 0
