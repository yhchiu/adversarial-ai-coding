"""Ports helpers.test.sh:792-844 (stage flow) and 540-549 (human gate)."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.runstate import RunState
from adversarial_ai_coding.workflow import (
    begin_stage,
    commit_if_dirty,
    commit_work,
    end_stage,
    human_gate_spec,
    set_spec_roles_from_slot,
)


def with_state(ctx, new_repo):
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    return ctx


def test_begin_end_records_stage_and_checkpoint(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    assert begin_stage(ctx, "stage-one") is True
    assert ctx.cur_stage == "stage-one"
    assert ctx.cur_round == 1
    end_stage(ctx)
    assert ctx.state.stage_done("stage-one")
    assert not ctx.state.stage_done("stage-two")
    assert ctx.state.read_last_head() == head_sha(new_repo)


def test_begin_stage_skips_completed_and_logs(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    logged = []
    ctx.echo = logged.append
    begin_stage(ctx, "stage-one")
    end_stage(ctx)
    assert begin_stage(ctx, "stage-one") is False
    assert any(
        "== skip [stage-one] (already completed in run" in line for line in logged
    )


def test_begin_stage_skip_verifies_artifacts(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    artifact = new_repo / "artifact.md"
    artifact.touch()
    begin_stage(ctx, "stage-one")
    end_stage(ctx)
    assert begin_stage(ctx, "stage-one", artifact) is False
    with pytest.raises(WorkflowAbort, match="run archive"):
        begin_stage(ctx, "stage-one", new_repo / "missing-artifact.md")


def test_begin_stage_resets_worker_session(make_ctx):
    ctx = make_ctx()
    ctx.session.worker_session = "old-session"
    begin_stage(ctx, "next-stage")
    assert ctx.session.worker_session == ""


def test_begin_end_without_claimed_state(make_ctx):
    ctx = make_ctx()
    assert ctx.state is None
    assert begin_stage(ctx, "some-stage") is True
    end_stage(ctx)
    assert ctx.cur_stage == "some-stage"


def test_commit_work_ensures_commit(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-code"
    (new_repo / "dirty.txt").write_text("x\n", encoding="utf-8")
    prompts = []
    monkeypatch.setattr(wf_mod, "work", lambda ctx, agent, prompt: prompts.append(prompt))
    commit_work(ctx, "claude", "Task done")
    assert prompts and "Task done" in prompts[0]
    from adversarial_ai_coding.gitops import status_porcelain

    assert status_porcelain(new_repo) == ""


def test_commit_if_dirty_skips_clean_tree(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(
        wf_mod,
        "work",
        lambda ctx, agent, prompt: pytest.fail("clean tree: no AI call"),
    )
    commit_if_dirty(ctx, "claude", "nothing")


def test_human_gate_disabled_passes(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "0", "RETRY_ON_LIMIT": "0"})
    human_gate_spec(ctx)


def test_human_gate_approval_and_abort(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    ctx.ask = lambda prompt: "y"
    human_gate_spec(ctx)
    ctx.ask = lambda prompt: "n"
    with pytest.raises(WorkflowAbort, match="spec was not approved"):
        human_gate_spec(ctx)


def test_set_spec_roles_from_slot(make_ctx):
    ctx = make_ctx(
        {"AGENT_A": "claude", "AGENT_B": "codex", "RETRY_ON_LIMIT": "0"}
    )
    set_spec_roles_from_slot(ctx, "B")
    assert ctx.spec_roles.owner_agent == "codex"
    assert ctx.spec_roles.reviewer_agent == "claude"
    assert ctx.spec_roles.owner_slot == "B"
    assert ctx.spec_roles.reviewer_slot == "A"


def test_default_ask_rejects_noninteractive_stdin(monkeypatch):
    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: False)
    with pytest.raises(WorkflowAbort, match="No interactive terminal"):
        wf_mod._default_ask("approve?")
