"""Ports idempotent finish behavior and adds a fully stubbed pipeline smoke test."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.runstate import RunState
from adversarial_ai_coding.workflow import finish, run_workflow


def test_finish_reports_existing_pr_instead_of_recreating(make_ctx):
    ctx = make_ctx({"OPEN_PR": "1", "RETRY_ON_LIMIT": "0"})
    lines = []
    ctx.echo = lines.append
    gh_calls = []

    def fake_gh(args, cwd):
        gh_calls.append(args)
        if args[:2] == ["pr", "view"]:
            return 0, "https://example.com/pr/1"
        pytest.fail("pr create must not run when a PR exists")

    finish(
        ctx,
        "task title",
        which=lambda name: "gh",
        run_gh=fake_gh,
        run_git=lambda args, cwd: (
            0,
            "origin-url" if "get-url" in args else "",
        ),
    )
    assert any("PR already exists: https://example.com/pr/1" in line for line in lines)
    assert (ctx.wf / "pr-body.md").is_file()
    assert "AgentRef(" not in (ctx.wf / "pr-body.md").read_text(encoding="utf-8")


def test_finish_creates_pr_when_missing(make_ctx):
    ctx = make_ctx({"OPEN_PR": "1", "RETRY_ON_LIMIT": "0"})
    gh_calls = []

    def fake_gh(args, cwd):
        gh_calls.append(args)
        return (1, "") if args[:2] == ["pr", "view"] else (0, "CREATE-CALLED")

    finish(
        ctx,
        "task title",
        which=lambda name: "gh",
        run_gh=fake_gh,
        run_git=lambda args, cwd: (
            0,
            "origin-url" if "get-url" in args else "",
        ),
    )
    assert any(args[:2] == ["pr", "create"] for args in gh_calls)


def test_finish_without_open_pr_prints_commands(make_ctx):
    ctx = make_ctx({"RETRY_ON_LIMIT": "0"})
    lines = []
    ctx.echo = lines.append
    finish(
        ctx,
        "long task title\nsecond line",
        which=lambda name: None,
        run_gh=None,
        run_git=None,
    )
    joined = "\n".join(lines)
    assert "git push -u origin" in joined
    assert "gh pr create --title" in joined
    assert "long task title" in joined
    assert "second line" in (ctx.wf / "pr-body.md").read_text(encoding="utf-8")


def test_run_workflow_single_spec_stage_order(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.run_id = "run"
    order = []

    def fake_work(ctx_arg, agent, prompt):
        order.append(("work", ctx_arg.cur_stage))
        if ctx_arg.cur_stage == "write-spec":
            ctx_arg.spec_dir.mkdir(parents=True, exist_ok=True)
            (ctx_arg.spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        if ctx_arg.cur_stage == "write-implementation-plan":
            (ctx_arg.spec_dir / "plan.md").write_text(
                "- [ ] only task\n", encoding="utf-8"
            )

    monkeypatch.setattr(wf_mod, "work", fake_work)
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx, reviewer, worker, scope, gate_cmd="": order.append(
            ("review", ctx.cur_stage)
        ),
    )
    monkeypatch.setattr(
        wf_mod, "human_gate_spec", lambda ctx: order.append(("human", ctx.cur_stage))
    )
    monkeypatch.setattr(
        wf_mod,
        "human_gate_plan",
        lambda ctx: order.append(("human-plan", ctx.cur_stage)),
    )
    monkeypatch.setattr(
        wf_mod,
        "gate_loop_ref",
        lambda cmd, **kwargs: order.append(("gate", cmd)),
    )
    monkeypatch.setattr(
        wf_mod, "finish", lambda ctx, task, **kwargs: order.append(("finish", task))
    )
    run_workflow(ctx, "demo task")
    stages = ctx.state.completed_stages()
    assert stages == [
        "write-spec",
        "commit-spec",
        "write-implementation-plan",
        "write-acceptance-tests",
        "write-code",
        "final-review-and-fixes",
    ]
    assert ctx.state.is_completed()
    assert ("human", "write-spec") in order
    # The plan gate runs after the AI review and before the plan is committed
    # (the trailing work call is commit_work's commit prompt).
    assert [e for e in order if e[1] == "write-implementation-plan"] == [
        ("work", "write-implementation-plan"),
        ("review", "write-implementation-plan"),
        ("human-plan", "write-implementation-plan"),
        ("work", "write-implementation-plan"),
    ]
    assert ("finish", "demo task") in order
    assert (ctx.spec_dir / "plan.md").read_text(encoding="utf-8").startswith("- [x]")
