"""CLI: python -m judge tests/fixtures/public_cases.json

With no --responses, runs in self-check mode: it scores the organizer's own
expected_output as if we had submitted it. That run must come back perfect.
A harness that rejects the organizer's reference answers is too strict, and
will send the whole team chasing failures that are not there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from judge.interpretation import DIMENSIONS
from judge.report import REPLAY_WIRED, aggregate, score_case

_LABELS = {
    "coverage": "cov",
    "applies": "app",
    "type": "typ",
    "hours": "hrs",
    "numeric": "num",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m judge")
    parser.add_argument("cases", type=Path, help="public case pack JSON")
    parser.add_argument(
        "--responses",
        type=Path,
        help=(
            "JSON object mapping case_id to our /optimize-energy response. "
            "Omit to self-check the organizer's expected_output."
        ),
    )
    args = parser.parse_args(argv)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    responses = _load_responses(args.responses)

    if not REPLAY_WIRED:
        print(
            "!! REPLAY UNWIRED: judge/replay.py is missing, so energy balance,\n"
            "!! battery bounds, rate limits, directive windows and end-of-day\n"
            "!! neutrality are NOT being checked. VALID below means schema and\n"
            "!! totals only. D1 owns judge/replay.py (PLAN 1.3).\n"
        )

    results = []
    for case in cases:
        response = (
            case["expected_output"]
            if responses is None
            else responses.get(case["id"])
        )
        result = score_case(case, response)
        results.append(result)
        _print_case(result)

    _print_summary(results)
    return 0 if _is_perfect(results) else 1


def _load_responses(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {item.get("scenario_id"): item for item in data}
    return data


def _print_case(result: dict[str, Any]) -> None:
    flags = " ".join(
        f"{_LABELS[dimension]}={'ok' if result['interpretation'][dimension] else 'NO'}"
        for dimension in DIMENSIONS
    )
    print(
        f"{result['case_id']:<12} {'VALID' if result['valid'] else 'INVALID':<8}"
        f"{flags}   team={result['team_cost']:>10.2f} "
        f"opt={result['optimal_cost']:>10.2f} q={result['quality_ratio']:.4f}"
    )
    for error in result["errors"]:
        print(f"                 - {error}")


def _print_summary(results: list[dict[str, Any]]) -> None:
    scores = aggregate(results)
    valid = sum(1 for result in results if result["valid"])
    checks = len(results) * len(DIMENSIONS)
    passed = sum(
        1 for result in results for ok in result["interpretation"].values() if ok
    )
    # Matches aggregate(): optimization credit requires a valid plan.
    mean_quality = (
        sum(r["quality_ratio"] for r in results if r["valid"]) / len(results)
        if results
        else 0.0
    )

    print()
    print(f"Interpretation {scores['interpretation']:>6.2f} / 25   "
          f"({passed}/{checks} dimension checks)")
    print(f"Application    {scores['application']:>6.2f} / 25   "
          f"({valid}/{len(results)} cases valid)")
    print(f"Optimization   {scores['optimization']:>6.2f} / 10   "
          f"(mean quality_ratio {mean_quality:.4f} over valid cases)")
    print(f"Measured       {scores['total']:>6.2f} / 60   "
          "(API, performance, deployment and docs are not measurable here)")


def _is_perfect(results: list[dict[str, Any]]) -> bool:
    return all(
        result["valid"]
        and all(result["interpretation"].values())
        and result["quality_ratio"] >= 1.0
        for result in results
    )


if __name__ == "__main__":
    sys.exit(main())
