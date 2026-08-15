"""Phased ATDD suggestion at the spec human gate.

The spec reviewer judges phased fitness as a side output of its normal
review; the judgment travels in aac/.run/phased-suggestion.json and
never touches verdict.json. Everything here fails open to "no
suggestion": this mechanism must never block or fail a run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from .config import Settings
from .i18n import emit

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


def read_suggestion(
    wf: Path, warn: Callable[[str], None] | None = None
) -> tuple[bool, str]:
    """Return ``(phased, reason)``; unusable input becomes ``(False, "")``.

    A recommendation survives a missing or non-string ``reason``: the
    judgment is what the gate needs, and dropping it over its prose would
    lose the reviewer's answer entirely. ``warn`` reports input the
    workflow did not expect; a missing file and a well-formed negative
    judgment are normal and stay quiet.
    """

    def report(template: str, **fields: object) -> None:
        if warn is not None:
            emit(warn, template, **fields)

    try:
        text = suggestion_path(wf).read_text(encoding="utf-8")
    except FileNotFoundError:
        return (False, "")
    except (OSError, UnicodeDecodeError) as exc:
        report(
            "(warning: {name} is unreadable ({exc}); treating it as no suggestion)",
            name=SUGGESTION_NAME,
            exc=exc,
        )
        return (False, "")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        report(
            "(warning: {name} is unreadable as JSON ({exc}); treating it as no suggestion)",
            name=SUGGESTION_NAME,
            exc=exc,
        )
        return (False, "")
    phased = payload.get("phased") if isinstance(payload, dict) else None
    if not isinstance(phased, bool):
        report(
            '(warning: {name} has no boolean "phased" field; treating it as no suggestion)',
            name=SUGGESTION_NAME,
        )
        return (False, "")
    if not phased:
        return (False, "")
    reason = payload.get("reason")
    if not isinstance(reason, str):
        report(
            '(warning: {name} has no string "reason"; keeping the recommendation without one)',
            name=SUGGESTION_NAME,
        )
        return (True, "")
    return (True, reason)
