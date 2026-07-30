"""Offline workflow harness: fake agents, env, and cli.main invocation.

Four test modules drive the real CLI against tests/fake_agent.py. The
helpers used to live in test_resume_integration.py and were imported from
there (and, in one case, copy-pasted), which made that module both a test
file and everyone's harness. They live here instead.
"""

import os
import sys
from pathlib import Path

from adversarial_ai_coding import cli

FAKE = str(Path(__file__).parent / "fake_agent.py")


def make_wrapper(work: Path, role: str) -> str:
    """A PATH-resolvable command that runs the fake agent in this role."""
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
        "AGENT_A": make_wrapper(work, "worker"),
        "AGENT_B": make_wrapper(work, "reviewer"),
    }
    env.update(overrides)
    return env


def run_cli(repo, env, args=None, monkeypatch=None):
    # The fake-agent wrappers run as subprocesses and read FAKE_* from the
    # real process environment, so every var goes through setenv too.
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    argv = ["demo task"] if args is None else args
    return cli.main(argv, env, stdin_isatty=False)


def state_dir_of(repo: Path) -> Path:
    return next((repo / "aac/.run" / "state").iterdir())


def calls(work: Path, pattern: str) -> int:
    log = work / "calls.log"
    if not log.is_file():
        return 0
    return sum(
        1
        for line in log.read_text(encoding="utf-8").splitlines()
        if line == pattern
    )


def implementation_tasks(work: Path, role: str) -> list[str]:
    log = work / "implementation-tasks.log"
    if not log.is_file():
        return []
    prefix = f"{role} "
    return [
        line.removeprefix(prefix)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]


def driver_workdir(tmp_path: Path) -> Path:
    return tmp_path.parent / f"{tmp_path.name}-driver"
