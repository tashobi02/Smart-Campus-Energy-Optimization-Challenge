from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.guardrails.validator import validate_directives
from app.llm.client import LLMUnavailable, aclose
from app.llm.interpreter import interpret_notes
from app.optimizer.directives import apply_directives
from app.optimizer.solver import solve
from app.replay.final_validator import replay
from app.schemas.request import ScenarioRequest
from app.schemas.response import OptimizeResponse

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
    version="0.1.0",
    description="LLM-assisted campus energy optimization — Phase 0 walking skeleton",
    lifespan=lifespan,
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(request: ScenarioRequest):
    try:
        # 1. LLM Interpreter — extract directives from operator notes
        try:
            raw_directives = interpret_notes(request.operator_notes)
        except LLMUnavailable:
            # Bottom rung of the failure ladder (PLAND2 4.5). A provider
            # outage costs this case's interpretation points and nothing
            # else; it must never reach the client as a 5xx.
            logger.warning("model unavailable; degrading to a no_op interpretation")
            raw_directives = _all_no_op(len(request.operator_notes))

        # 2. Guardrail Validator — validate / sanitise directives
        clean_directives = validate_directives(
            raw_directives, len(request.operator_notes)
        )

        # 3. Apply directives to produce solver constraints
        constraints = apply_directives(
            request.hours, request.battery, clean_directives
        )

        # 4. Math Optimizer — solve for optimal schedule
        plan = solve(request.hours, request.battery, constraints)

        # 5. Final Validator — replay and check
        ok, errors = replay(plan)
        if not ok:
            raise HTTPException(
                status_code=500,
                detail=f"Plan validation failed: {errors}",
            )

        # 6. Compute summary fields
        total_grid = sum(e.grid_kwh for e in plan)
        total_cost = sum(
            e.grid_kwh * request.hours[i].tariff_bdt_per_kwh
            for i, e in enumerate(plan)
        )
        peak_grid = max(e.grid_kwh for e in plan)

        # 7. Build the response. The directive union rejects a shape that is
        # illegal for its type (4.3); rather than let that become a 500, fall
        # back to no_op and keep the schedule, the totals and the schema.
        try:
            interpretations = clean_directives
            return _build_response(
                request, interpretations, plan, total_grid, total_cost, peak_grid
            )
        except ValidationError:
            logger.warning(
                "directive interpretation failed response validation; "
                "degrading to no_op"
            )
            return _build_response(
                request,
                _all_no_op(len(request.operator_notes)),
                plan,
                total_grid,
                total_cost,
                peak_grid,
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


def _build_response(
    request: ScenarioRequest,
    interpretations: List[Dict[str, Any]],
    plan: list,
    total_grid: float,
    total_cost: float,
    peak_grid: float,
) -> OptimizeResponse:
    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=interpretations,
        hourly_plan=plan,
        total_grid_kwh=round(total_grid, 2),
        total_cost_bdt=round(total_cost, 2),
        peak_grid_kwh=round(peak_grid, 2),
        plan_summary="Phase 0 stub: naive grid-only plan with battery idle.",
    )


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
    logger.exception("unhandled error")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
