from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

FIXTURES = Path(__file__).parent / "fixtures" / "public_cases.json"


def _sample_input() -> dict:
    with open(FIXTURES) as f:
        return json.load(f)["cases"][0]["input"]


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_startup_is_network_free():
    """PLAND2 5.3 — no provider call in the import path.

    The shared httpx client is built lazily on first call, never at import
    time. A warm-up call in the import path would put a provider round trip
    between container start and the first ready probe.

    Other tests in this module share the same module-level `_client` (which
    is the lazy-build point), so we cannot assert it is None across the
    whole session. Instead we assert the lazy-build *helper* exists and the
    module's globals at import time contain only `_get_client()` — never a
    pre-built client. The contract is "import does not open a socket"; the
    `_get_client` indirection is what guarantees that.
    """
    import subprocess
    import sys

    # Run a fresh interpreter that just imports the module and inspects it.
    # If the import itself built the client, this will be non-None.
    code = (
        "from app.llm import client as llm_client; "
        "import sys; "
        "sys.exit(0 if llm_client._client is None else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        "importing app.llm.client built the httpx client eagerly; "
        "/health would open a socket before serving a request."
    )


# --- 4.1 / 4.2 status codes -------------------------------------------------
# The spec wants 400 for a malformed or structurally invalid request. FastAPI
# defaults to 422, and a scenario that parsed but was nonsense used to reach
# the optimizer and surface as a 500.


def test_malformed_json_returns_400():
    resp = client.post(
        "/optimize-energy",
        content="{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400


def test_missing_required_field_returns_400():
    payload = _sample_input()
    del payload["battery"]
    assert client.post("/optimize-energy", json=payload).status_code == 400


def test_all_hours_zero_returns_400():
    """24 entries that all claim hour 0 — the case that used to be a 500."""
    payload = _sample_input()
    for entry in payload["hours"]:
        entry["hour"] = 0
    assert client.post("/optimize-energy", json=payload).status_code == 400


def test_duplicate_hour_returns_400():
    payload = _sample_input()
    payload["hours"][5]["hour"] = 6
    assert client.post("/optimize-energy", json=payload).status_code == 400


def test_wrong_number_of_hours_returns_400():
    payload = _sample_input()
    payload["hours"] = payload["hours"][:23]
    assert client.post("/optimize-energy", json=payload).status_code == 400


@pytest.mark.parametrize("count", [0, 4])
def test_operator_notes_outside_one_to_three_returns_400(count):
    payload = _sample_input()
    payload["operator_notes"] = ["A note."] * count
    assert client.post("/optimize-energy", json=payload).status_code == 400


@pytest.mark.parametrize("note", ["", "   "])
def test_empty_operator_note_returns_400(note):
    payload = _sample_input()
    payload["operator_notes"] = [note]
    assert client.post("/optimize-energy", json=payload).status_code == 400


@pytest.mark.parametrize("count", [1, 2, 3])
def test_one_to_three_notes_are_accepted(count):
    payload = _sample_input()
    payload["operator_notes"] = ["The corridor lights were serviced."] * count
    resp = client.post("/optimize-energy", json=payload)
    assert resp.status_code == 200
    assert len(resp.json()["directive_interpretation"]) == count


# --- 4.4 secret safety ------------------------------------------------------

SECRET = "sk-live-0123456789abcdef"


def test_internal_error_response_carries_no_detail(monkeypatch):
    """An httpx error can carry the provider URL and the Authorization
    header. None of it may reach the client."""
    def boom(*args, **kwargs):
        raise RuntimeError(
            f"connect to https://openrouter.ai/api/v1/chat/completions "
            f"failed; Authorization: Bearer {SECRET}"
        )

    monkeypatch.setattr("app.main.solve_best_effort", boom)
    resp = client.post("/optimize-energy", json=_sample_input())

    assert resp.status_code == 500
    body = resp.text
    assert SECRET not in body
    assert "openrouter.ai" not in body
    assert "Traceback" not in body
    assert resp.json() == {"detail": "Internal server error"}


# --- 4.5 failure ladder -----------------------------------------------------


def test_model_outage_returns_200_not_5xx(monkeypatch):
    """A provider outage costs interpretation points, never the request."""
    from app.llm.client import LLMUnavailable

    def unavailable(*args, **kwargs):
        raise LLMUnavailable("all model attempts failed (ConnectError)")

    monkeypatch.setattr("app.main.interpret_notes", unavailable)
    payload = _sample_input()
    resp = client.post("/optimize-energy", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["directive_interpretation"]) == len(payload["operator_notes"])
    assert all(
        entry["directive_type"] == "no_op" and entry["applies"] is False
        for entry in body["directive_interpretation"]
    )
    assert len(body["hourly_plan"]) == 24
