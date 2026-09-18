"""GridWise judge replica — the offline oracle.

Pure standard library on purpose. This package must never import from `app`:
it is the thing that decides whether `app` is correct, so it cannot share
`app`'s bugs, and it has to run on a bare Python with nothing installed.

Run it:  python -m judge tests/fixtures/public_cases.json
"""
from __future__ import annotations

from judge.replay import check_plan, recompute_totals

# Absolute tolerance for every numeric comparison in the harness.
# The public case pack's constraint_reminders fix this at 0.01 kWh / 0.01 BDT.
TOL = 0.01

# Binary floating point puts 38365.01 - 38365.0 at 0.010000000002, a hair
# above TOL. Comparing with a bare `> TOL` therefore rejects a difference the
# spec calls equivalent. A harness stricter than the judge is worse than no
# harness: it sends the team chasing failures that do not exist.
_EPSILON = 1e-9


def within(value: float, other: float = 0.0) -> bool:
    """True when two numbers agree inside the spec's 0.01 absolute tolerance."""
    return abs(value - other) <= TOL + _EPSILON

__all__ = ["TOL", "check_plan", "recompute_totals", "within"]
