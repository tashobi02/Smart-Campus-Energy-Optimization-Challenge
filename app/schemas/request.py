from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, model_validator
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
    operator_notes: List[str] = Field(..., min_length=1, max_length=3)
    hours: List[HourEntry] = Field(..., min_length=24, max_length=24)
    battery: BatteryConfig

    @field_validator("operator_notes")
    @classmethod
    def _notes_must_be_non_empty(cls, notes: List[str]) -> List[str]:
        """The spec calls for 1-3 non-empty natural-language strings."""
        for index, note in enumerate(notes):
            if not note.strip():
                raise ValueError(f"operator_notes[{index}] must not be empty")
        return notes

    @model_validator(mode="after")
    def _hours_must_cover_the_day(self) -> "ScenarioRequest":
        """Exactly one entry per hour 0..23 (PLAND2 4.2).

        The length bound alone lets 24 entries all claiming hour 0 through,
        which then fails deep in the optimizer and surfaces as a 500 where
        the spec wants a 400.
        """
        hours = sorted(entry.hour for entry in self.hours)
        if hours != list(range(24)):
            raise ValueError(
                "hours must contain exactly one entry for each hour 0 through 23"
            )
        return self
