"""Import of externally written spec/plan files (IMPORT_SPEC/IMPORT_PLAN).

Design: docs/superpowers/specs/2026-07-19-import-spec-plan-design.md.
Deterministic validation runs at preflight, before any paid AI call. File
validation is fresh-run only: a resumed run re-validates at stage time,
because a completed import stage must not fail resume when the source
file has since been deleted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .config import Settings, SettingsError
from .phases import TASK_PREFIX, PhasePlanError, parse_phases

CONTRACT_HINT = "See docs/import-format.md for the import format contract."


def _read_import_file(path: Path, var: str) -> str:
    if not path.is_file():
        raise SettingsError(f"{var}={path}: file not found.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"{var}={path}: unreadable ({exc}).") from None
    if not text.strip():
        raise SettingsError(f"{var}={path}: file is empty.")
    return text


def _has_assumptions_heading(text: str) -> bool:
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        lower = line.lower()
        if "assumptions" in lower and "open questions" in lower:
            return True
    return False


def validate_import_spec(path: Path) -> None:
    text = _read_import_file(path, "IMPORT_SPEC")
    if not _has_assumptions_heading(text):
        raise SettingsError(
            f"IMPORT_SPEC={path}: the spec must contain a Markdown heading "
            "whose text includes both 'Assumptions' and 'Open Questions' "
            "(case-insensitive), for example "
            f"'## Assumptions and Open Questions'. {CONTRACT_HINT}"
        )


def validate_import_plan(path: Path, phases: bool) -> None:
    text = _read_import_file(path, "IMPORT_PLAN")
    if phases:
        try:
            parse_phases(path)
        except PhasePlanError as exc:
            raise SettingsError(
                f"IMPORT_PLAN={path}: not a valid phased plan "
                f"(PHASES=1):\n{exc}\n{CONTRACT_HINT}"
            ) from None
    elif not any(
        line.startswith(TASK_PREFIX) for line in text.splitlines()
    ):
        raise SettingsError(
            f"IMPORT_PLAN={path}: the plan must contain at least one "
            f"'{TASK_PREFIX}' task line. {CONTRACT_HINT}"
        )


def import_preflight(
    settings: Settings, env: Mapping[str, str], *, fresh_run: bool
) -> None:
    """Reject bad import config before workspace setup and any AI call."""

    if env.get("IMPORT_REVIEW") and not settings.import_spec:
        raise SettingsError(
            "IMPORT_REVIEW is set but IMPORT_SPEC is not. Unset "
            "IMPORT_REVIEW, or provide IMPORT_SPEC."
        )
    if settings.import_plan and not settings.import_spec:
        raise SettingsError(
            "IMPORT_PLAN requires IMPORT_SPEC: a plan is written against a "
            "spec, and the workflow does not reconstruct a spec from a plan."
        )
    if settings.import_spec and settings.dual_spec:
        raise SettingsError(
            "IMPORT_SPEC and DUAL_SPEC=1 are incompatible: dual candidate "
            "specs and an imported spec contradict each other. Disable one."
        )
    if not fresh_run or not settings.import_spec:
        return
    validate_import_spec(Path(settings.import_spec))
    if settings.import_plan:
        validate_import_plan(Path(settings.import_plan), settings.phases)
