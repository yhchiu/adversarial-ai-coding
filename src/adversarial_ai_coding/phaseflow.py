"""Phased ATDD stage flow (PHASES=1).

Like dual_spec.py, this module drives workflow.py primitives; every call
goes through the module object (wf.work, wf.review_loop_ref, ...) so the
existing monkeypatch seams on workflow keep working.
"""

from __future__ import annotations

from pathlib import Path

from . import workflow as wf
from .agents import (
    agent_model,
    impl_ref,
    is_builtin_agent,
    resolve_model_args,
)
from .config import WorkflowAbort
from .phases import Phase, PhasePlanError, parse_phases
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


def red_check(ctx: wf.WorkflowContext, phase: Phase, cmd: str) -> None:
    """TDD-red gate run by the workflow, never trusted from AI output."""

    from .gates import run_shell

    if not cmd:
        ctx.echo_err(
            "(warning: no phase gate command; the red check is skipped. Set "
            "PHASE_GATE_CMD or GATE_CMD to enable it.)"
        )
        return
    attempt = 1
    while True:
        ctx.log(f">>> Phase red check:{cmd}")
        rc, output = run_shell(cmd, ctx.workspace)
        if phase.regression_guard:
            ok = rc == 0
            expected = (
                'this phase is marked "(regression-guard)", so its tests must '
                "PASS against current behavior, but the command failed"
            )
        else:
            ok = rc != 0
            expected = (
                "the new phase tests must FAIL (red) because the phase is not "
                "implemented yet, but the command passed. Tests that pass "
                "before the implementation exists prove nothing"
            )
        if ok:
            ctx.log("Phase red check passed")
            return
        ctx.log(f"Phase red check failed (attempt {attempt})")
        if attempt >= ctx.settings.max_rounds:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] phase red check "
                "failed repeatedly; human intervention required"
            )
            raise WorkflowAbort(
                f"!! [{ctx.cur_stage}] Phase red check failed "
                f"{ctx.settings.max_rounds} times; stopping for human "
                "intervention. Output:\n"
                + "\n".join(output.splitlines()[-50:])
            )
        attempt += 1
        prompt = render_prompt(
            ctx.prompts_dir,
            "phase-red-check-failed",
            {
                "COMMAND": cmd,
                "EXPECTED": expected,
                "PHASE_TITLE": phase.title,
                "OUTPUT": "\n".join(output.splitlines()[-150:]),
            },
        )
        wf.work(ctx, ctx.spec_roles.reviewer_agent, prompt)


def run_phased_stages(
    ctx: wf.WorkflowContext, spec_file: Path, plan_file: Path
) -> None:
    from .gitops import head_sha
    from .runstate import (
        ensure_named_task_queue,
        ensure_phases,
        mark_plan_task_done,
        phase_queue_name,
        pop_task_queue,
        remaining_tasks,
        restore_or_record_base,
    )

    if ctx.state is None:
        raise WorkflowAbort("!! PHASES=1 requires claimed run state.")
    phases = ensure_phases(ctx.state, plan_file)
    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    phase_gate = ctx.phase_gate_cmd or ctx.gate_cmd
    impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)
    ctx.log(
        "Resolved implementation: "
        f"agent={impl.name} model={agent_model(impl, ctx.settings)} "
        f"args={resolve_model_args(impl, ctx.settings)}"
    )
    if ctx.settings.impl_model and not is_builtin_agent(impl.name):
        ctx.log(
            "warning: IMPL_MODEL is ignored for custom implementation "
            f"agent {impl.name}"
        )
    done_titles: list[str] = []
    for phase in phases:
        label = f"phase-{phase.number:02d}"
        base_name = f"{label}-test-base"
        if wf.begin_stage(ctx, f"{label}-write-tests", protected_list, protected_base):
            test_base = restore_or_record_base(
                ctx.state, base_name, lambda: head_sha(ctx.workspace)
            )
            wf.work(
                ctx,
                ctx.spec_roles.reviewer_agent,
                render_prompt(
                    ctx.prompts_dir,
                    "write-phase-tests",
                    {
                        "SPEC_FILE": str(spec_file),
                        "PLAN_FILE": str(plan_file),
                        "SPEC_DIR": str(ctx.spec_dir),
                        "PHASE_TITLE": phase.title,
                        "PHASES_DONE": ", ".join(done_titles) or "none",
                        "PROTECTED_TESTS_FILE": str(protected_list),
                    },
                ),
            )
            scope = render_prompt(
                ctx.prompts_dir,
                "review-scope-acceptance-tests",
                {"TEST_BASE": test_base, "SPEC_FILE": str(spec_file)},
            )
            wf.review_loop_ref(
                ctx,
                ctx.spec_roles.owner_agent,
                ctx.spec_roles.reviewer_agent,
                scope,
            )
            red_check(ctx, phase, phase_gate)
            wf.commit_work(
                ctx,
                ctx.spec_roles.reviewer_agent,
                f"Phase {phase.number} acceptance tests",
            )
            wf.record_protected_tests(ctx, test_base, append=True)
            wf.end_stage(ctx)
        wf.activate_protected_controls(ctx)
        if wf.begin_stage(ctx, f"{label}-implement"):
            queue = phase_queue_name(phase.number)
            ensure_named_task_queue(ctx.state, queue, list(phase.tasks))
            total = len(phase.tasks)
            while remaining_tasks(ctx.state, queue):
                task_line = remaining_tasks(ctx.state, queue)[0]
                index = total - len(remaining_tasks(ctx.state, queue)) + 1
                ctx.log(
                    f"--- Phase {phase.number} task {index}/{total}:{task_line} ---"
                )
                wf.work(
                    ctx,
                    impl,
                    render_prompt(
                        ctx.prompts_dir,
                        "implement-plan-task",
                        {
                            "PLAN_FILE": str(plan_file),
                            "TASK": task_line,
                            "PROTECTED_TESTS_FILE": str(protected_list),
                        },
                    ),
                )
                wf.gate_loop_ref(
                    ctx.build_gate_cmd,
                    cwd=ctx.workspace,
                    prompts_dir=ctx.prompts_dir,
                    max_rounds=ctx.settings.max_rounds,
                    do_work=lambda prompt: wf.work(ctx, impl, prompt),
                    log=ctx.log,
                    notify=ctx.notify,
                    stage=ctx.cur_stage,
                )
                wf.commit_work(ctx, impl, f'Task "{task_line}"')
                pop_task_queue(ctx.state, queue)
                mark_plan_task_done(plan_file, task_line)
            ctx.log(
                f"--- Phase {phase.number} tasks complete; running the phase "
                "gate. All tests written so far must pass. ---"
            )
            wf.gate_loop_ref(
                phase_gate,
                cwd=ctx.workspace,
                prompts_dir=ctx.prompts_dir,
                max_rounds=ctx.settings.max_rounds,
                do_work=lambda prompt: wf.work(ctx, impl, prompt),
                log=ctx.log,
                notify=ctx.notify,
                stage=ctx.cur_stage,
            )
            wf.commit_if_dirty(
                ctx, impl, f"Phase {phase.number} gate repairs"
            )
            if ctx.settings.phase_review:
                phase_base = restore_or_record_base(
                    ctx.state, base_name, lambda: head_sha(ctx.workspace)
                )
                scope = render_prompt(
                    ctx.prompts_dir,
                    "review-scope-phase",
                    {
                        "PHASE_TITLE": phase.title,
                        "PHASE_BASE": phase_base,
                        "PLAN_FILE": str(plan_file),
                    },
                )
                wf.review_loop_ref(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    impl,
                    scope,
                    gate_cmd=phase_gate,
                )
                wf.commit_if_dirty(
                    ctx, impl, f"Phase {phase.number} review fixes"
                )
            wf.end_stage(ctx)
        done_titles.append(phase.title)
