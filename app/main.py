from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.guardrails.validator import validate_directives
from app.llm.interpreter import interpret_notes
from app.optimizer.directives import apply_directives
from app.optimizer.solver import solve
from app.replay.final_validator import replay
from app.schemas.request import ScenarioRequest
from app.schemas.response import (
    DirectiveInterpretation,
    OptimizeResponse,
)

app = FastAPI(
    title="GridWise Energy Optimizer",
    version="0.1.0",
    description="LLM-assisted campus energy optimization — Phase 0 walking skeleton",
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
def optimize_energy(request: ScenarioRequest):
    try:
        # 1. LLM Interpreter — extract directives from operator notes
        raw_directives = interpret_notes(request.operator_notes)

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

        # 7. Build directive interpretation response objects
        interpretations = [
            DirectiveInterpretation(**d) for d in clean_directives
        ]

        return OptimizeResponse(
            scenario_id=request.scenario_id,
            directive_interpretation=interpretations,
            hourly_plan=plan,
            total_grid_kwh=round(total_grid, 2),
            total_cost_bdt=round(total_cost, 2),
            peak_grid_kwh=round(peak_grid, 2),
            plan_summary="Phase 0 stub: naive grid-only plan with battery idle.",
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
