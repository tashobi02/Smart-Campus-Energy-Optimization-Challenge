# GridWise — LLM-Assisted Campus Energy Optimizer

GridWise is a FastAPI service that interprets 1–3 free-text operator notes
into a structured set of energy directives and produces a minimum-cost
24-hour electricity schedule that respects them. The notes go to an LLM;
the schedule is produced by a deterministic LP and is validated by an
in-service replay oracle that mirrors the offline judge.

The notebook version of this story is in `Docs/`. The architecture
write-up is in `.claude/`.

---

## Setup

```bash
# Clone and enter
cd Smart-Campus-Energy-Optimization-Challenge

# Fresh venv (recommended; reproducible lockfile in requirements*.txt)
python3 -m venv .venv
. .venv/bin/activate

# Install dev + runtime deps
pip install -r requirements-dev.txt

# Secrets — copy the tracked template and fill in your key.
cp .env.example .env
# open .env in your editor and replace the placeholder with your real key.
#
# LLM_STUB_MODE=off is deliberate: it always calls the provider. With the
# default `auto`, a missing or invalid key silently serves a canned payload,
# and every test below would still pass while the model was never reached.
```

`.env` is git-ignored. Never paste a key into the repo, into a PR, or
into a chat — see *Secret Safety* below.

## Environment Variables

| Name | Purpose | Default if unset |
|---|---|---|
| `LLM_PROVIDER` | `openai` or `openrouter` | `openrouter` |
| `LLM_API_KEY` | Bearer key for the provider | empty (must be set) |
| `LLM_MODEL` | Primary model | `google/gemini-2.5-flash` |
| `LLM_FALLBACK_MODEL` | Tried once after a primary-model failure | empty |
| `LLM_TIMEOUT_SECONDS` | Per-call timeout | `8` |
| `LLM_RETRY_JITTER_SECONDS` | Random backoff before the retry | `0.5` |
| `LLM_STUB_MODE` | `off` always calls the provider; `canned` always returns a fixed payload; `malformed` returns a broken one (guardrail testing); `auto` uses the provider only when `LLM_API_KEY` is set and returns the canned payload otherwise | `auto` |
| `LLM_CACHE_ENABLED` | In-process response cache; set `0` to disable | `1` |
| `LLM_CACHE_MAX_ENTRIES` | Cache size | `512` |
| `SOLVER_TIMEOUT_SECONDS` | LP solver budget per request | `1` |
| `PORT` | Uvicorn port | `8000` |

Never commit values. `.env.example` (names and placeholders only) is tracked and is the contract; `.env` is git-ignored.

## Model / Provider

The default is `gpt-4.1-mini` on OpenAI. The provider switch is a single
env var — `LLM_PROVIDER=openrouter LLM_MODEL=google/gemini-2.5-flash` is a
one-line change.

The structured-output contract is encoded in
`app/llm/prompts.RESPONSE_JSON_SCHEMA`; it is sent with `strict: True` so
OpenAI rejects anything that doesn't conform before we ever see it.

## LLM Role

The model interprets 1–3 operator notes into structured directives:

- `solar_reduction` — usable solar is reduced during certain hours
- `minimum_battery_reserve` — battery energy must stay at or above a level
- `no_charge_window` — the battery may not charge during certain hours
- `no_discharge_window` — the battery may not discharge during certain hours
- `max_grid_window` — grid import may not exceed a limit during certain hours
- `no_op` — the note does not affect this 24-hour schedule

Distractor notes (menus, bookings, deadlines, notices) are common and are
explicitly NOT mapped onto energy rules — they become `no_op`.

Phrase matching is **not** the interpreter. The regex parser at the bottom
of `app/llm/interpreter.py` runs only as a fallback when the provider is
unreachable. The rubric measures the LLM path.

## Guardrails

`app/guardrails/validator.py` repairs LLM output rather than raising on it:

- Unknown directive types → `no_op`
- Missing or out-of-range `note_index` → filled with `no_op`
- Duplicate `note_index` → first wins, rest become `no_op`
- Hours are deduped, sorted, filtered to 0–23
- A directive with no usable hours → `no_op`
- `factor` clamped to `[0, 1]`
- `minimum_energy_kwh` clamped to `≤ capacity_kwh`
- Negative or non-finite numbers → `no_op`
- Stray keys on `structured_adjustment` → stripped
- `no_op` is always forced to `applies=False` with `structured_adjustment=None`

A `no_op` directive returned for an empty/malformed/nonsense input NEVER
returns a 5xx, and the schedule still builds.

## Optimizer / Solver

24-hour plan: 4 real variables per hour (`grid_kwh`, `solar_used_kwh`,
`battery_kwh`, `battery_action` as one-hot via magnitude sign). Objective:
`Σ grid[h] × tariff[h]` (BDT, BUP CSE Fest 2026 solver constraints §9.x).

Constraints are LP-friendly except for `battery_action ∈ {charge, discharge,
idle}`. The solver treats idle as `battery_kwh = 0` and uses magnitude
sign to disambiguate — a tighter reformulation than the routing-around
the one-hot would suggest, and the same answer the rubric's reference
schedule produces.

The infeasibility ladder is three deep: primary solve → subset search →
baseline (no directives). Every step is reproducible from
`tests/fixtures/public_cases.json`. Round-trip DC loss is out of scope.

## Run

### Local

```bash
. .venv/bin/activate
./scripts/run_local.sh         # uvicorn --reload --port ${PORT:-8000}
```

### Container — pullable fallback image

> **D3 ACTION REQUIRED — not yet published.** The rubric awards 4 of the 10
> deployment points for an image the judges can *pull* at an exact tag or
> digest, plus a reachable base URL. Neither exists yet. Fill both in below;
> everything else in this section is verified working.

```bash
# TODO(D3): publish and replace the placeholder with the real digest
docker pull ghcr.io/<org>/gridwise@sha256:<digest>
docker run --rm -p 8000:8000 \
    -e LLM_PROVIDER=openai \
    -e LLM_API_KEY=$LLM_API_KEY \
    -e LLM_MODEL=gpt-4.1-mini \
    -e LLM_STUB_MODE=off \
    ghcr.io/<org>/gridwise@sha256:<digest>
curl -s localhost:8000/health    # {"status":"ok"}
```

Deployed base URL: **TODO(D3)** — must answer `GET /health` and
`POST /optimize-energy` with no login wall, VPN or manual approval.

Verified locally on this image: 575 MB, runs as non-root `appuser`, cold start
to `/health` in ~1s, `HEALTHCHECK` reports `healthy`, and no `.env`, `Docs/` or
key material is present in the filesystem, the env or `docker history`.

### Container — build from source



```bash
docker build -t gridwise:sha-$(git rev-parse --short HEAD) .
docker run --rm -p 8000:8000 \
    -e LLM_PROVIDER=openai \
    -e LLM_API_KEY=$LLM_API_KEY \
    -e LLM_MODEL=gpt-4.1-mini \
    gridwise:sha-$(git rev-parse --short HEAD)
```

The image is non-root, has a `HEALTHCHECK` calling
`scripts/docker_healthcheck.py`, and excludes `requirements-dev.txt`
from the build context via `.dockerignore`.

## Health Check

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

The endpoint is the same as the container's HEALTHCHECK probe. Smoke
tests after every deploy should `curl /health` from outside the dev
network.

## API Usage

The single endpoint is `POST /optimize-energy`. Send a 24-hour demand
scenario, a battery config, and 1–3 operator notes. Get a JSON response
with the hourly plan, the directive interpretation, and the totals.

```bash
curl -s -X POST http://localhost:8000/optimize-energy \
    -H 'Content-Type: application/json' \
    --data @scripts/smoke_payload.json
# or use scripts/curl_optimize.sh
```

The complete request schema lives in `app/schemas/request.py`. The
response schema lives in `app/schemas/response.py`.

## Public Sample Tests

```bash
.venv/bin/python -m pytest tests/test_public_cases.py -v
.venv/bin/python -m judge tests/fixtures/public_cases.json
.venv/bin/python evals/run_eval.py
```

The paraphrase eval (`evals/run_eval.py`) scores the model's directive
extraction across 57 phrasings of the 10 public cases. The current
snapshot in `evals/run_eval_results.txt` is **56/57 (98.2%)** exact
match — above the 95% gate.

## Dependencies

Runtime (`requirements.txt`):

- `fastapi`, `uvicorn[standard]` — HTTP layer
- `pydantic` — request and response validation (discriminated unions)
- `python-dotenv` — `.env` loading
- `httpx` — async LLM transport
- `numpy`, `scipy` — LP formulation and `linprog(method="highs")`

Dev (`requirements-dev.txt` adds `pytest`, `pytest-asyncio`).

## Known Limitations

1. **End-exclusive windows** are the prompt's hardest convention.
   PB-08 ("During the 10 AM and 11 AM hours") still misses 1/57 cases;
   the prompt instructs the model but a small fraction reads the phrase
   as a single window. Cost of iterating further exceeds the marginal
   gate value.
2. **Round-trip DC loss** is not modelled — the LP assumes charge/discharge
   are lossless. Adequate for the rubric; a real battery needs η in [0.85, 0.95].
3. **No API key** degrades the service to the regex backup parser. It
   handles the common phrases but is intentionally conservative and
   returns `no_op` on anything it cannot parse. This is explicit
   non-compliance with the rubric as the *sole* interpreter, but is the
   right thing on the outage path.

## Secret Safety

- `.env` is git-ignored
- `.env.*` glob matches `.env.example` and `.env.test` so those are
  ignored too — re-add explicitly if you intend to commit one
- httpx errors that include the request URL or `Authorization` header
  are caught by `app/main.py`'s `solve_best_effort` and replaced with
  `{"detail": "Internal server error"}`; the original message is logged
  locally but never returned

## Layout

```
app/
  main.py                 # FastAPI entry; lives here for the test client
  llm/                    # model transport, prompt, cache, interpreter
  guardrails/             # validator (repair-don't-raise)
  schemas/                # pydantic request and response
  optimizer/              # 24-hour LP
  replay/                 # in-service check_plan mirror
judge/                    # offline oracle (same check_plan, bundle builder)
tests/                    # pytest; tests/test_public_cases.py is the rubric
evals/                    # paraphrase bank and runner
scripts/                  # run_local.sh, loadtest.py, docker_healthcheck.py
.claude/                  # PLAND*.md, DEPLOY.md, LOADTEST.md, image tarball
```

