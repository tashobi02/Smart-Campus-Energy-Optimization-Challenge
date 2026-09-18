from __future__ import annotations
from pydantic import BaseModel, Field
from typing import List


class HourEntry(BaseModel):
    hour: int = Field(..., ge=0, le=23)
    demand_kwh: float = Field(..., ge=0)
    solar_kwh: float = Field(..., ge=0)
    tariff_bdt_per_kwh: float = Field(..., ge=0)


class BatteryConfig(BaseModel):
    capacity_kwh: float = Field(..., gt=0)
    initial_energy_kwh: float = Field(..., ge=0)
    minimum_energy_kwh: float = Field(..., ge=0)
    max_charge_kwh_per_hour: float = Field(..., ge=0)
    max_discharge_kwh_per_hour: float = Field(..., ge=0)


class ScenarioRequest(BaseModel):
    scenario_id: str
    operator_notes: List[str]
    hours: List[HourEntry] = Field(..., min_length=24, max_length=24)
    battery: BatteryConfig
