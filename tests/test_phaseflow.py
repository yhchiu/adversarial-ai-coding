"""Phased stage flow: structure check, red check, and the phase loop."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding import gates
from adversarial_ai_coding import phaseflow
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.phaseflow import phased_plan_structure_check
from adversarial_ai_coding.phases import Phase
from adversarial_ai_coding.runstate import RunState

VALID_PLAN = """# Plan

## Phase 1: feature works
Acceptance: the CLI prints the result.
- [ ] add the flag
"""


def test_structure_check_passes_valid_plan(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text(VALID_PLAN, encoding="utf-8")
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("valid plan: no repair call")
    )
    phased_plan_structure_check(ctx, plan)


def test_structure_check_repairs_then_passes(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text("# Plan\n\n- [ ] stray task\n", encoding="utf-8")
    prompts = []

    def repair(ctx_arg, agent, prompt):
        prompts.append((agent, prompt))
        plan.write_text(VALID_PLAN, encoding="utf-8")

    monkeypatch.setattr(wf_mod, "work", repair)
    phased_plan_structure_check(ctx, plan)
    assert len(prompts) == 1
    agent, prompt = prompts[0]
    assert agent == ctx.spec_roles.owner_agent
    assert "stray task" in prompt


def test_structure_check_exhaustion_aborts_and_notifies(
    make_ctx, new_repo, monkeypatch
):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text("prose without phases\n", encoding="utf-8")
    monkeypatch.setattr(wf_mod, "work", lambda *args: None)
    notices = []
    monkeypatch.setattr(ctx, "notify", notices.append)
    with pytest.raises(WorkflowAbort, match="invalid phased structure"):
        phased_plan_structure_check(ctx, plan)
    assert notices


NORMAL = Phase(number=1, title="feature works", regression_guard=False, tasks=("t",))
GUARD = Phase(number=2, title="unchanged", regression_guard=True, tasks=("t",))


def test_red_check_passes_when_normal_phase_is_red(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (1, "red"))
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("no repair expected")
    )
    phaseflow.red_check(ctx, NORMAL, "gate")


def test_red_check_passes_when_guard_phase_is_green(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (0, "green"))
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("no repair expected")
    )
    phaseflow.red_check(ctx, GUARD, "gate")


def test_red_check_repairs_with_test_author_then_passes(make_ctx, monkeypatch):
    ctx = make_ctx()
    results = iter([(0, "green"), (1, "red")])
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(results))
    repairs = []
    monkeypatch.setattr(
        wf_mod, "work", lambda ctx_arg, agent, prompt: repairs.append((agent, prompt))
    )
    phaseflow.red_check(ctx, NORMAL, "gate")
    assert len(repairs) == 1
    agent, prompt = repairs[0]
    assert agent == ctx.spec_roles.reviewer_agent
    assert "must FAIL" in prompt


def test_red_check_exhaustion_aborts(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (0, "green"))
    monkeypatch.setattr(wf_mod, "work", lambda *args: None)
    notices = []
    monkeypatch.setattr(ctx, "notify", notices.append)
    with pytest.raises(WorkflowAbort, match="red check failed"):
        phaseflow.red_check(ctx, NORMAL, "gate")
    assert notices


def test_red_check_skips_without_command(make_ctx, monkeypatch):
    ctx = make_ctx()
    warnings = []
    ctx.echo_err = warnings.append
    monkeypatch.setattr(
        gates, "run_shell", lambda cmd, cwd: pytest.fail("must not run")
    )
    phaseflow.red_check(ctx, NORMAL, "")
    assert any("red check is skipped" in line for line in warnings)


PHASED_PLAN = """# Plan

## Phase 1: feature works
Acceptance: src.txt exists.
- [ ] task one
- [ ] task two

## Phase 2: old behavior unchanged (regression-guard)
Acceptance: base.txt unchanged.
- [ ] task three
"""


def test_run_phased_stages_drives_phases_in_order(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx(
        {"PHASES": "1", "IMPL_MODEL": "impl-model", "RETRY_ON_LIMIT": "0"}
    )
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.gate_cmd = "full-gate"
    ctx.build_gate_cmd = "build-gate"
    ctx.phase_gate_cmd = "phase-gate"
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.spec_dir / "plan.md"
    plan.write_text(PHASED_PLAN, encoding="utf-8")
    spec = ctx.spec_dir / "spec.md"
    spec.write_text("spec\n", encoding="utf-8")

    events = []
    monkeypatch.setattr(
        wf_mod,
        "work",
        lambda ctx_arg, agent, prompt: events.append(
            ("work", agent.slot, prompt.splitlines()[0])
        ),
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_work",
        lambda ctx_arg, agent, description: events.append(
            ("commit", agent.slot, description)
        ),
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_if_dirty",
        lambda ctx_arg, agent, description: events.append(
            ("dirty", agent.slot, description)
        ),
    )
    monkeypatch.setattr(
        wf_mod, "gate_loop_ref", lambda cmd, **kwargs: events.append(("gate", cmd))
    )
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": events.append(
            ("review", reviewer.slot, worker.slot)
        ),
    )
    red_results = iter([(1, "red"), (0, "green")])
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(red_results))

    phaseflow.run_phased_stages(ctx, spec, plan)

    assert ctx.state.completed_stages() == [
        "phase-01-write-tests",
        "phase-01-implement",
        "phase-02-write-tests",
        "phase-02-implement",
    ]
    test_writers = [
        event[1]
        for event in events
        if event[0] == "work"
        and event[2].startswith("Write acceptance tests for exactly one")
    ]
    assert test_writers == ["B", "B"]
    assert [event for event in events if event[0] == "review"] == [
        ("review", "A", "B"),
        ("review", "A", "B"),
    ]
    assert [event for event in events if event[0] == "gate"] == [
        ("gate", "build-gate"),
        ("gate", "build-gate"),
        ("gate", "phase-gate"),
        ("gate", "build-gate"),
        ("gate", "phase-gate"),
    ]
    implementers = [
        event[1]
        for event in events
        if event[0] == "work" and event[2].startswith("Implement this task")
    ]
    assert implementers == ["I", "I", "I"]
    task_commits = [
        event[2]
        for event in events
        if event[0] == "commit" and event[2].startswith('Task "')
    ]
    assert task_commits == ['Task "task one"', 'Task "task two"', 'Task "task three"']
    assert ctx.protected_controls is not None
    plan_text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in plan_text and plan_text.count("- [x]") == 3


def test_phase_review_adds_reviewer_loop_over_impl(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx(
        {
            "PHASES": "1",
            "PHASE_REVIEW": "1",
            "IMPL_MODEL": "impl-model",
            "RETRY_ON_LIMIT": "0",
        }
    )
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.phase_gate_cmd = "phase-gate"
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.spec_dir / "plan.md"
    plan.write_text(
        "## Phase 1: feature works\nAcceptance: x.\n- [ ] task one\n",
        encoding="utf-8",
    )
    spec = ctx.spec_dir / "spec.md"
    spec.write_text("spec\n", encoding="utf-8")

    events = []
    monkeypatch.setattr(wf_mod, "work", lambda ctx_arg, agent, prompt: None)
    monkeypatch.setattr(
        wf_mod, "commit_work", lambda ctx_arg, agent, description: None
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_if_dirty",
        lambda ctx_arg, agent, description: events.append(
            ("dirty", agent.slot, description)
        ),
    )
    monkeypatch.setattr(wf_mod, "gate_loop_ref", lambda cmd, **kwargs: None)
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": events.append(
            ("review", reviewer.slot, worker.slot)
        ),
    )
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (1, "red"))

    phaseflow.run_phased_stages(ctx, spec, plan)

    assert ("review", "B", "I") in events
    assert ("dirty", "I", "Phase 1 review fixes") in events
