"""Paraphrase eval runner (PLAND2 3.6).

    python evals/run_eval.py
    python evals/run_eval.py --only clock_words --verbose
    python evals/run_eval.py --limit 10

Scores directive extraction with judge.interpretation -- the same scorer the
judge replica uses, so a green run here means the same thing a green run
there does.

The point of the per-convention breakdown is diagnosis, not a grade. A bare
pass rate tells D1 that something is wrong; "clock_words 7/13" tells them
the prompt cannot read 'from six until nine in the evening', which is a
fixable sentence in the prompt rather than a guessing game.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from judge.interpretation import compare_note  # noqa: E402

BANK = REPO_ROOT / "evals" / "paraphrase_bank.json"
CASES = REPO_ROOT / "tests" / "fixtures" / "public_cases.json"

# PLAND2 3.6 gate. Type + hours + numerics must all match for an exact match;
# `applies` is reported separately because it fails in a different way.
GATE = 0.95
EXACT_MATCH_DIMENSIONS = ("type", "hours", "numeric")


def build_extractor() -> Callable[[list[str], dict[str, Any]], Any]:
    """Adapt to D1's interpreter, whose signature is still settling.

    This is the single place that knows how to call it. D1 owns
    app/llm/interpreter.py; 3.2 adds the battery argument and 3.5 makes it
    async, so both shapes are accepted and neither needs a change here.
    """
    from app.llm import interpreter

    extract_fn = interpreter.interpret_notes
    takes_battery = len(inspect.signature(extract_fn).parameters) > 1

    def extract(notes: list[str], battery: dict[str, Any]) -> Any:
        result = extract_fn(notes, battery) if takes_battery else extract_fn(notes)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        return result

    return extract


def load_batteries() -> dict[str, dict[str, Any]]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    return {case["id"]: case["input"]["battery"] for case in cases}


def run(entries: list[dict[str, Any]], verbose: bool) -> list[dict[str, Any]]:
    extract = build_extractor()
    batteries = load_batteries()
    results = []

    for entry in entries:
        battery = batteries[entry["battery_ref"]]
        try:
            predicted = extract([entry["note"]], battery)
            record = predicted[0] if predicted else None
        except Exception as exc:  # a crash is a failure, not a stopped run
            record = None
            if verbose:
                print(f"  {entry['id']}: extractor raised {type(exc).__name__}")

        dimensions = compare_note(record, entry["truth"])
        results.append(
            {
                "entry": entry,
                "predicted": record,
                "dimensions": dimensions,
                "exact": all(dimensions[d] for d in EXACT_MATCH_DIMENSIONS),
            }
        )
    return results


def report(results: list[dict[str, Any]], verbose: bool) -> bool:
    for result in results:
        entry = result["entry"]
        if result["exact"] and not verbose:
            continue
        status = "ok  " if result["exact"] else "MISS"
        failed = [d for d, ok in result["dimensions"].items() if not ok]
        print(
            f"{status} {entry['id']}  {entry['truth']['directive_type']:<24}"
            f"{','.join(entry['conventions'])}"
        )
        if not result["exact"]:
            print(f"       note      {entry['note']}")
            print(f"       failed    {', '.join(failed)}")
            print(f"       expected  {_summarise(entry['truth'])}")
            print(f"       got       {_summarise(result['predicted'])}")

    exact = sum(1 for result in results if result["exact"])
    total = len(results)
    rate = exact / total if total else 0.0

    print(f"\nExact match  {exact}/{total}  {rate:.1%}   "
          f"(gate {GATE:.0%})  {'PASS' if rate >= GATE else 'FAIL'}")

    _breakdown("By convention", results, lambda r: r["entry"]["conventions"])
    _breakdown(
        "By directive type",
        results,
        lambda r: [r["entry"]["truth"]["directive_type"]],
    )
    _dimension_breakdown(results)
    return rate >= GATE


def _breakdown(
    title: str,
    results: list[dict[str, Any]],
    key: Callable[[dict[str, Any]], list[str]],
) -> None:
    buckets: dict[str, list[bool]] = defaultdict(list)
    for result in results:
        for label in key(result):
            buckets[label].append(result["exact"])

    print(f"\n{title}:")
    rows = sorted(
        buckets.items(), key=lambda kv: (sum(kv[1]) / len(kv[1]), kv[0])
    )
    for label, outcomes in rows:
        passed, total = sum(outcomes), len(outcomes)
        rate = passed / total
        flag = "   <- weakest" if rows and label == rows[0][0] and rate < 1 else ""
        print(f"  {label:<24} {passed:>3}/{total:<3} {rate:>6.1%}{flag}")


def _dimension_breakdown(results: list[dict[str, Any]]) -> None:
    print("\nBy dimension:")
    for dimension in ("applies", "type", "hours", "numeric"):
        passed = sum(1 for r in results if r["dimensions"][dimension])
        print(f"  {dimension:<24} {passed:>3}/{len(results):<3} "
              f"{passed / len(results):>6.1%}")


def _summarise(record: Any) -> str:
    if not isinstance(record, dict):
        return "<nothing>"
    adjustment = record.get("structured_adjustment")
    return f"{record.get('directive_type')} {adjustment}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GridWise paraphrase eval")
    parser.add_argument("--bank", type=Path, default=BANK)
    parser.add_argument(
        "--only", help="run only entries carrying this convention tag"
    )
    parser.add_argument("--limit", type=int, help="run only the first N entries")
    parser.add_argument(
        "--verbose", action="store_true", help="show passing entries too"
    )
    args = parser.parse_args(argv)

    entries = json.loads(args.bank.read_text(encoding="utf-8"))["entries"]
    if args.only:
        entries = [e for e in entries if args.only in e["conventions"]]
    if args.limit:
        entries = entries[: args.limit]

    if not entries:
        print("no entries selected")
        return 1

    print(f"Scoring {len(entries)} paraphrases through app.llm.interpreter\n")
    return 0 if report(run(entries, args.verbose), args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
