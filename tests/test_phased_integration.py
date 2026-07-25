"""Offline PHASES=1 scenarios with fake agents (no AI cost)."""

import sys
from pathlib import Path

from test_resume_integration import (
    calls,
    driver_workdir,
    implementation_tasks,
    run_cli,
    state_dir_of,
    wf_env,
)

from adversarial_ai_coding import cli
from adversarial_ai_coding.runstate import RunState

EXPECTED_STAGES = [
    "write-spec",
    "commit-spec",
    "write-implementation-plan",
    "phase-01-write-tests",
    "phase-01-implement",
    "phase-02-write-tests",
    "phase-02-implement",
    "write-code",
    "final-review-and-fixes",
]


def phased_env(work: Path, **overrides) -> dict:
    # Red check / phase gate: fails until the fake implement step creates
    # src.txt, then passes. Phase 1 is red before implementation; phase 2
    # (regression-guard) is green because phase 1 already created src.txt.
    # If cmd.exe misparses the quoted two-path command on some setup, wrap
    # it in a .cmd file exactly like _make_wrapper in test_resume_integration.
    (work / "check_impl.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('src.txt').exists() else 1)\n",
        encoding="utf-8",
    )
    env = wf_env(
        work,
        PHASES="1",
        PHASE_GATE_CMD=f'"{sys.executable}" "{work / "check_impl.py"}"',
        FAKE_IMPLEMENTATION_TASKS_LOG=str(work / "implementation-tasks.log"),
    )
    env.update(overrides)
    return env


def test_phased_run_completes_and_appends_protection(
    new_repo, tmp_path, monkeypatch
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work)
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 0
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    assert st.completed_stages() == EXPECTED_STAGES
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"
    assert implementation_tasks(work, "fake-worker") == [
        "add feature one",
        "add feature two",
        "add regression fixture",
    ]
    plan = next((new_repo / "specs").glob("*/plan.md")).read_text(
        encoding="utf-8"
    )
    assert "- [ ] " not in plan and plan.count("- [x]") == 3
    assert calls(work, "fake-reviewer write-phase-tests") == 2
    assert calls(work, "fake-worker review") == 2
    assert calls(work, "fake-reviewer review") == 4


def test_phased_resume_skips_completed_phases(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work, FAKE_ABORT_ON_NTH="2")
    (work / "abort-on").write_text("write-phase-tests\n", encoding="utf-8")
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 75
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    stages = st.completed_stages()
    assert "phase-01-implement" in stages
    assert "phase-02-write-tests" not in stages

    (work / "abort-on").unlink()
    rc = run_cli(
        new_repo,
        dict(env, RESUME_RUN=state.name),
        args=[],
        monkeypatch=monkeypatch,
    )
    assert rc == 0
    assert (state / "completed").is_file()
    # phase 1 tests were not rewritten: 1 before the abort, the aborted
    # attempt, and 1 on resume for phase 2
    assert calls(work, "fake-reviewer write-phase-tests") == 3
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"


def test_phases_is_immutable_across_resume(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work, FAKE_ABORT_ON_NTH="1")
    (work / "abort-on").write_text("write-phase-tests\n", encoding="utf-8")
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 75
    state = state_dir_of(new_repo)
    rc = run_cli(
        new_repo,
        dict(env, RESUME_RUN=state.name, PHASES="0"),
        args=[],
        monkeypatch=monkeypatch,
    )
    assert rc == 1


def test_phase_review_runs_reviewer_per_phase(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work, PHASE_REVIEW="1")
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 0
    assert calls(work, "fake-reviewer review") == 6
    assert calls(work, "fake-worker review") == 2


def test_phase_gate_repair_is_committed_not_leaked(new_repo, tmp_path, monkeypatch):
    """A dirty phase-gate repair must not ride into the next phase's
    protected-test commit (GPT review blocker 1)."""
    work = driver_workdir(tmp_path)
    work.mkdir()
    # Gate: red while src.txt is missing; then fails once while dropping a
    # dirty repair file into the workspace (simulating an uncommitted fix
    # made during the gate loop); passes afterwards.
    (work / "flaky_gate.py").write_text(
        "import pathlib, sys\n"
        "if not pathlib.Path('src.txt').exists():\n"
        "    sys.exit(1)\n"
        "if not pathlib.Path('repair.txt').exists():\n"
        "    pathlib.Path('repair.txt').write_text('dirty repair\\n')\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    env = phased_env(
        work,
        PHASE_GATE_CMD=f'"{sys.executable}" "{work / "flaky_gate.py"}"',
    )
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 0
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"


def test_resume_after_controls_recorded_before_stage_end(
    new_repo, tmp_path, monkeypatch
):
    """Crash window: phase-2 tests committed and controls recorded, but
    phase-02-write-tests not yet in the ledger. Resume must finish the
    stage without re-running the test writer (GPT review blocker 5)."""
    from adversarial_ai_coding import workflow as wf_mod
    from adversarial_ai_coding.config import WorkflowAbort

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work)
    real_end = wf_mod.end_stage
    injected = []

    def crashing_end_stage(ctx):
        if ctx.cur_stage == "phase-02-write-tests" and not injected:
            injected.append(True)
            raise WorkflowAbort("injected: crash before the ledger write")
        real_end(ctx)

    monkeypatch.setattr(wf_mod, "end_stage", crashing_end_stage)
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 1
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    assert "phase-02-write-tests" not in st.completed_stages()

    rc = run_cli(
        new_repo,
        dict(env, RESUME_RUN=state.name),
        args=[],
        monkeypatch=monkeypatch,
    )
    assert rc == 0
    # The checkpoint keeps the resume from re-asking B for phase-2 tests
    # (the fake writes identical content, so tampering would not fire here;
    # a real writer varies content and exhausts the recovery loop).
    assert calls(work, "fake-reviewer write-phase-tests") == 2
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"


def test_spec_gate_suggestion_flips_run_to_phased(new_repo, tmp_path, monkeypatch):
    """PHASES unset; the reviewer suggests phased; the human says y twice."""

    import json

    from adversarial_ai_coding import workflow as wf_mod

    work = driver_workdir(tmp_path)
    work.mkdir()
    (work / "check_impl.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('src.txt').exists() else 1)\n",
        encoding="utf-8",
    )
    env = wf_env(
        work,
        HUMAN_GATE="1",
        PHASE_GATE_CMD=f'"{sys.executable}" "{work / "check_impl.py"}',
    )
    assert "PHASES" not in env
    asked = []
    answers = iter(["y", "y"])

    def fake_input(prompt=""):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(new_repo)
    monkeypatch.setenv("PYTHONPATH", "")
    monkeypatch.setenv(
        "FAKE_PHASED_SUGGESTION",
        '{"phased": true, "reason": "two independent features"}',
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert cli.main(["demo task"], env, stdin_isatty=True) == 0
    assert len(asked) == 2
    assert "Enable Phased ATDD" in asked[1]
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    assert st.completed_stages() == EXPECTED_STAGES
    snap = json.loads((state / "settings.json").read_text(encoding="utf-8"))
    assert snap["phases"] == "1"


def test_spec_gate_suggestion_declined_stays_single_shot(
    new_repo, tmp_path, monkeypatch
):
    import json

    from adversarial_ai_coding import workflow as wf_mod

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, HUMAN_GATE="1")
    answers = iter(["y", "n"])
    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.chdir(new_repo)
    monkeypatch.setenv("PYTHONPATH", "")
    monkeypatch.setenv(
        "FAKE_PHASED_SUGGESTION",
        '{"phased": true, "reason": "two independent features"}',
    )
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert cli.main(["demo task"], env, stdin_isatty=True) == 0
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    stages = st.completed_stages()
    assert "write-acceptance-tests" in stages
    assert not any(stage.startswith("phase-") for stage in stages)
    snap = json.loads((state / "settings.json").read_text(encoding="utf-8"))
    assert snap["phases"] == "0"
