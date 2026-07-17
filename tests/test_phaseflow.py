"""Phased stage flow: structure check, red check, and the phase loop."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.phaseflow import phased_plan_structure_check

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
