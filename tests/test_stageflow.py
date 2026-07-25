"""Ports helpers.test.sh:792-844 (stage flow) and 540-549 (human gate)."""

import pytest

from adversarial_ai_coding import agents
from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.runstate import RunState
from adversarial_ai_coding.workflow import (
    begin_stage,
    commit_if_dirty,
    commit_work,
    end_stage,
    human_gate_plan,
    human_gate_spec,
    plan_gate_preflight,
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


def test_begin_stage_resets_worker_session_and_owner(make_ctx):
    ctx = make_ctx()
    ctx.session.worker_session = "old-session"
    ctx.session.owner = ctx.ref("A")
    begin_stage(ctx, "next-stage")
    assert ctx.session.worker_session == ""
    assert ctx.session.owner is None


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
    commit_work(ctx, ctx.ref("A"), "Task done")
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
    commit_if_dirty(ctx, ctx.ref("A"), "nothing")


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


def _write_suggestion(ctx, phased=True, reason="two independent features"):
    import json

    (ctx.wf / "phased-suggestion.json").write_text(
        json.dumps({"phased": phased, "reason": reason}), encoding="utf-8"
    )


def _write_settings_snapshot(ctx):
    from adversarial_ai_coding.runstate import snapshot_values, write_snapshot

    write_snapshot(
        ctx.state.state_dir,
        snapshot_values(
            ctx.settings,
            branch="main",
            gate_cmd="",
            build_gate_cmd="",
            phase_gate_cmd="",
            task_arg="t",
            task_source_kind="arg",
            task_source_path="",
        ),
    )


def test_spec_gate_offer_flips_settings_and_snapshot(make_ctx, new_repo):
    from adversarial_ai_coding.runstate import load_snapshot

    ctx = with_state(
        make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"}), new_repo
    )
    _write_settings_snapshot(ctx)
    _write_suggestion(ctx)
    ctx.phased_suggestion_valid = True
    asked = []
    answers = iter(["y", "y"])
    ctx.ask = lambda prompt: (asked.append(prompt), next(answers))[1]

    human_gate_spec(ctx)

    assert ctx.settings.phases is True
    assert len(asked) == 2
    assert asked[1] == "Enable Phased ATDD for this run? [y/N]:"
    assert load_snapshot(ctx.state.state_dir)["PHASES"] == "1"


def test_spec_gate_offer_declined_changes_nothing(make_ctx, new_repo):
    from adversarial_ai_coding.runstate import load_snapshot

    ctx = with_state(
        make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"}), new_repo
    )
    _write_settings_snapshot(ctx)
    _write_suggestion(ctx)
    ctx.phased_suggestion_valid = True
    answers = iter(["y", "n"])
    ctx.ask = lambda prompt: next(answers)

    human_gate_spec(ctx)

    assert ctx.settings.phases is False
    assert load_snapshot(ctx.state.state_dir)["PHASES"] == "0"


def test_spec_gate_ignores_stale_positive_suggestion(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx)
    asked = []
    ctx.ask = lambda prompt: (asked.append(prompt), "y")[1]

    human_gate_spec(ctx)

    assert len(asked) == 1
    assert ctx.settings.phases is False


def test_spec_gate_stays_silent_without_a_recommendation(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx, phased=False)
    ctx.phased_suggestion_valid = True
    asked = []
    ctx.ask = lambda prompt: (asked.append(prompt), "y")[1]

    human_gate_spec(ctx)

    assert len(asked) == 1  # only the spec approval question


def test_spec_gate_respects_explicit_phases_zero(make_ctx):
    ctx = make_ctx({"PHASES": "0", "HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx)
    ctx.phased_suggestion_valid = True
    asked = []
    ctx.ask = lambda prompt: (asked.append(prompt), "y")[1]

    human_gate_spec(ctx)

    assert len(asked) == 1
    assert ctx.settings.phases is False


def test_spec_gate_logs_only_without_human_gate(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "0", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx, reason="fits nicely")
    ctx.phased_suggestion_valid = True
    logged = []
    ctx.echo = logged.append
    ctx.ask = lambda prompt: pytest.fail("HUMAN_GATE=0 must never ask")

    human_gate_spec(ctx)

    assert ctx.settings.phases is False
    assert any(
        "reviewer suggests Phased ATDD: fits nicely" in line for line in logged
    )


def test_append_phased_suggestion_scope_only_when_armed(make_ctx):
    from adversarial_ai_coding.workflow import append_phased_suggestion_scope

    ctx = make_ctx()
    scope = append_phased_suggestion_scope(ctx, "base scope\n")
    assert scope.startswith("base scope\n")
    assert "phased-suggestion.json" in scope
    assert ctx.phased_suggestion_active is True

    ctx2 = make_ctx({"PHASES": "0", "RETRY_ON_LIMIT": "0"})
    assert append_phased_suggestion_scope(ctx2, "base scope\n") == "base scope\n"
    assert ctx2.phased_suggestion_active is False


def test_plan_gate_is_off_by_default(make_ctx):
    ctx = make_ctx({"RETRY_ON_LIMIT": "0"})
    ctx.ask = lambda prompt: pytest.fail("plan gate must not ask when disabled")
    human_gate_plan(ctx)


def test_plan_gate_approval_and_abort(make_ctx):
    ctx = make_ctx({"HUMAN_GATE_PLAN": "1", "RETRY_ON_LIMIT": "0"})
    lines = []
    ctx.echo = lines.append
    ctx.ask = lambda prompt: "y"
    human_gate_plan(ctx)
    assert any("plan.md" in line for line in lines)
    ctx.ask = lambda prompt: "n"
    with pytest.raises(WorkflowAbort, match="plan was not approved"):
        human_gate_plan(ctx)


def test_plan_gate_preflight_needs_a_terminal(make_ctx):
    settings = make_ctx({"HUMAN_GATE_PLAN": "1", "RETRY_ON_LIMIT": "0"}).settings
    with pytest.raises(WorkflowAbort, match="requires an interactive terminal"):
        plan_gate_preflight(settings, stdin_isatty=False)
    plan_gate_preflight(settings, stdin_isatty=True)
    plan_gate_preflight(make_ctx().settings, stdin_isatty=False)


def test_set_spec_roles_from_slot(make_ctx):
    ctx = make_ctx(
        {"AGENT_A": "claude", "AGENT_B": "codex", "RETRY_ON_LIMIT": "0"}
    )
    set_spec_roles_from_slot(ctx, "B")
    assert ctx.spec_roles.owner_agent == ctx.ref("B")
    assert ctx.spec_roles.reviewer_agent == ctx.ref("A")
    assert ctx.spec_roles.owner_slot == "B"
    assert ctx.spec_roles.reviewer_slot == "A"


def test_write_code_routes_only_task_loop_repairs_and_commit_to_impl(
    make_ctx, new_repo, monkeypatch
):
    ctx = with_state(
        make_ctx(
            {
                "AGENT_A": "codex",
                "AGENT_B": "claude",
                "MODEL_A": "owner-model",
                "IMPL_MODEL": "impl-model",
                "IMPL_ARGS": "--impl-only",
                "RETRY_ON_LIMIT": "0",
            }
        ),
        new_repo,
    )
    ctx.gate_cmd = "full-gate"
    ctx.build_gate_cmd = "build-gate"
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    plan_file = ctx.spec_dir / "plan.md"
    plan_file.write_text("- [ ] route this task\n", encoding="utf-8")
    (ctx.wf / "protected-tests.txt").write_text(
        "acceptance_test.py\n", encoding="utf-8"
    )
    (ctx.wf / "protected-base.sha").write_text("base\n", encoding="utf-8")

    resolved_for = []

    def tracking_impl_ref(owner, settings):
        resolved_for.append(owner)
        return agents.impl_ref(owner, settings)

    monkeypatch.setattr(wf_mod, "impl_ref", tracking_impl_ref, raising=False)
    monkeypatch.setattr(
        wf_mod,
        "begin_stage",
        lambda ctx, name, *artifacts: name == "write-code",
    )
    monkeypatch.setattr(wf_mod, "end_stage", lambda ctx: None)
    monkeypatch.setattr(wf_mod, "finish", lambda ctx, task: None)

    work_calls = []

    def fake_work(ctx, agent, prompt):
        if not work_calls:
            assert ctx.protected_controls is not None
        label = "protected-repair" if ctx.checking_protected else prompt
        work_calls.append((label, agent))
        if prompt == "build-gate-repair":
            wf_mod.check_protected(ctx, agent)

    monkeypatch.setattr(wf_mod, "work", fake_work)
    violations = iter([["acceptance_test.py"], []])
    monkeypatch.setattr(
        wf_mod,
        "protected_violations",
        lambda protected, base, workspace: next(violations),
    )

    gate_events = []

    def fail_then_repair_gate(cmd, **kwargs):
        gate_events.append((cmd, "failed"))
        kwargs["do_work"](f"{cmd}-repair")
        gate_events.append((cmd, "passed"))

    monkeypatch.setattr(wf_mod, "gate_loop_ref", fail_then_repair_gate)

    review_events = []

    def fail_then_repair_review(ctx, reviewer, worker, scope, gate_cmd=""):
        review_events.append("failed")
        wf_mod.work(ctx, worker, "branch-review-repair")
        review_events.append("passed")

    monkeypatch.setattr(wf_mod, "review_loop_ref", fail_then_repair_review)
    commits = []
    monkeypatch.setattr(
        wf_mod,
        "commit_work",
        lambda ctx, agent, description: commits.append((agent, description)),
    )
    dirty_commits = []
    monkeypatch.setattr(
        wf_mod,
        "commit_if_dirty",
        lambda ctx, agent, description: dirty_commits.append((agent, description)),
    )

    wf_mod.run_workflow(ctx, "route plan tasks")

    assert resolved_for == [ctx.spec_roles.owner_agent]
    assert ctx.protected_controls is not None
    assert ctx.protected_controls.paths == frozenset({"acceptance_test.py"})
    assert ctx.protected_controls.base == "base"
    assert gate_events == [
        ("build-gate", "failed"),
        ("build-gate", "passed"),
        ("full-gate", "failed"),
        ("full-gate", "passed"),
    ]
    assert review_events == ["failed", "passed"]

    def agent_for(label):
        return next(agent for prompt, agent in work_calls if label in prompt)

    assert agent_for("route this task").slot == "I"
    assert agent_for("build-gate-repair").slot == "I"
    assert agent_for("protected-repair").slot == "I"
    assert agent_for("full-gate-repair") == ctx.spec_roles.owner_agent
    assert agent_for("branch-review-repair") == ctx.spec_roles.owner_agent
    assert commits == [
        (
            agents.impl_ref(ctx.spec_roles.owner_agent, ctx.settings),
            'Task "route this task"',
        )
    ]
    assert dirty_commits == [(ctx.spec_roles.owner_agent, "Review fixes")]

    resolved_lines = [
        line
        for line in ctx.archive.log_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("Resolved implementation:")
    ]
    assert len(resolved_lines) == 1
    assert "agent=codex" in resolved_lines[0]
    assert "model=impl-model" in resolved_lines[0]
    assert "args=--impl-only" in resolved_lines[0]


def test_write_code_logs_custom_impl_model_warning(make_ctx, monkeypatch):
    ctx = make_ctx(
        {
            "AGENT_A": "owner-wrapper",
            "AGENT_B": "reviewer-wrapper",
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_MODEL": "ignored-model",
            "RETRY_ON_LIMIT": "0",
        }
    )
    (ctx.wf / "protected-tests.txt").write_text("", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("base\n", encoding="utf-8")
    monkeypatch.setattr(
        wf_mod,
        "begin_stage",
        lambda ctx, name, *artifacts: name == "write-code",
    )
    monkeypatch.setattr(wf_mod, "end_stage", lambda ctx: None)
    monkeypatch.setattr(wf_mod, "review_loop_ref", lambda *args, **kwargs: None)
    monkeypatch.setattr(wf_mod, "commit_if_dirty", lambda *args, **kwargs: None)
    monkeypatch.setattr(wf_mod, "finish", lambda ctx, task: None)

    wf_mod.run_workflow(ctx, "log custom implementation settings")

    log = ctx.archive.log_path.read_text(encoding="utf-8")
    assert "Resolved implementation: agent=impl-wrapper model= args=" in log
    assert (
        "warning: IMPL_MODEL is ignored for custom implementation agent "
        "impl-wrapper" in log
    )


def test_default_ask_rejects_noninteractive_stdin(monkeypatch):
    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: False)
    with pytest.raises(WorkflowAbort, match="No interactive terminal"):
        wf_mod._default_ask("approve?")


def test_branch_and_final_reviews_receive_run_base(make_ctx, monkeypatch):
    # The write-code and final-acceptance reviews must be anchored to the
    # commit the run started from, not left to the reviewer to guess.
    ctx = make_ctx()
    (ctx.wf / "protected-tests.txt").write_text("", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("base\n", encoding="utf-8")
    run_base = head_sha(ctx.workspace)
    monkeypatch.setattr(
        wf_mod,
        "begin_stage",
        lambda ctx, name, *artifacts: name
        in {"write-code", "final-review-and-fixes"},
    )
    monkeypatch.setattr(wf_mod, "end_stage", lambda ctx: None)
    monkeypatch.setattr(wf_mod, "finish", lambda ctx, task: None)
    monkeypatch.setattr(wf_mod, "work", lambda ctx, agent, prompt: None)
    monkeypatch.setattr(wf_mod, "gate_loop_ref", lambda cmd, **kwargs: None)
    monkeypatch.setattr(wf_mod, "commit_if_dirty", lambda *args, **kwargs: None)
    scopes = []
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx, reviewer, worker, scope, gate_cmd="": scopes.append(scope),
    )

    wf_mod.run_workflow(ctx, "anchor reviews to the run base")

    assert len(scopes) == 2
    for scope in scopes:
        assert run_base in scope
