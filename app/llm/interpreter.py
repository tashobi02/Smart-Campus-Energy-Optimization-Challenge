from __future__ import annotations
from typing import Any, Dict, List


def interpret_notes(operator_notes: List[str]) -> List[Dict[str, Any]]:
    """Interpret operator notes into raw directive dicts.

    Phase 0: returns no_op for every note — makes the pipeline callable
    end-to-end before any real prompt exists.
    """
    directives: List[Dict[str, Any]] = []
    for idx, _note in enumerate(operator_notes):
        directives.append(
            {
                "note_index": idx,
                "applies": False,
                "directive_type": "no_op",
                "structured_adjustment": None,
                "explanation": "Phase 0 stub: note not yet interpreted.",
            }
        )
    return directives
