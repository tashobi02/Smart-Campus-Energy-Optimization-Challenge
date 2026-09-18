"""Fault-injection suite — written against the Problem Statement / Participant
Guide contract, not against the current Phase 0 stub behaviour.

Several of these are expected to FAIL until Phase 4 lands (see PLAN.md §4 and
the fix list in §9): malformed JSON and missing fields currently return 422
instead of 400, a structurally invalid scenario (e.g. 24 duplicate hours)
currently returns 500 instead of 400, and operator_notes has no 1-3 bound yet
so 0 or 5 notes currently return 200 instead of 400. That is intentional —
the fix lands against a test that already exists rather than one written
afterwards to match it.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"
client = TestClient(app)


def _load_cases() -> list[dict]:
    with open(FIXTURES) as f:
        return json.load(f)["cases"]


def _valid_payload() -> dict:
    return copy.deepcopy(_load_cases()[0]["input"])


# ---------------------------------------------------------------------------
# Bad requests — Problem Statement §6.1, Participant Guide §05.
# Each must return the documented status, and the process must stay up.
# ---------------------------------------------------------------------------


def test_malformed_json_returns_400():
    resp = client.post(
        "/optimize-energy",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    resp.json()  # must still be a well-formed JSON error body


def test_missing_required_field_returns_400():
    payload = _valid_payload()
    del payload["battery"]
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_duplicate_hours_returns_400():
    payload = _valid_payload()
    for entry in payload["hours"]:
        entry["hour"] = 0
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_zero_operator_notes_returns_400():
    payload = _valid_payload()
    payload["operator_notes"] = []
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_five_operator_notes_returns_400():
    payload = _valid_payload()
    base_note = payload["operator_notes"][0]
    payload["operator_notes"] = [base_note] * 5
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 400


def test_valid_request_returns_200():
    resp = client.post("/optimize-energy", json=_valid_payload())
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Secret safety — Participant Guide §08. Fix target: app/main.py:82, which
# currently does `detail=str(e)` and leaks internal error text to the client.
# ---------------------------------------------------------------------------


def test_internal_error_does_not_leak_details(monkeypatch):
    secret = "sk-super-secret-token-do-not-leak"  # noqa: S105 - test fixture only

    def _boom(*_args, **_kwargs):
        raise RuntimeError(f"upstream call failed, Authorization: Bearer {secret}")

    monkeypatch.setattr("app.main.solve_best_effort", _boom)

    resp = client.post("/optimize-energy", json=_valid_payload())
    assert resp.status_code == 500
    assert secret not in resp.text
    assert "Authorization" not in resp.text


# ---------------------------------------------------------------------------
# Stability — fire the public cases repeatedly; zero 5xx, zero non-JSON.
# ---------------------------------------------------------------------------


def test_public_cases_are_stable_under_repetition():
    cases = _load_cases()
    for _ in range(3):
        for case in cases:
            resp = client.post("/optimize-energy", json=case["input"])
            assert resp.status_code < 500, f"{case['id']}: {resp.status_code}"
            resp.json()


# ---------------------------------------------------------------------------
# Bad model behaviour — STUBBED.
#
# These require D2's LLM fault-injection hooks (Phase 3-4). Today,
# app/llm/interpreter.py is a pure no_op stub that never calls
# app/llm/client.py, so there is nothing to inject a fault into yet.
# Un-skip and wire these once interpret_notes() actually calls the model.
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="waiting on D2's LLM fault-injection hooks (Phase 4)")
@pytest.mark.parametrize(
    "fault",
    [
        "invalid_json",
        "unsupported_directive_type",
        "hours_out_of_range",  # e.g. [25, -1, 13, 13]
        "factor_out_of_range",  # e.g. 1.7
        "applies_true_with_no_op",
        "timeout",
        "http_500",
    ],
)
def test_bad_model_output_never_returns_5xx(fault):
    """Every injected model fault must yield a 200 with a valid schedule,
    never a 5xx. Wire this to whatever fault-injection hook D2 adds to the
    interpreter/client (e.g. a monkeypatched `complete()` that raises or
    returns garbage for each `fault` case)."""
    raise NotImplementedError(f"fault-injection hook not wired yet: {fault}")
