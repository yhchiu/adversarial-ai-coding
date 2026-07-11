"""Ports helpers.test.sh:191-218 for work and protected-test recovery."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.agents import AgentResult
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC
from adversarial_ai_coding.workflow import check_protected, work


def test_work_archives_prompt_and_sends_file_reference(make_ctx, monkeypatch):
    ctx = make_ctx()
    seen = {}

    def fake_worker(name, prompt, settings, session, io):
        seen["prompt"] = prompt
        io.agent_out.write_text("ok\n", encoding="utf-8")
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    work(ctx, "claude", "FULL_PROMPT_SENTINEL for worker")

    assert "Read the full workflow prompt" in seen["prompt"]
    assert "worker-stage-r1-prompt.md" in seen["prompt"]
    assert "FULL_PROMPT_SENTINEL" not in seen["prompt"]
    artifacts = list(ctx.archive.run_dir.glob("*-worker-stage-r1-prompt.md"))
    assert artifacts
    assert "FULL_PROMPT_SENTINEL for worker" in artifacts[0].read_text(
        encoding="utf-8"
    )
    outputs = list(ctx.archive.run_dir.glob("*-worker-stage-r1-output.txt"))
    assert outputs and "worker output" in outputs[0].read_text(encoding="utf-8")
    assert ctx.archive.metrics_path.is_file()


def test_work_quota_abort_raises_resumable(make_ctx, monkeypatch):
    ctx = make_ctx()

    def limited_worker(name, prompt, settings, session, io):
        io.agent_out.write_text("You've hit your usage limit\n", encoding="utf-8")
        return AgentResult(1, "limited")

    monkeypatch.setattr(wf_mod, "run_worker", limited_worker)
    with pytest.raises(WorkflowAbort) as exc:
        work(ctx, "claude", "prompt")
    assert exc.value.rc == QUOTA_ABORT_RC


def test_work_ordinary_agent_failure_does_not_raise(make_ctx, monkeypatch):
    ctx = make_ctx()

    def failing_worker(name, prompt, settings, session, io):
        io.agent_out.write_text("undefined: IsPalindrome\n", encoding="utf-8")
        return AgentResult(1, "build error")

    monkeypatch.setattr(wf_mod, "run_worker", failing_worker)
    work(ctx, "claude", "prompt")


def test_check_protected_repairs_then_stops(make_ctx, monkeypatch):
    ctx = make_ctx()
    (ctx.wf / "protected-tests.txt").write_text("acc_test.go\n", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("basesha\n", encoding="utf-8")
    monkeypatch.setattr(
        wf_mod,
        "protected_violations",
        lambda protected_file, base, cwd: ["acc_test.go"],
    )
    repair_prompts = []
    monkeypatch.setattr(
        wf_mod, "work", lambda ctx, agent, prompt: repair_prompts.append(prompt)
    )
    with pytest.raises(WorkflowAbort, match="human intervention"):
        check_protected(ctx, "claude")
    assert len(repair_prompts) == 2


def test_check_protected_noop_without_protection_files(make_ctx):
    ctx = make_ctx()
    check_protected(ctx, "claude")
