"""Import of externally written spec/plan files (IMPORT_SPEC/IMPORT_PLAN).

Design: docs/superpowers/specs/2026-07-19-import-spec-plan-design.md.
Deterministic validation runs at preflight, before any paid AI call. File
validation is fresh-run only: a resumed run re-validates at stage time,
because a completed import stage must not fail resume when the source
file has since been deleted.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Mapping

from .config import Settings, SettingsError, WorkflowAbort
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

    if "IMPORT_REVIEW" in env and not settings.import_spec:
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


def stage_import(ctx, kind: str, src_str: str, dst: Path) -> None:
    """Copy an imported artifact into the spec dir and archive the original.

    Runs inside the write stage: a resumed run that redoes the stage
    re-validates and re-copies. The source file is never modified.
    """

    src = Path(src_str)
    archive_name = f"imported-{kind}.md"
    try:
        if kind == "spec":
            validate_import_spec(src)
        else:
            validate_import_plan(src, ctx.settings.phases)
    except SettingsError as exc:
        archived = (
            ctx.state.import_archive_path(kind)
            if ctx.state is not None
            else None
        )
        archive_hint = (
            f"the archived copy is {archived}."
            if archived is not None
            else "no archived copy is available."
        )
        raise WorkflowAbort(
            f"!! Cannot import the {kind}: {exc}\n"
            "   If an earlier attempt of this run imported it, the "
            f"{archive_hint}"
        ) from None
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        archived = ctx.archive.archive_snapshot(
            src, archive_name, "workflow", None, ctx.cur_stage, ctx.cur_round
        )
        if archived is not None and ctx.state is not None:
            ctx.state.record_import_archive(kind, archived)
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Cannot stage imported {kind} from {src} at {dst} "
            f"(archive {archive_name} under {ctx.archive.run_dir}): {exc}"
        ) from exc
    review = "on" if ctx.settings.import_review else "off"
    ctx.log(f"Imported {kind} from {src} (review: {review})")
