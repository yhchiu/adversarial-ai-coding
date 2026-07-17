"""Ports helpers.test.sh:191-218 for work and protected-test recovery."""

import subprocess

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.agents import AgentResult
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC
from adversarial_ai_coding.workflow import check_protected, work


def _write_controls(ctx, paths="acc_test.go\n", base=None):
    base_text = f"{head_sha(ctx.workspace)}\n" if base is None else base
    (ctx.wf / "protected-tests.txt").write_text(paths, encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text(base_text, encoding="utf-8")
    wf_mod.activate_protected_controls(ctx)


def test_work_archives_prompt_and_sends_file_reference(make_ctx, monkeypatch):
    ctx = make_ctx()
    seen = {}

    def fake_worker(name, prompt, settings, session, io):
        seen["prompt"] = prompt
        io.agent_out.write_text("ok\n", encoding="utf-8")
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    work(ctx, ctx.ref("A"), "FULL_PROMPT_SENTINEL for worker")

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
        work(ctx, ctx.ref("A"), "prompt")
    assert exc.value.rc == QUOTA_ABORT_RC


def test_work_ordinary_agent_failure_does_not_raise(make_ctx, monkeypatch):
    ctx = make_ctx()

    def failing_worker(name, prompt, settings, session, io):
        io.agent_out.write_text("undefined: IsPalindrome\n", encoding="utf-8")
        return AgentResult(1, "build error")

    monkeypatch.setattr(wf_mod, "run_worker", failing_worker)
    work(ctx, ctx.ref("A"), "prompt")


@pytest.mark.parametrize(
    ("target", "action"),
    [
        ("protected-tests.txt", "modify"),
        ("protected-tests.txt", "empty"),
        ("protected-tests.txt", "delete"),
        ("protected-base.sha", "modify"),
        ("protected-base.sha", "empty"),
        ("protected-base.sha", "directory"),
        ("protected-tests.txt", "invalid-utf8"),
    ],
)
def test_work_aborts_when_worker_tampers_with_protected_controls(
    make_ctx, monkeypatch, target, action
):
    ctx = make_ctx()
    _write_controls(ctx)
    path = ctx.wf / target

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        if action == "modify":
            path.write_text("forged\n", encoding="utf-8")
        elif action == "empty":
            path.write_bytes(b"")
        elif action == "delete":
            path.unlink()
        elif action == "directory":
            path.unlink()
            path.mkdir()
        else:
            path.write_bytes(b"\xff")
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)

    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")


def test_work_rejects_preexisting_control_tampering_before_agent_call(
    make_ctx, monkeypatch
):
    ctx = make_ctx()
    _write_controls(ctx)
    (ctx.wf / "protected-base.sha").write_text("forged\n", encoding="utf-8")
    called = False

    def fake_worker(*args, **kwargs):
        nonlocal called
        called = True
        return AgentResult(0, "unexpected")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)

    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")
    assert called is False


def test_empty_protected_list_still_activates_control_integrity(
    make_ctx, monkeypatch
):
    ctx = make_ctx()
    _write_controls(ctx, paths="")
    assert ctx.protected_controls is not None
    assert ctx.protected_controls.paths == frozenset()

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        (ctx.wf / "protected-tests.txt").write_text(
            "new-test.py\n", encoding="utf-8"
        )
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")


def test_tampering_error_keeps_archive_failure_as_cause(make_ctx, monkeypatch):
    ctx = make_ctx()
    _write_controls(ctx)

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        return AgentResult(0, "worker output")

    def fail_archive(*args, **kwargs):
        (ctx.wf / "protected-base.sha").write_text("forged\n", encoding="utf-8")
        raise OSError("archive failed")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    monkeypatch.setattr(ctx.archive, "archive_git_state", fail_archive)

    with pytest.raises(WorkflowAbort, match="protected control") as exc_info:
        work(ctx, ctx.ref("A"), "prompt")
    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "archive failed"


@pytest.mark.parametrize(
    ("target", "action"),
    [
        ("protected-tests.txt", "missing"),
        ("protected-tests.txt", "invalid-utf8"),
        ("protected-base.sha", "empty"),
        ("protected-base.sha", "invalid-utf8"),
        ("protected-base.sha", "directory"),
    ],
)
def test_activate_protected_controls_rejects_invalid_inputs(
    make_ctx, target, action
):
    ctx = make_ctx()
    protected = ctx.wf / "protected-tests.txt"
    base = ctx.wf / "protected-base.sha"
    protected.write_text("acc_test.go\n", encoding="utf-8")
    base.write_text(f"{head_sha(ctx.workspace)}\n", encoding="utf-8")
    path = ctx.wf / target
    if action == "missing":
        path.unlink()
    elif action == "empty":
        path.write_bytes(b"")
    elif action == "invalid-utf8":
        path.write_bytes(b"\xff")
    else:
        path.unlink()
        path.mkdir()

    with pytest.raises(WorkflowAbort, match="protected control"):
        wf_mod.activate_protected_controls(ctx)


def test_acceptance_control_target_allows_missing_and_regular_but_not_directory(
    make_ctx,
):
    ctx = make_ctx()
    path = ctx.wf / "protected-tests.txt"
    wf_mod._require_regular_or_missing_control(path)
    path.write_text("old\n", encoding="utf-8")
    wf_mod._require_regular_or_missing_control(path)
    path.unlink()
    path.mkdir()
    with pytest.raises(WorkflowAbort, match="non-regular protected control"):
        wf_mod._require_regular_or_missing_control(path)


def test_check_protected_repairs_then_stops(make_ctx, monkeypatch):
    ctx = make_ctx()
    (ctx.wf / "protected-tests.txt").write_text("acc_test.go\n", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("basesha\n", encoding="utf-8")
    wf_mod.activate_protected_controls(ctx)
    monkeypatch.setattr(
        wf_mod,
        "protected_violations",
        lambda protected, base, cwd: ["acc_test.go"],
    )
    repair_prompts = []
    monkeypatch.setattr(
        wf_mod, "work", lambda ctx, agent, prompt: repair_prompts.append(prompt)
    )
    with pytest.raises(WorkflowAbort, match="human intervention"):
        check_protected(ctx, "claude")
    assert len(repair_prompts) == 2


def test_check_protected_uses_snapshot_paths_and_base(make_ctx, monkeypatch):
    ctx = make_ctx()
    _write_controls(ctx, paths="acc_test.go\n", base="snapshot-base\n")
    (ctx.wf / "protected-tests.txt").write_text("forged.py\n", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("forged-base\n", encoding="utf-8")
    captured = []

    def capture(protected, base, cwd):
        captured.append((protected, base, cwd))
        return []

    monkeypatch.setattr(wf_mod, "protected_violations", capture)

    check_protected(ctx, ctx.ref("A"))

    assert captured == [
        (frozenset({"acc_test.go"}), "snapshot-base", ctx.workspace)
    ]


def test_check_protected_fails_closed_when_git_diff_fails(make_ctx):
    ctx = make_ctx()
    _write_controls(ctx, base="not-a-valid-base\n")

    with pytest.raises(WorkflowAbort, match="git diff failed closed") as exc_info:
        check_protected(ctx, ctx.ref("A"))

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)


def test_check_protected_noop_without_protection_files(make_ctx):
    ctx = make_ctx()
    check_protected(ctx, "claude")
