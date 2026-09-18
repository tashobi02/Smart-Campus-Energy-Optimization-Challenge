"""D1: defensive parsing of model output, and the outage-path backup parser."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.llm import interpreter
from app.llm.interpreter import LLMUnavailable, fallback_parse
from app.schemas.request import BatteryConfig

BATTERY = BatteryConfig(
    capacity_kwh=200,
    initial_energy_kwh=100,
    minimum_energy_kwh=40,
    max_charge_kwh_per_hour=50,
    max_discharge_kwh_per_hour=50,
)

PAYLOAD = {
    "directive_interpretation": [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "structured_adjustment": {"hours": [2, 3, 4]},
            "explanation": "charger isolated",
        }
    ]
}


def _interpret(notes):
    return asyncio.run(interpreter.interpret_notes(notes, BATTERY))


def _with_reply(monkeypatch, text):
    async def fake(system, user, *, json_schema=None):
        return text

    monkeypatch.setattr(interpreter, "complete", fake)


@pytest.mark.parametrize(
    "reply",
    [
        json.dumps(PAYLOAD),
        f"```json\n{json.dumps(PAYLOAD)}\n```",
        f"Here you go:\n```\n{json.dumps(PAYLOAD)}\n```\nHope that helps.",
        json.dumps(PAYLOAD["directive_interpretation"]),  # bare list
    ],
)
def test_parses_model_output_in_several_wrappings(monkeypatch, reply):
    _with_reply(monkeypatch, reply)
    [result] = _interpret(["The charger is isolated from 2 AM until 5 AM."])
    assert result["directive_type"] == "no_charge_window"
    assert result["structured_adjustment"]["hours"] == [2, 3, 4]


def test_unparseable_output_falls_back_rather_than_raising(monkeypatch):
    _with_reply(monkeypatch, "I'm afraid I can't help with that.")
    results = _interpret(["Do not charge the battery between 2 PM and 4 PM."])
    assert len(results) == 1


def test_provider_outage_falls_back_rather_than_raising(monkeypatch):
    async def fake(system, user, *, json_schema=None):
        raise LLMUnavailable("provider down")

    monkeypatch.setattr(interpreter, "complete", fake)
    results = _interpret(["Do not charge the battery between 2 PM and 4 PM."])
    assert results[0]["directive_type"] == "no_charge_window"


def test_empty_notes_short_circuits():
    assert _interpret([]) == []


# The four conventions the interpretation score is measured on.

def test_windows_are_start_inclusive_and_end_exclusive():
    [result] = fallback_parse(["Do not charge the battery from 1 PM to 3 PM."], BATTERY)
    assert result["structured_adjustment"]["hours"] == [13, 14]


def test_factor_is_the_remaining_fraction_not_the_cut():
    [result] = fallback_parse(
        ["Expect an 80% reduction in rooftop solar between 11 AM and 2 PM."], BATTERY
    )
    assert result["structured_adjustment"]["factor"] == pytest.approx(0.2)


def test_drop_to_wording_reads_as_the_remaining_fraction():
    [result] = fallback_parse(
        ["Solar output will drop to about 25% from noon until 2 PM."], BATTERY
    )
    assert result["structured_adjustment"]["factor"] == pytest.approx(0.25)


def test_percentage_reserve_resolves_against_capacity():
    [result] = fallback_parse(
        ["Keep at least 50% of the battery capacity from 6 PM until 9 PM."], BATTERY
    )
    assert result["directive_type"] == "minimum_battery_reserve"
    assert result["structured_adjustment"]["minimum_energy_kwh"] == pytest.approx(100.0)


def test_distractor_notes_are_no_op():
    for note in (
        "The cafeteria menu changes tomorrow.",
        "The sports office moved next month's registration deadline.",
        "The library is extending book-return hours next week.",
    ):
        [result] = fallback_parse([note], BATTERY)
        assert result["directive_type"] == "no_op"
        assert result["applies"] is False
        assert result["structured_adjustment"] is None
