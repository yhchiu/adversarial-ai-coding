"""Phased ATDD stage flow (PHASES=1).

Like dual_spec.py, this module drives workflow.py primitives; every call
goes through the module object (wf.work, wf.review_loop_ref, ...) so the
existing monkeypatch seams on workflow keep working.
"""

from __future__ import annotations

from pathlib import Path

from . import workflow as wf
from .config import WorkflowAbort
from .phases import PhasePlanError, parse_phases
from .prompts import render_prompt


def phased_plan_structure_check(ctx: wf.WorkflowContext, plan_file: Path) -> None:
    """Deterministic plan-format gate: parse, send repairs to the owner, abort."""

    attempt = 1
    while True:
        try:
            parse_phases(plan_file)
        except PhasePlanError as exc:
            ctx.log(f"Phased plan structure check failed (attempt {attempt})")
            if attempt >= ctx.settings.max_rounds:
                ctx.notify(
                    f"adversarial-ai-coding:[{ctx.cur_stage}] phased plan "
                    "structure check failed repeatedly; human intervention "
                    "required"
                )
                raise WorkflowAbort(
                    f"!! [{ctx.cur_stage}] plan.md still has an invalid phased "
                    f"structure after {ctx.settings.max_rounds} attempts; "
                    f"stopping for human intervention.\n{exc}"
                )
            attempt += 1
            prompt = render_prompt(
                ctx.prompts_dir,
                "phased-plan-invalid",
                {"PLAN_FILE": str(plan_file), "PROBLEMS": str(exc)},
            )
            wf.work(ctx, ctx.spec_roles.owner_agent, prompt)
            continue
        ctx.log("Phased plan structure check passed")
        return
