"""Ports helpers.test.sh:260-272, 364-374, 220-250, 1042-1049."""

import json

import pytest

from adversarial_ai_coding import review as review_mod
from adversarial_ai_coding.agents import AgentResult
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.prompts import default_prompts_dir
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC
from adversarial_ai_coding.review import (
    compose_review_prompt,
    review_loop,
    run_review,
    verdict_approved,
)

PROMPTS = default_prompts_dir({})


def test_verdict_approved_cases(tmp_path):
    verdict = tmp_path / "v.json"
    verdict.write_text(
        '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
    )
    assert verdict_approved(verdict)
    verdict.write_text(
        '{"approved":false,"blockers":["x"],"suggestions":[]}',
        encoding="utf-8",
    )
    assert not verdict_approved(verdict)
    assert not verdict_approved(tmp_path / "nothere.json")
    verdict.write_text("not json at all", encoding="utf-8")
    assert not verdict_approved(verdict)


def test_compose_review_prompt_verdict_instruction(tmp_path):
    claude = compose_review_prompt("claude", "scope", PROMPTS, tmp_path / ".workflow")
    codex = compose_review_prompt("codex", "scope", PROMPTS, tmp_path / ".workflow")
    custom = compose_review_prompt(
        "custom-agent", "scope", PROMPTS, tmp_path / ".workflow"
    )
    assert "Finally write the verdict" not in claude
    assert "Finally write the verdict" in codex
    assert "Finally write the verdict" in custom
    assert "scope" in claude


def approving_reviewer(verdict=None):
    def fake(name, prompt, settings, session, io):
        io.agent_out.write_text("reviewed\n", encoding="utf-8")
        payload = verdict or {"approved": True, "blockers": [], "suggestions": []}
        io.verdict_path.write_text(json.dumps(payload), encoding="utf-8")
        return AgentResult(0, "review text")

    return fake


def test_run_review_archives_and_approves(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage, ctx.cur_round = "review", 2
    seen = {}

    def fake_reviewer(name, prompt, settings, session, io):
        seen["prompt"] = prompt
        io.agent_out.write_text("reviewed\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
        )
        (ctx.wf / "review.md").write_text("approved\n", encoding="utf-8")
        return AgentResult(0, "review text")

    monkeypatch.setattr(review_mod, "run_reviewer", fake_reviewer)
    assert run_review(ctx, "codex", "FULL_PROMPT_SENTINEL review scope") is True
    assert "Read the full workflow prompt" in seen["prompt"]
    assert "reviewer-review-r2-prompt.md" in seen["prompt"]
    assert "FULL_PROMPT_SENTINEL" not in seen["prompt"]
    artifacts = list(ctx.archive.run_dir.glob("*-reviewer-review-r2-prompt.md"))
    assert artifacts
    assert "FULL_PROMPT_SENTINEL review scope" in artifacts[0].read_text(
        encoding="utf-8"
    )


def test_run_review_prewrites_failed_sentinel(make_ctx, monkeypatch):
    ctx = make_ctx()

    def silent_reviewer(name, prompt, settings, session, io):
        io.agent_out.write_text("said nothing structured\n", encoding="utf-8")
        return AgentResult(0, "prose only")

    monkeypatch.setattr(review_mod, "run_reviewer", silent_reviewer)
    assert run_review(ctx, "codex", "scope") is False
    verdict = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is False
    assert verdict["blockers"] == ["reviewer did not write a verdict"]


def test_run_review_quota_abort(make_ctx, monkeypatch):
    ctx = make_ctx()

    def limited_reviewer(name, prompt, settings, session, io):
        io.agent_out.write_text("You've hit your usage limit\n", encoding="utf-8")
        return AgentResult(1, "limited")

    monkeypatch.setattr(review_mod, "run_reviewer", limited_reviewer)
    with pytest.raises(WorkflowAbort) as exc:
        run_review(ctx, "codex", "scope")
    assert exc.value.rc == QUOTA_ABORT_RC


def test_run_review_collects_suggestions(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-spec"
    monkeypatch.setattr(
        review_mod,
        "run_reviewer",
        approving_reviewer(
            {"approved": True, "blockers": [], "suggestions": ["tighten naming"]}
        ),
    )
    run_review(ctx, "codex", "scope")
    text = ctx.suggestions_path.read_text(encoding="utf-8")
    assert "## write-spec(round 1)" in text
    assert "- tighten naming" in text
    ctx.collect_review_suggestions = False
    run_review(ctx, "codex", "scope")
    assert text == ctx.suggestions_path.read_text(encoding="utf-8")


def test_review_loop_repair_round_then_approval(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-code"
    outcomes = iter([False, True])
    monkeypatch.setattr(
        review_mod, "run_review", lambda ctx, agent, scope: next(outcomes)
    )
    repairs = []
    monkeypatch.setattr(
        review_mod, "work", lambda ctx, agent, prompt: repairs.append(prompt)
    )
    gates_run = []
    monkeypatch.setattr(
        review_mod, "gate_loop", lambda cmd, **kwargs: gates_run.append(cmd)
    )
    review_loop(ctx, "codex", "claude", "scope", gate_cmd="go test ./...")
    assert len(repairs) == 1
    assert "review.md" in repairs[0]
    assert gates_run == ["go test ./..."]
    assert ctx.cur_round == 2


def test_review_loop_max_rounds_aborts(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(review_mod, "run_review", lambda ctx, agent, scope: False)
    monkeypatch.setattr(review_mod, "work", lambda ctx, agent, prompt: None)
    with pytest.raises(WorkflowAbort, match="Review still failed"):
        review_loop(ctx, "codex", "claude", "scope")
