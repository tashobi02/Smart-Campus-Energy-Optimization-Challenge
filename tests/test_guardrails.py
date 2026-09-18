"""D1: the guardrail repairs LLM output rather than raising on it.

Every case here would be a 500 under a rejecting validator, which would forfeit
API, schema and reliability credit on top of the affected note.
"""
from __future__ import annotations

import pytest

from app.guardrails.validator import validate_directives
from app.schemas.request import BatteryConfig
from app.schemas.response import directive_adapter

BATTERY = BatteryConfig(
    capacity_kwh=200,
    initial_energy_kwh=100,
    minimum_energy_kwh=40,
    max_charge_kwh_per_hour=50,
    max_discharge_kwh_per_hour=50,
)


def _one(raw, num_notes=1):
    return validate_directives([raw], num_notes, BATTERY)


def _entry(**overrides):
    base = {
        "note_index": 0,
        "applies": True,
        "directive_type": "no_charge_window",
        "structured_adjustment": {"hours": [2, 3]},
        "explanation": "test",
    }
    base.update(overrides)
    return base


def test_valid_directive_passes_through():
    [result] = _one(_entry())
    assert result["directive_type"] == "no_charge_window"
    assert result["structured_adjustment"] == {"hours": [2, 3]}
    assert result["applies"] is True


def test_output_always_validates_against_the_response_schema():
    """Whatever the model sent, the repaired record must be serialisable."""
    for raw in (None, "garbage", {}, _entry(directive_type="teleport")):
        [result] = _one(raw)
        directive_adapter.validate_python(result)


def test_unknown_directive_type_becomes_no_op():
    [result] = _one(_entry(directive_type="shed_load"))
    assert result["directive_type"] == "no_op"
    assert result["applies"] is False
    assert result["structured_adjustment"] is None


def test_no_op_semantics_are_forced():
    [result] = _one(
        _entry(directive_type="no_op", applies=True, structured_adjustment={"hours": [1]})
    )
    assert result["applies"] is False
    assert result["structured_adjustment"] is None


def test_applies_is_forced_true_for_real_directives():
    [result] = _one(_entry(applies=False))
    assert result["applies"] is True


def test_hours_are_deduplicated_filtered_and_sorted():
    [result] = _one(_entry(structured_adjustment={"hours": [5, 25, 3, 3, -1, 0]}))
    assert result["structured_adjustment"]["hours"] == [0, 3, 5]


def test_directive_with_no_usable_hours_becomes_no_op():
    [result] = _one(_entry(structured_adjustment={"hours": [99, -4]}))
    assert result["directive_type"] == "no_op"


def test_factor_is_clamped_into_range():
    for sent, expected in ((1.7, 1.0), (-0.3, 0.0)):
        [result] = _one(
            _entry(
                directive_type="solar_reduction",
                structured_adjustment={"hours": [12], "factor": sent},
            )
        )
        assert result["structured_adjustment"]["factor"] == expected


def test_reserve_above_capacity_is_clamped_to_capacity():
    [result] = _one(
        _entry(
            directive_type="minimum_battery_reserve",
            structured_adjustment={"hours": [18], "minimum_energy_kwh": 9999},
        )
    )
    assert result["structured_adjustment"]["minimum_energy_kwh"] == BATTERY.capacity_kwh


def test_negative_grid_cap_becomes_no_op():
    [result] = _one(
        _entry(
            directive_type="max_grid_window",
            structured_adjustment={"hours": [19], "max_grid_kwh": -5},
        )
    )
    assert result["directive_type"] == "no_op"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None])
def test_non_finite_numbers_become_no_op(bad):
    [result] = _one(
        _entry(
            directive_type="max_grid_window",
            structured_adjustment={"hours": [19], "max_grid_kwh": bad},
        )
    )
    assert result["directive_type"] == "no_op"


def test_extra_adjustment_keys_are_stripped():
    [result] = _one(
        _entry(structured_adjustment={"hours": [2], "factor": 0.5, "nonsense": 1})
    )
    assert result["structured_adjustment"] == {"hours": [2]}


def test_missing_notes_are_filled_with_no_op():
    results = validate_directives([_entry()], 3, BATTERY)
    assert [r["note_index"] for r in results] == [0, 1, 2]
    assert [r["directive_type"] for r in results[1:]] == ["no_op", "no_op"]


def test_duplicate_note_index_keeps_only_the_first():
    results = validate_directives(
        [_entry(), _entry(directive_type="no_discharge_window")], 2, BATTERY
    )
    assert [r["note_index"] for r in results] == [0, 1]
    assert results[0]["directive_type"] == "no_charge_window"
    assert results[1]["directive_type"] == "no_op"


def test_out_of_range_note_index_is_dropped():
    results = validate_directives([_entry(note_index=7)], 1, BATTERY)
    assert results[0]["directive_type"] == "no_op"


def test_non_list_payload_yields_all_no_op():
    results = validate_directives({"oops": True}, 2, BATTERY)
    assert len(results) == 2
    assert all(r["directive_type"] == "no_op" for r in results)


def test_blank_explanation_is_replaced():
    [result] = _one(_entry(explanation="   "))
    assert result["explanation"].strip()
