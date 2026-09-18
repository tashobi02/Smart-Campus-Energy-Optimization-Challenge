from __future__ import annotations
from enum import Enum
from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class DirectiveType(str, Enum):
    solar_reduction = "solar_reduction"
    minimum_battery_reserve = "minimum_battery_reserve"
    no_charge_window = "no_charge_window"
    no_discharge_window = "no_discharge_window"
    max_grid_window = "max_grid_window"
    no_op = "no_op"


class BatteryAction(str, Enum):
    charge = "charge"
    discharge = "discharge"
    idle = "idle"


# --- structured_adjustment shapes, one per directive type -------------------
# Hour values are bounded, but ordering and uniqueness are deliberately NOT
# enforced here: the guardrail sorts and de-duplicates (PLAND1 2.1), and
# rejecting at the schema turns a guardrail miss into a 500, which costs more
# than the malformed directive itself.

DirectiveHours = List[Annotated[int, Field(ge=0, le=23)]]


class _Adjustment(BaseModel):
    """Extra keys are refused: a factor on a no_charge_window is exactly the
    illegal-for-this-type shape the loose Dict used to wave through."""

    model_config = ConfigDict(extra="forbid")


class SolarReductionAdjustment(_Adjustment):
    hours: DirectiveHours
    factor: float = Field(..., ge=0, le=1)


class MinimumReserveAdjustment(_Adjustment):
    hours: DirectiveHours
    minimum_energy_kwh: float = Field(..., ge=0)


class MaxGridAdjustment(_Adjustment):
    hours: DirectiveHours
    max_grid_kwh: float = Field(..., ge=0)


class WindowAdjustment(_Adjustment):
    """no_charge_window and no_discharge_window carry hours and nothing else."""

    hours: DirectiveHours


class _DirectiveBase(BaseModel):
    note_index: int = Field(..., ge=0)
    explanation: str


class SolarReductionDirective(_DirectiveBase):
    applies: Literal[True]
    directive_type: Literal["solar_reduction"]
    structured_adjustment: SolarReductionAdjustment


class MinimumReserveDirective(_DirectiveBase):
    applies: Literal[True]
    directive_type: Literal["minimum_battery_reserve"]
    structured_adjustment: MinimumReserveAdjustment


class NoChargeWindowDirective(_DirectiveBase):
    applies: Literal[True]
    directive_type: Literal["no_charge_window"]
    structured_adjustment: WindowAdjustment


class NoDischargeWindowDirective(_DirectiveBase):
    applies: Literal[True]
    directive_type: Literal["no_discharge_window"]
    structured_adjustment: WindowAdjustment


class MaxGridWindowDirective(_DirectiveBase):
    applies: Literal[True]
    directive_type: Literal["max_grid_window"]
    structured_adjustment: MaxGridAdjustment


class NoOpDirective(_DirectiveBase):
    applies: Literal[False]
    directive_type: Literal["no_op"]
    structured_adjustment: None = None


# Discriminated union keyed on directive_type (PLAND2 4.3). The old loose
# Dict[str, Union[List[int], float, int]] accepted a factor on a
# no_charge_window and a no_op carrying an adjustment. Pairing each type with
# its own shape -- and pinning `applies` to a Literal per type -- makes those
# combinations unserialisable rather than merely wrong.
DirectiveInterpretation = Annotated[
    Union[
        SolarReductionDirective,
        MinimumReserveDirective,
        NoChargeWindowDirective,
        NoDischargeWindowDirective,
        MaxGridWindowDirective,
        NoOpDirective,
    ],
    Field(discriminator="directive_type"),
]

directive_adapter: TypeAdapter[DirectiveInterpretation] = TypeAdapter(
    DirectiveInterpretation
)


class HourlyPlanEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    grid_kwh: float = Field(..., ge=0)
    solar_used_kwh: float = Field(..., ge=0)
    battery_action: BatteryAction
    battery_kwh: float = Field(..., ge=0)
    battery_energy_after_kwh: float = Field(..., ge=0)


class OptimizeResponse(BaseModel):
    scenario_id: str
    directive_interpretation: List[DirectiveInterpretation]
    hourly_plan: List[HourlyPlanEntry] = Field(..., min_length=24, max_length=24)
    total_grid_kwh: float = Field(..., ge=0)
    total_cost_bdt: float = Field(..., ge=0)
    peak_grid_kwh: float = Field(..., ge=0)
    plan_summary: str
