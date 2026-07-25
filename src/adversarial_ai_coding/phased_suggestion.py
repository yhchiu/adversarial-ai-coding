"""Phased ATDD suggestion at the spec human gate.

The spec reviewer judges phased fitness as a side output of its normal
review; the judgment travels in .workflow/phased-suggestion.json and
never touches verdict.json. Everything here fails open to "no
suggestion": this mechanism must never block or fail a run.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings

SUGGESTION_NAME = "phased-suggestion.json"
DEFAULT_SUGGESTION = '{"phased": false, "reason": ""}\n'


def suggestion_path(wf: Path) -> Path:
    return wf / SUGGESTION_NAME


def suggestion_armed(settings: Settings) -> bool:
    """True when the spec review should also judge phased fitness.

    Explicit PHASES in the environment means the user already decided;
    an imported plan cannot retroactively become a phased plan. HUMAN_GATE
    does not gate arming: with the gate off the recommendation is logged.
    """

    return (
        not settings.phases
        and not settings.phases_explicit
        and not settings.import_plan
    )


def reset_suggestion(wf: Path) -> None:
    suggestion_path(wf).write_text(DEFAULT_SUGGESTION, encoding="utf-8")


def read_suggestion(wf: Path) -> tuple[bool, str]:
    """Return ``(phased, reason)``; malformed input becomes ``(False, "")``."""

    try:
        payload = json.loads(suggestion_path(wf).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return (False, "")
    if (
        not isinstance(payload, dict)
        or payload.get("phased") is not True
        or not isinstance(payload.get("reason"), str)
    ):
        return (False, "")
    return (True, payload["reason"])
