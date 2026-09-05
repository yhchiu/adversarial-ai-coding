"""Deterministic quality gates (sh:771-787, 1356-1380).

Anything machine-verifiable is run by the script; AI claims about test
status are only hints.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import WorkflowAbort
from .i18n import emit
from .prompts import render_prompt


def detect_gate(cwd: Path) -> str:
    if (cwd / "go.mod").is_file():
        return "go build ./... && go vet ./... && go test ./..."
    package = cwd / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get(
                "scripts", {}
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            scripts = {}
        if scripts.get("test"):
            return "npm test"
        return ""
    if (cwd / "Cargo.toml").is_file():
        return "cargo test"
    if _is_python_project(cwd) and _uses_pytest(cwd) and _has_test_files(cwd):
        return _pytest_command(cwd)
    return ""


def _is_python_project(cwd: Path) -> bool:
    return any(
        (cwd / marker).is_file()
        for marker in ("pyproject.toml", "setup.py", "setup.cfg")
    )


def _uses_pytest(cwd: Path) -> bool:
    """Whether the project says pytest is its runner.

    A marker file alone would claim a gate for a unittest or nose project
    that pytest may not be able to run at all, the same reason a
    package.json without a test script claims nothing.
    """
    if (cwd / "pytest.ini").is_file():
        return True
    sections = {
        "pyproject.toml": "pytest",
        "tox.ini": "[pytest]",
        "setup.cfg": "[tool:pytest]",
    }
    for name, needle in sections.items():
        path = cwd / name
        if not path.is_file():
            continue
        try:
            if needle in path.read_text(encoding="utf-8"):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def _has_test_files(cwd: Path) -> bool:
    """Whether pytest would collect anything here.

    pytest exits 5 when it collects no tests, which a gate reads as a
    failure. Without this check the workflow would send an agent to repair
    code that is not broken, once per round, until MAX_ROUNDS.
    """
    patterns = ("test_*.py", "*_test.py")
    if any(next(cwd.glob(pattern), None) is not None for pattern in patterns):
        return True
    tests = cwd / "tests"
    return tests.is_dir() and any(
        next(tests.rglob(pattern), None) is not None for pattern in patterns
    )


def _pytest_command(cwd: Path) -> str:
    """The command that runs this project's pytest, not ours.

    sys.executable is deliberately unused: it is the interpreter running
    the workflow, whose environment holds neither the target project's
    dependencies nor necessarily pytest itself, so a gate built from it
    would fail for reasons that have nothing to do with the code under
    test. A lock file names the tool that owns the environment; failing
    that, the project's own .venv is the interpreter its tests need; only
    then does the spelling that exists on this machine decide.
    """
    if (cwd / "uv.lock").is_file():
        return "uv run pytest"
    if (cwd / "poetry.lock").is_file():
        return "poetry run pytest"
    for venv in (
        Path(".venv") / "Scripts" / "python.exe",
        Path(".venv") / "bin" / "python",
    ):
        # Gates run with cwd set to the workspace, so a relative path needs
        # no quoting: .venv never contains a space.
        if (cwd / venv).is_file():
            return f"{venv} -m pytest"
    for name in ("python", "python3"):
        if shutil.which(name):
            return f"{name} -m pytest"
    return ""


def detect_build_gate(cwd: Path) -> str:
    if (cwd / "go.mod").is_file():
        return "go build ./..."
    if (cwd / "Cargo.toml").is_file():
        return "cargo build"
    return ""


def run_shell(cmd: str, cwd: Path) -> tuple[int, str]:
    # Divergence: bash ran `bash -c "$cmd"`; shell=True uses the platform
    # shell (cmd.exe on Windows). Detected gates only chain with &&.
    proc = subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])


def gate_loop(
    cmd: str,
    *,
    cwd: Path,
    prompts_dir: Path,
    max_rounds: int,
    do_work: Callable[[str], None],
    log: Callable[..., None],
    notify: Callable[[str], None],
    stage: str,
    run_shell: Callable[[str, Path], tuple[int, str]] = run_shell,
) -> None:
    if not cmd:
        return
    attempt = 1
    while True:
        emit(log, ">>> Quality gate:{cmd}", cmd=cmd)
        rc, output = run_shell(cmd, cwd)
        if rc == 0:
            emit(log, "Quality gate passed")
            return
        emit(log, "Quality gate failed (attempt {attempt})", attempt=attempt)
        if attempt >= max_rounds:
            notify(
                f"adversarial-ai-coding:[{stage}] quality gate failed "
                "repeatedly; human intervention required"
            )
            raise WorkflowAbort(
                f"!! [{stage}] Quality gate failed {max_rounds} times; stopping "
                f"for human intervention. Output:\n{_tail(output, 50)}"
            )
        attempt += 1
        prompt = render_prompt(
            prompts_dir,
            "quality-gate-failed",
            {"COMMAND": cmd, "OUTPUT": _tail(output, 150)},
        )
        do_work(prompt)
