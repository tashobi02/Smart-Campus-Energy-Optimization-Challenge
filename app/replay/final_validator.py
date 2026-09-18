from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from app.schemas.request import BatteryConfig, HourEntry
from app.schemas.response import HourlyPlanEntry
from judge.replay import check_plan


def replay(
    plan: Sequence[HourlyPlanEntry],
    hours: Sequence[HourEntry],
    battery: BatteryConfig,
    bundle: Dict[str, Any],
) -> Tuple[bool, List[str]]:
    """Replay the finished schedule before it leaves the service.

    Delegates to the same checker the offline judge harness uses, so the
    in-service guard and the oracle cannot disagree about what is valid.
    """
    errors = check_plan(plan, hours, battery, bundle)
    return not errors, errors
