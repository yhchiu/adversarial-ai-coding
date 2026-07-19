"""cli.py styler wiring: colored terminal, plain run log and redirects.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

import os
import sys
from pathlib import Path

from adversarial_ai_coding import cli
from adversarial_ai_coding.style import Styler

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
        "RETRY_ON_LIMIT": "0",
        "NOTIFY_CMD": "",
        "FAKE_CALLS_LOG": str(work / "calls.log"),
        "FAKE_ABORT_ON": str(work / "abort-on"),
        "AGENT_A": _make_wrapper(work, "worker"),
        "AGENT_B": _make_wrapper(work, "reviewer"),
    }
    env.update(overrides)
    return env


def driver_workdir(tmp_path: Path) -> Path:
    return tmp_path.parent / f"{tmp_path.name}-driver"


def run_cli(repo, env, monkeypatch, args=None):
    # The fake-agent wrappers run as subprocesses and read FAKE_* from the
    # real process environment, so every var goes through setenv too.
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    argv = ["demo task"] if args is None else args
    return cli.main(argv, env, stdin_isatty=False)


def run_log_text(repo: Path) -> str:
    logs = list((repo / ".workflow" / "runs").rglob("*-run.log"))
    assert logs, "expected an archived run log"
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def test_invalid_color_value_fails_fast(new_repo, tmp_path, monkeypatch, capsys):
    # Runs inside the throwaway repo with fake agents so the pre-wiring
    # red run is safe: it completes a fake workflow instead of failing
    # fast, and the rc assertion is what fails.
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, COLOR="sometimes")
    rc = run_cli(new_repo, env, monkeypatch)
    assert rc == 1
    assert "COLOR must be auto, always, or never" in capsys.readouterr().err


def test_full_run_color_always_paints_terminal_but_not_run_log(
    new_repo, tmp_path, monkeypatch, capsys
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, COLOR="always")
    assert run_cli(new_repo, env, monkeypatch) == 0
    out = capsys.readouterr().out
    # Stage banner and progress lines carry dark-theme SGR codes.
    assert "\x1b[1;96m================" in out
    assert "\x1b[36m>>> " in out
    # The archived run log stays plain.
    assert "\x1b[" not in run_log_text(new_repo)


def test_full_run_auto_mode_emits_no_codes_when_not_a_tty(
    new_repo, tmp_path, monkeypatch, capsys
):
    # pytest capture streams are not ttys, so auto behaves like a redirect.
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch) == 0
    captured = capsys.readouterr()
    assert "\x1b[" not in captured.out
    assert "\x1b[" not in captured.err
    assert "\x1b[" not in run_log_text(new_repo)


def test_ctx_log_file_stays_plain_when_echo_is_styled(make_ctx, capsys):
    styler = Styler.from_env(
        {"COLOR": "always"},
        stdout_isatty=False,
        stderr_isatty=False,
        enable_vt=lambda _stream: True,
    )
    ctx = make_ctx()
    ctx.echo = styler.out
    ctx.log("!! Protected acceptance test files were modified:")
    assert "\x1b[1;91m" in capsys.readouterr().out
    log_text = ctx.archive.log_path.read_text(encoding="utf-8")
    assert "\x1b[" not in log_text
    assert "!! Protected acceptance test files were modified:" in log_text
