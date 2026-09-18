"""GridWise judge replica — the offline oracle.

Pure standard library on purpose. This package must never import from `app`:
it is the thing that decides whether `app` is correct, so it cannot share
`app`'s bugs, and it has to run on a bare Python with nothing installed.

Run it:  python -m judge tests/fixtures/public_cases.json
"""

# Absolute tolerance for every numeric comparison in the harness.
# The public case pack's constraint_reminders fix this at 0.01 kWh / 0.01 BDT.
TOL = 0.01
