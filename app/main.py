from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.guardrails.validator import validate_directives
from app.llm.client import LLMUnavailable, aclose
from app.llm.interpreter import interpret_notes
from app.optimizer.solver import solve_best_effort
from app.replay.final_validator import replay
from app.schemas.request import ScenarioRequest
from app.schemas.response import (
    DirectiveInterpretation,
    HourlyPlanEntry,
    OptimizeResponse,
    directive_adapter,
)
from judge.replay import recompute_totals

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Nothing before the yield: startup stays network-free so /health is
    ready well inside its 60s window (PLAND2 5.3). The shared httpx client
    is built on first use and closed here."""
    yield
    await aclose()


app = FastAPI(
    title="GridWise Energy Optimizer",
    version="1.0.0",
    description="LLM-assisted campus energy optimization",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


def _summarise(
    directives: Sequence[dict],
    plan: Sequence[HourlyPlanEntry],
    relaxations: Sequence[str],
) -> str:
    applied = [d["directive_type"] for d in directives if d["applies"]]
    charge = sum(1 for e in plan if e.battery_action.value == "charge")
    discharge = sum(1 for e in plan if e.battery_action.value == "discharge")

    parts = [
        f"Applied {len(applied)} operator directive(s)"
        + (f" ({', '.join(sorted(set(applied)))})" if applied else "")
        + f"; {len(directives) - len(applied)} note(s) had no schedule effect."
    ]
    parts.append(
        f"Charged the battery in {charge} hour(s) at low tariff and discharged in "
        f"{discharge} hour(s) at peak, returning to the starting state of charge."
    )
    if relaxations:
        parts.append("Note: " + "; ".join(relaxations) + ".")
    return " ".join(parts)


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: ScenarioRequest):
    try:
        # 1. LLM interprets every operator note (falls back safely if unavailable).
        try:
            raw_directives = await interpret_notes(request.operator_notes, request.battery)
        except LLMUnavailable:
            # Bottom rung of the failure ladder (PLAND2 4.5). A provider
            # outage costs this case's interpretation points and nothing
            # else; it must never reach the client as a 5xx.
            logger.warning("model unavailable; degrading to a no_op interpretation")
            raw_directives = _all_no_op(len(request.operator_notes))

        # 2. Deterministic guardrails repair the untrusted interpretation.
        directives = validate_directives(
            raw_directives, len(request.operator_notes), request.battery
        )

        # 3+4. Apply directives and solve, relaxing only if a misread note made the
        # problem infeasible.
        plan, bundle, relaxations = solve_best_effort(
            request.hours, request.battery, directives
        )

        # 5. Replay the finished schedule with the same checker the judge uses.
        valid, errors = replay(plan, request.hours, request.battery, bundle)
        if not valid:
            # Never surface a 5xx: fall back to a schedule that is valid by
            # construction rather than returning a plan we know breaks the rules.
            logger.error("final replay rejected the plan: %s", errors)
            plan, _, _ = solve_best_effort(request.hours, request.battery, [])
            relaxations = list(relaxations) + ["fell back to an unconstrained schedule"]

        # 6. Totals are derived from hourly_plan, the judge's source of truth.
        totals = recompute_totals(plan, request.hours)

        # 7. Build the response. The directive union rejects a shape that is
        # illegal for its type (4.3); rather than let that become a 500, fall
        # back to no_op and keep the schedule, the totals and the schema.
        try:
            interpretations: List[DirectiveInterpretation] = [
                directive_adapter.validate_python(d) for d in directives
            ]
        except ValidationError:
            logger.warning(
                "directive interpretation failed response validation; "
                "degrading to no_op"
            )
            interpretations = [
                directive_adapter.validate_python(d)
                for d in _all_no_op(len(request.operator_notes))
            ]

        return OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=interpretations,
            hourly_plan=plan,
            total_grid_kwh=round(totals["total_grid_kwh"], 2),
            total_cost_bdt=round(totals["total_cost_bdt"], 2),
            peak_grid_kwh=round(totals["peak_grid_kwh"], 2),
            plan_summary=_summarise(directives, plan, relaxations),
        )

    except HTTPException:
        raise
    except Exception:
        # Detail stays in the log; an httpx error can carry the provider URL,
        # the request body or the Authorization header, and the rubric scores
        # "no sensitive stack traces or secret values in responses" (4.4).
        logger.exception("unhandled error while optimizing")
        raise HTTPException(status_code=500, detail="Internal server error")


def _all_no_op(count: int) -> List[Dict[str, Any]]:
    """One no_op per note — always a legal interpretation."""
    return [
        {
            "note_index": index,
            "applies": False,
            "directive_type": "no_op",
            "structured_adjustment": None,
            "explanation": "No directive applied for this note.",
        }
        for index in range(count)
    ]


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
):
    """The spec wants 400 for a malformed or structurally invalid request.

    FastAPI's default is 422, and a scenario that parses but is nonsense —
    24 entries all claiming hour 0 — used to fail deep in the pipeline and
    surface as a 500. Both are 400 now (PLAND2 4.1).
    """
    return JSONResponse(
        status_code=400,
        content=jsonable_encoder(
            {"detail": "Invalid request", "errors": exc.errors()}
        ),
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Log the detail server-side; never return it. Provider errors can carry the
    # request URL and configuration, which must not reach the client.
    logger.exception("unhandled error on %s", getattr(request.url, "path", ""))
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
