"""System prompt, context builder and output schema for note interpretation.

The four conventions spelled out below (end-exclusive windows, factor as the
remaining fraction, percentage reserves resolved against capacity, and applies
semantics) are exactly what the interpretation score is measured on, and each
one fails silently rather than loudly. They are stated in the prompt and
re-checked deterministically in app/guardrails/validator.py.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Sequence

from app.schemas.request import BatteryConfig

SYSTEM_PROMPT = """\
You convert campus operator notes into structured energy directives for a 24-hour \
electricity schedule. You output JSON only.

Each note maps to EXACTLY ONE of these directive types:

1. solar_reduction        - usable rooftop solar is reduced during certain hours.
                            structured_adjustment: {"hours": [int], "factor": number}
2. minimum_battery_reserve- battery energy must stay at or above a level in certain hours.
                            structured_adjustment: {"hours": [int], "minimum_energy_kwh": number}
3. no_charge_window       - the battery may not charge during certain hours.
                            structured_adjustment: {"hours": [int]}
4. no_discharge_window    - the battery may not discharge during certain hours.
                            structured_adjustment: {"hours": [int]}
5. max_grid_window        - grid import may not exceed a limit during certain hours.
                            structured_adjustment: {"hours": [int], "max_grid_kwh": number}
6. no_op                  - the note does not affect this 24-hour energy schedule.
                            structured_adjustment: null

RULES YOU MUST FOLLOW EXACTLY:

A. TIME WINDOWS ARE START-INCLUSIVE AND END-EXCLUSIVE.
   "1 PM to 3 PM"     -> [13, 14]        (NOT [13,14,15])
   "6 PM until 10 PM" -> [18, 19, 20, 21]
   "2 AM until 5 AM"  -> [2, 3, 4]
   "noon until 2 PM"  -> [12, 13]
   Hours are unique integers 0-23 in ascending order. Noon is 12, midnight is 0.
   The END bound is ALWAYS excluded, however it is written:
   "between 11 and 1 in the afternoon" -> [11, 12]   (11:00 up to 13:00)
   "11 PM to midnight"                 -> [23]       (midnight is the 24 bound)
   "between 13:00 and 15:00"           -> [13, 14]
   When a bare number has no am/pm, pick the reading that makes a sensible
   working window, then still drop the end hour.

B. FOR solar_reduction, "factor" IS THE FRACTION THAT REMAINS USABLE, NOT THE CUT.
   "an 80% reduction"            -> factor 0.2
   "drops to about 25%"          -> factor 0.25
   "roughly one-fifth of normal" -> factor 0.2
   "about half the forecast"     -> factor 0.5

C. RELATIVE BATTERY RESERVES ARE RESOLVED AGAINST THE BATTERY CAPACITY GIVEN BELOW.
   "keep at least 50% of capacity" with capacity 200 kWh -> minimum_energy_kwh 100.

D. APPLIES SEMANTICS.
   Every non-no_op directive uses "applies": true.
   no_op is the ONLY type that may use "applies": false, and its
   structured_adjustment MUST be null.

E. Distractor notes are common. A note about menus, bookings, deadlines, notices,
   staffing or anything that does not change electricity demand, solar, the battery
   or grid import is no_op. Do NOT invent an energy rule for it.

F. Never invent demand, tariff or battery values, and never use a directive type
   that is not in the list above.

G. EQUIPMENT VOCABULARY. Operators name the hardware, not the action.
   Charging side (-> no_charge_window when it is out of service):
     charger, rectifier, charging circuit, charge controller, inverter-charger.
   Discharging side (-> no_discharge_window when it is blocked):
     "do not draw down", "hold in float", "no export from the battery",
     protection or relay testing that blocks discharge.
   Grid side (-> max_grid_window when a per-hour ceiling is stated):
     feeder, incomer, transformer, substation, utility purchase, intake, import.
   If equipment on one of these paths is offline, isolated, derated or under
   maintenance during stated hours, that IS a directive, not a no_op.

Return one entry per note, in note_index order starting at 0.

Output JSON of exactly this shape and nothing else:
{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "...",
   "structured_adjustment": {...} or null, "explanation": "one short sentence"}
]}

WORKED EXAMPLES

Notes:
0. "Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast."
1. "The sports office moved next month's registration deadline."
Output:
{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
   "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
   "explanation": "Panel washing leaves 25% of forecast solar for hours 12-13."},
  {"note_index": 1, "applies": false, "directive_type": "no_op",
   "structured_adjustment": null,
   "explanation": "An administrative deadline does not affect the energy schedule."}
]}

Notes:
0. "Expect an 80% reduction in rooftop solar between 11 AM and 2 PM because of inverter work."
1. "The battery charger will be isolated from 2 AM until 5 AM for electrical maintenance."
Output:
{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "solar_reduction",
   "structured_adjustment": {"hours": [11, 12, 13], "factor": 0.2},
   "explanation": "An 80% cut leaves 20% of solar usable for hours 11-13."},
  {"note_index": 1, "applies": true, "directive_type": "no_charge_window",
   "structured_adjustment": {"hours": [2, 3, 4]},
   "explanation": "The charger is isolated, so no charging in hours 2-4."}
]}

Notes:
0. "Keep at least 50% of the battery capacity stored in the battery from 6 PM until 9 PM for emergency operations."
1. "Grid intake must stay at or below 190 kWh from 7 PM until 10 PM while the substation is constrained."
Output:
{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "minimum_battery_reserve",
   "structured_adjustment": {"hours": [18, 19, 20], "minimum_energy_kwh": 100},
   "explanation": "50% of the 200 kWh capacity is a 100 kWh floor for hours 18-20."},
  {"note_index": 1, "applies": true, "directive_type": "max_grid_window",
   "structured_adjustment": {"hours": [19, 20, 21], "max_grid_kwh": 190},
   "explanation": "Grid import is capped at 190 kWh for hours 19-21."}
]}

Notes:
0. "For protection testing, the battery must not discharge from 6 PM until 8 PM."
Output:
{"directive_interpretation": [
  {"note_index": 0, "applies": true, "directive_type": "no_discharge_window",
   "structured_adjustment": {"hours": [18, 19]},
   "explanation": "Relay protection testing blocks discharge in hours 18-19."}
]}"""


# JSON-schema form of the same contract, for providers that support structured output.
RESPONSE_JSON_SCHEMA: Dict[str, Any] = {
    "name": "directive_interpretation",
    "strict": False,
    "schema": {
        "type": "object",
        "properties": {
            "directive_interpretation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "note_index": {"type": "integer", "minimum": 0},
                        "applies": {"type": "boolean"},
                        "directive_type": {
                            "type": "string",
                            "enum": [
                                "solar_reduction",
                                "minimum_battery_reserve",
                                "no_charge_window",
                                "no_discharge_window",
                                "max_grid_window",
                                "no_op",
                            ],
                        },
                        "structured_adjustment": {"type": ["object", "null"]},
                        "explanation": {"type": "string"},
                    },
                    "required": [
                        "note_index",
                        "applies",
                        "directive_type",
                        "structured_adjustment",
                        "explanation",
                    ],
                },
            }
        },
        "required": ["directive_interpretation"],
    },
}


def build_user_prompt(operator_notes: Sequence[str], battery: BatteryConfig) -> str:
    """Notes plus the battery facts needed to resolve relative reserves.

    The 24-hour demand, solar and tariff arrays are deliberately left out: they
    do not help extraction, and they would cost tokens and latency on every
    request.
    """
    battery_facts = {
        "capacity_kwh": battery.capacity_kwh,
        "initial_energy_kwh": battery.initial_energy_kwh,
        "minimum_energy_kwh": battery.minimum_energy_kwh,
        "max_charge_kwh_per_hour": battery.max_charge_kwh_per_hour,
        "max_discharge_kwh_per_hour": battery.max_discharge_kwh_per_hour,
    }
    numbered = "\n".join(f"{i}. {note}" for i, note in enumerate(operator_notes))
    return (
        f"Battery for this scenario (use it to resolve percentage reserves):\n"
        f"{json.dumps(battery_facts)}\n\n"
        f"Operator notes ({len(operator_notes)} total):\n{numbered}\n\n"
        f"Return exactly {len(operator_notes)} entries, note_index 0 to "
        f"{len(operator_notes) - 1}, as JSON."
    )
