"""Ports helpers.test.sh:376-493 and 900-940 (dual-spec helpers and restore)."""

import pytest

from adversarial_ai_coding import dual_spec as ds
from adversarial_ai_coding.config import WorkflowAbort


def dual_ctx(make_ctx, **extra_env):
    env = {
        "AGENT_A": "claude",
        "AGENT_B": "codex",
        "DUAL_SPEC": "1",
        "RETRY_ON_LIMIT": "0",
    }
    env.update(extra_env)
    ctx = make_ctx(env)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    return ctx


def test_normalize_decision():
    assert ds.normalize_dual_spec_decision("A") == "adopt-a"
    assert ds.normalize_dual_spec_decision("mb") == "merge-b"
    assert ds.normalize_dual_spec_decision("nope") is None
    assert ds.normalize_dual_spec_decision("") is None


def test_owner_slot_and_roles(make_ctx):
    ctx = dual_ctx(make_ctx)
    assert ds.dual_spec_owner_slot("adopt-a") == "A"
    assert ds.dual_spec_owner_slot("merge-b") == "B"
    assert ds.dual_spec_owner_slot("bogus") is None
    assert ds.agent_for_slot(ctx, "B") == "codex"
    assert ds.reviewer_slot_for_owner_slot("A") == "B"


def test_preflight_requires_human_gate_and_tty(make_ctx):
    ctx = dual_ctx(make_ctx, HUMAN_GATE="0")
    with pytest.raises(WorkflowAbort, match="requires HUMAN_GATE=1"):
        ds.dual_spec_preflight(ctx.settings, stdin_isatty=True)
    ctx2 = dual_ctx(make_ctx, HUMAN_GATE="1")
    with pytest.raises(WorkflowAbort, match="interactive terminal"):
        ds.dual_spec_preflight(ctx2.settings, stdin_isatty=False)
    ds.dual_spec_preflight(ctx2.settings, stdin_isatty=True)
    ctx3 = dual_ctx(make_ctx, DUAL_SPEC="0", HUMAN_GATE="0")
    ds.dual_spec_preflight(ctx3.settings, stdin_isatty=False)


def test_merge_request_template_is_not_content(make_ctx):
    ctx = dual_ctx(make_ctx)
    ds.write_spec_merge_request_template(ctx, "A", "B")
    assert ds.merge_request_has_content(ctx) is False


def test_merge_request_accepts_real_instructions(make_ctx):
    ctx = dual_ctx(make_ctx)
    (ctx.wf / "spec-merge-request.md").write_text(
        "# Dual Spec Merge Request\n\n## Items to adopt from B\n\n"
        "- adopt from Candidate B the stricter timeout acceptance criterion.\n"
        "- edge cases, especially empty task files, must be covered.\n",
        encoding="utf-8",
    )
    assert ds.merge_request_has_content(ctx) is True
    (ctx.wf / "spec-merge-request.md").write_text(
        "# Dual Spec Merge Request\n\n## Items to adopt from B\n\n"
        "adopt from Candidate B the stricter timeout acceptance criterion.\n"
        "edge cases, especially empty task files, must be covered.\n",
        encoding="utf-8",
    )
    assert ds.merge_request_has_content(ctx) is True
    (ctx.wf / "spec-merge-request.md").unlink()
    assert ds.merge_request_has_content(ctx) is False


def test_final_review_scope_merge_checks_adoption(make_ctx):
    ctx = dual_ctx(make_ctx)
    scope = ds.dual_spec_final_review_scope(ctx, "merge-b")
    assert "spec-merge-request.md" in scope
    assert "block approval" in scope
    plain = ds.dual_spec_final_review_scope(ctx, "adopt-a")
    assert "spec-merge-request.md" not in plain


def test_apply_adopt_reviews_and_gates(make_ctx, monkeypatch):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-a.md").write_text("candidate A\n", encoding="utf-8")
    (ctx.spec_dir / "spec-b.md").write_text("candidate B\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        ds,
        "review_loop",
        lambda ctx, reviewer, worker, scope: calls.append(
            ("review", reviewer, worker)
        ),
    )
    monkeypatch.setattr(ds, "human_gate_spec", lambda ctx: calls.append(("human",)))
    monkeypatch.setattr(
        ds,
        "work",
        lambda ctx, agent, prompt: pytest.fail("adopt path must not call work"),
    )
    ds.apply_dual_spec_decision(ctx, "adopt-a", "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "candidate A\n"
    assert calls == [("review", "codex", "claude"), ("human",)]


def test_apply_merge_calls_owner_then_reviews(make_ctx, monkeypatch):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-a.md").write_text("candidate A\n", encoding="utf-8")
    (ctx.spec_dir / "spec-b.md").write_text("candidate B\n", encoding="utf-8")
    (ctx.wf / "spec-merge-request.md").write_text("adopt item\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(
        ds,
        "review_loop",
        lambda ctx, reviewer, worker, scope: calls.append(
            ("review", reviewer, worker, scope)
        ),
    )
    monkeypatch.setattr(ds, "human_gate_spec", lambda ctx: calls.append(("human",)))

    def fake_work(ctx_arg, agent, prompt):
        calls.append(("work", agent))
        (ctx.spec_dir / "spec.md").write_text("merged B\n", encoding="utf-8")

    monkeypatch.setattr(ds, "work", fake_work)
    ds.apply_dual_spec_decision(ctx, "merge-b", "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "merged B\n"
    assert calls[0] == ("work", "codex")
    assert calls[1][0:3] == ("review", "claude", "codex")
    assert "block approval" in calls[1][3]
    assert calls[2] == ("human",)


def test_restore_decision_adopt_b(make_ctx):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-decision.md").write_text(
        "# Dual Spec Decision\n\n- decision: adopt-b\n- selected owner slot: B\n",
        encoding="utf-8",
    )
    ds.restore_dual_spec_decision(ctx)
    assert ctx.dual_spec_decision == "adopt-b"
    assert ctx.spec_roles.owner_agent == "codex"
    assert ctx.spec_roles.reviewer_agent == "claude"


def test_restore_merge_requires_merge_request(make_ctx):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-decision.md").write_text(
        "- decision: merge-b\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowAbort, match="run archive"):
        ds.restore_dual_spec_decision(ctx)
    (ctx.wf / "spec-merge-request.md").write_text("adopt item\n", encoding="utf-8")
    ds.restore_dual_spec_decision(ctx)
    assert ctx.dual_spec_decision == "merge-b"
    assert ctx.spec_roles.owner_agent == "codex"


def test_restore_invalid_or_missing_decision(make_ctx):
    ctx = dual_ctx(make_ctx)
    with pytest.raises(WorkflowAbort, match="no decision yet"):
        ds.restore_dual_spec_decision(ctx)
    (ctx.spec_dir / "spec-decision.md").write_text(
        "- decision: bogus\n", encoding="utf-8"
    )
    with pytest.raises(WorkflowAbort, match="Invalid decision"):
        ds.restore_dual_spec_decision(ctx)


def test_restore_existing_decision_left_alone(make_ctx):
    ctx = dual_ctx(make_ctx)
    ctx.dual_spec_decision = "adopt-a"
    ctx.spec_roles.owner_agent = "claude"
    ds.restore_dual_spec_decision(ctx)
    assert ctx.dual_spec_decision == "adopt-a"
    assert ctx.spec_roles.owner_agent == "claude"


def test_run_dual_spec_stage_uses_decision_variable(make_ctx, monkeypatch):
    ctx = dual_ctx(make_ctx)
    calls = []

    def fake_work(ctx_arg, agent, prompt):
        for name in (
            "spec-a.md",
            "spec-b.md",
            "spec-comparison-a.md",
            "spec-comparison-b.md",
        ):
            if name in prompt:
                (ctx.spec_dir / name).write_text(
                    f"made {name}\n", encoding="utf-8"
                )
                return

    monkeypatch.setattr(ds, "work", fake_work)
    monkeypatch.setattr(
        ds,
        "run_candidate_spec_review",
        lambda ctx, reviewer, scope, review_out, verdict_out: (
            review_out.write_text("review\n", encoding="utf-8"),
            verdict_out.write_text("{}", encoding="utf-8"),
        ),
    )

    def fake_selection(ctx_arg):
        print("log noise")
        ctx_arg.dual_spec_decision = "adopt-a"
        ds.write_dual_spec_decision_file(ctx_arg, "adopt-a")

    monkeypatch.setattr(ds, "human_gate_dual_spec_decision", fake_selection)
    monkeypatch.setattr(
        ds, "review_loop", lambda ctx, reviewer, worker, scope: calls.append("review")
    )
    monkeypatch.setattr(ds, "human_gate_spec", lambda ctx: calls.append("human"))
    ds.run_dual_spec_spec_stage(ctx, "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "made spec-a.md\n"
    assert calls == ["review", "human"]
    assert (ctx.spec_dir / "spec-comparison.md").is_file()
