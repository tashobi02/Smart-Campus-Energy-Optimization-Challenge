"""Interpretation scoring (PLAN 1.2).

Diffs a returned `directive_interpretation` against the case's ground truth.

Explanation text is never compared. The rubric does not match it
byte-for-byte, and scoring it produces phantom failures that cost hours.
"""
from __future__ import annotations

from typing import Any

from judge import TOL

# The numeric payload keys across all directive types. A key absent from both
# sides is not a disagreement; a key present on only one side is.
NUMERIC_KEYS = ("factor", "minimum_energy_kwh", "max_grid_kwh")

DIMENSIONS = ("coverage", "applies", "type", "hours", "numeric")


def compare_note(
    predicted: dict[str, Any] | None,
    truth: dict[str, Any],
) -> dict[str, bool]:
    """Compare one predicted directive record against its ground truth.

    Returns the four per-note dimensions. `coverage` is a property of the
    list as a whole and so lives in score_interpretation.
    """
    if not isinstance(predicted, dict):
        return {"applies": False, "type": False, "hours": False,
                "numeric": False}

    return {
        "applies": predicted.get("applies") == truth.get("applies"),
        "type": (
            predicted.get("directive_type") == truth.get("directive_type")
        ),
        "hours": (
            _hours(predicted.get("structured_adjustment"))
            == _hours(truth.get("structured_adjustment"))
        ),
        "numeric": _numerics_match(
            predicted.get("structured_adjustment"),
            truth.get("structured_adjustment"),
        ),
    }


def score_interpretation(
    predicted: Any,
    truth: list[dict[str, Any]],
) -> dict[str, bool]:
    """Score a whole `directive_interpretation` list against ground truth.

    Every dimension is an AND across notes: one wrong note fails that
    dimension for the case. When coverage fails the records cannot be
    aligned to notes, so nothing downstream of it can be trusted and every
    dimension reports False.
    """
    if not _has_coverage(predicted, truth):
        return {dimension: False for dimension in DIMENSIONS}

    scores = {dimension: True for dimension in DIMENSIONS}
    for predicted_note, truth_note in zip(predicted, truth):
        for dimension, ok in compare_note(predicted_note, truth_note).items():
            scores[dimension] &= ok
    return scores


def _has_coverage(predicted: Any, truth: list[dict[str, Any]]) -> bool:
    """Exactly one record per note, in note_index order 0..N-1."""
    if not isinstance(predicted, list) or len(predicted) != len(truth):
        return False
    return [
        entry.get("note_index") if isinstance(entry, dict) else None
        for entry in predicted
    ] == list(range(len(truth)))


def _hours(adjustment: Any) -> frozenset[int] | None:
    """The hour set of an adjustment, or None when it carries no hours.

    None and the empty set are kept distinct: "this directive has no hours"
    is a different claim from "this directive applies to no hours".
    """
    if not isinstance(adjustment, dict) or "hours" not in adjustment:
        return None
    hours = adjustment["hours"]
    if not isinstance(hours, list):
        return None
    return frozenset(h for h in hours if isinstance(h, int))


def _numerics_match(predicted: Any, truth: Any) -> bool:
    """Every numeric key on either side must be present on both, within TOL.

    Taking the union rather than only truth's keys catches a spurious
    `factor` invented for a no_charge_window, not just a missing one.
    """
    predicted_values = _numerics(predicted)
    truth_values = _numerics(truth)
    for key in set(predicted_values) | set(truth_values):
        if key not in predicted_values or key not in truth_values:
            return False
        if abs(predicted_values[key] - truth_values[key]) > TOL:
            return False
    return True


def _numerics(adjustment: Any) -> dict[str, float]:
    if not isinstance(adjustment, dict):
        return {}
    return {
        key: float(adjustment[key])
        for key in NUMERIC_KEYS
        if isinstance(adjustment.get(key), (int, float))
        and not isinstance(adjustment[key], bool)
    }
