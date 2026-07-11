"""Port of tests/resume.test.sh scenarios 1-4 and 6 (offline)."""

import os
import sys
from pathlib import Path

from adversarial_ai_coding import cli

FAKE = str(Path(__file__).parent / "fake_agent.py")


def _make_wrapper(work: Path, role: str) -> str:
    if os.name == "nt":
        path = work / f"fake-{role}.cmd"
        path.write_text(
            f'@"{sys.executable}" "{FAKE}" --role fake-{role} %*\r\n',
            encoding="utf-8",
        )
    else:
        path = work / f"fake-{role}"
        path.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" --role fake-{role} "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
    return str(path)


def wf_env(work: Path, **overrides) -> dict:
    env = {
        "HUMAN_GATE": "0",
        "DUAL_SPEC": "0",
        "AUTO_BRANCH": "1",
        "USE_WORKTREE": "0",
        "OPEN_PR": "0",
        "GATE_CMD": "exit 0",
        "BUILD_GATE_CMD": "exit 0",
        "RETRY_ON_LIMIT": "1",
        "NOTIFY_CMD": "",
        "FAKE_CALLS_LOG": str(work / "calls.log"),
        "FAKE_ABORT_ON": str(work / "abort-on"),
        "AGENT_A": _make_wrapper(work, "worker"),
        "AGENT_B": _make_wrapper(work, "reviewer"),
    }
    env.update(overrides)
    return env


def run_cli(repo, env, args=None, monkeypatch=None):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    argv = ["demo task"] if args is None else args
    return cli.main(argv, env, stdin_isatty=False)


def state_dir_of(repo: Path) -> Path:
    return next((repo / ".workflow" / "state").iterdir())


def calls(work: Path, pattern: str) -> int:
    log = work / "calls.log"
    if not log.is_file():
        return 0
    return sum(
        1
        for line in log.read_text(encoding="utf-8").splitlines()
        if line == pattern
    )


def driver_workdir(tmp_path: Path) -> Path:
    return tmp_path.parent / f"{tmp_path.name}-driver"


def test_scenario1_quota_abort_then_resume(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    (work / "abort-on").write_text("write-plan\n", encoding="utf-8")

    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 75
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState

    st = RunState(state_dir=state, run_id=state.name)
    stages = st.completed_stages()
    assert "write-spec" in stages and "commit-spec" in stages
    assert "write-implementation-plan" not in stages
    spec_calls_before = calls(work, "fake-worker write-spec")

    (work / "abort-on").unlink()
    env_resume = dict(env, RESUME_RUN=state.name)
    rc = run_cli(new_repo, env_resume, args=[], monkeypatch=monkeypatch)
    assert rc == 0
    assert calls(work, "fake-worker write-spec") == spec_calls_before
    assert (state / "completed").is_file()
    plan = next((new_repo / "specs").glob("*/plan.md"))
    text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in text and "- [x]" in text

    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN="last"),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 1
    )
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN="nonexistent"),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 1
    )


def test_plan_gate_asks_and_commits_the_human_edit(new_repo, tmp_path, monkeypatch):
    """HUMAN_GATE_PLAN=1 drives the real CLI with a simulated terminal."""

    from adversarial_ai_coding import workflow as wf_mod
    from adversarial_ai_coding.gitops import git_out, status_porcelain

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, HUMAN_GATE="0", HUMAN_GATE_PLAN="1")
    asked = []

    def fake_input(prompt=""):
        asked.append(prompt)
        plan = next((new_repo / "specs").glob("*/plan.md"))
        plan.write_text(
            plan.read_text(encoding="utf-8") + "- [ ] task the human added\n",
            encoding="utf-8",
        )
        return "y"

    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(new_repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert cli.main(["demo task"], env, stdin_isatty=True) == 0
    assert len(asked) == 1

    plan = next((new_repo / "specs").glob("*/plan.md"))
    rel = plan.relative_to(new_repo).as_posix()
    added_in = git_out(
        ["log", "--diff-filter=A", "--format=%H", "--", rel], new_repo
    )
    # The gate ran before the plan commit: the human edit is in the commit that
    # first added plan.md, not in a later one.
    assert "task the human added" in git_out(["show", f"{added_in}:{rel}"], new_repo)
    text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in text and "- [x]" in text
    assert status_porcelain(new_repo) == ""


def test_scenario2_lost_ledger_line_reruns_stage(
    new_repo, tmp_path, monkeypatch
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState

    st = RunState(state_dir=state, run_id=state.name)
    (state / "completed").unlink()
    stages = st.completed_stages()
    st._write_ledger(stages[:-1])
    before = calls(work, "fake-worker final-review")
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 0
    )
    assert calls(work, "fake-worker final-review") == before + 1


def test_scenario3_acceptance_window_keeps_base(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState

    st = RunState(state_dir=state, run_id=state.name)
    base_before = (state / "acceptance-test-base").read_text(encoding="utf-8")
    (state / "completed").unlink()
    st._write_ledger(
        [stage for stage in st.completed_stages() if stage != "write-acceptance-tests"]
    )
    (new_repo / ".workflow" / "protected-tests.txt").unlink()
    (new_repo / ".workflow" / "protected-base.sha").unlink()
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 0
    )
    rebuilt = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert "acc/acceptance.txt" in rebuilt
    assert (
        state / "acceptance-test-base"
    ).read_text(encoding="utf-8") == base_before


def test_scenario4_empty_queue_no_fallback(
    new_repo, tmp_path, monkeypatch, capsys
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState

    st = RunState(state_dir=state, run_id=state.name)
    (state / "completed").unlink()
    st._write_ledger([stage for stage in st.completed_stages() if stage != "write-code"])
    before = calls(work, "fake-worker implement")
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 0
    )
    assert calls(work, "fake-worker implement") == before
    assert "falling back to one whole-plan implementation task" not in (
        capsys.readouterr().err
    )


def test_scenario6_damaged_snapshot_refused_then_restored(
    new_repo, tmp_path, monkeypatch
):
    import json

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    (work / "abort-on").write_text("implement\n", encoding="utf-8")
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc != 0
    state = state_dir_of(new_repo)
    snapshot_path = state / "settings.json"
    backup = snapshot_path.read_text(encoding="utf-8")

    payload = json.loads(backup)
    payload["evil_key"] = "1"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 1
    )

    payload = json.loads(backup)
    del payload["schema"]
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 1
    )

    snapshot_path.write_text(backup, encoding="utf-8")
    (work / "abort-on").unlink()
    assert (
        run_cli(
            new_repo,
            dict(env, RESUME_RUN=state.name),
            args=[],
            monkeypatch=monkeypatch,
        )
        == 0
    )
