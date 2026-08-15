"""Deterministic quality gates (sh:771-787, 1356-1380).

Anything machine-verifiable is run by the script; AI claims about test
status are only hints.
"""

from __future__ import annotations

import json
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
