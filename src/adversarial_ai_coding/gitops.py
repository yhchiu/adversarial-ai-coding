"""Git operations: workspace lifecycle, protected files, fallback commits.

Port of adversarial-ai-coding.sh:789-792, 1449-1456, and 1750-1810.
Everything takes an explicit cwd; nothing changes the process directory.
"""

from __future__ import annotations

import subprocess
from collections.abc import Collection
from pathlib import Path
from typing import Callable

from .config import Settings
from .runstate import RunState, RunStateError


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def git_out(args: list[str], cwd: Path) -> str:
    proc = _git(args, cwd)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, ["git", *args], proc.stdout, proc.stderr
        )
    return proc.stdout.strip()


def is_inside_work_tree(cwd: Path) -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"], cwd).returncode == 0


def current_branch(cwd: Path) -> str:
    return git_out(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def head_sha(cwd: Path) -> str:
    return git_out(["rev-parse", "HEAD"], cwd)


def status_porcelain(cwd: Path) -> str:
    return git_out(["status", "--porcelain"], cwd)


def protected_violations(
    protected: Collection[str], base: str, cwd: Path
) -> list[str]:
    if not protected:
        return []
    changed = [
        line
        for line in git_out(["diff", "--name-only", base, "--"], cwd).splitlines()
        if line
    ]
    return [name for name in changed if name in protected]


def ensure_committed(
    cwd: Path, stage: str, echo_err: Callable[[str], None]
) -> None:
    if not status_porcelain(cwd):
        return
    echo_err("(worker left uncommitted changes; script is creating a fallback commit)")
    git_out(["add", "-A"], cwd)
    git_out(
        [
            "commit",
            "-m",
            f"chore: commit remaining {stage} changes",
            "-m",
            "Auto-committed by adversarial-ai-coding because the worker left "
            "uncommitted changes.",
        ],
        cwd,
    )


def verify_last_head(
    state: RunState, cwd: Path, echo_err: Callable[[str], None]
) -> None:
    recorded = state.read_last_head()
    if recorded is None:
        if state.completed_stages():
            raise RunStateError(
                f"!! Run {state.run_id} has completed stages but no last-head "
                "checkpoint; the state is damaged.\n   Start a fresh run, or "
                f"delete {state.state_dir} if you no longer need it."
            )
        return
    head = head_sha(cwd)
    if head == recorded:
        return
    if _git(["merge-base", "--is-ancestor", recorded, "HEAD"], cwd).returncode == 0:
        echo_err(
            f"(warning: new commits exist after the resume checkpoint "
            f"{recorded}; continuing)"
        )
        return
    raise RunStateError(
        f"!! The resume checkpoint {recorded} is not reachable from HEAD "
        "(branch reset/rebase, or the wrong repository).\n"
        f"   Fix the branch first, or delete {state.state_dir / 'last-head'} "
        "to force the resume."
    )


def resume_workspace(
    resumed_branch: str,
    state: RunState,
    cwd: Path,
    echo_err: Callable[[str], None],
) -> None:
    current = current_branch(cwd)
    if not resumed_branch:
        echo_err(
            f"(warning: the resume snapshot has no branch record; staying on {current})"
        )
    elif current != resumed_branch:
        exists = (
            _git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{resumed_branch}"],
                cwd,
            ).returncode
            == 0
        )
        if not exists:
            raise RunStateError(
                f"!! The resumed run's branch {resumed_branch} no longer exists "
                "in this repository.\n   If the run used USE_WORKTREE=1, cd "
                "into its worktree and resume there."
            )
        git_out(["switch", resumed_branch], cwd)
    verify_last_head(state, cwd, echo_err)
    if status_porcelain(cwd):
        echo_err(
            "!! The working tree is dirty. These changes will be absorbed "
            "into the next automatic commit (git add -A):"
        )
        echo_err(git_out(["status", "--short"], cwd))


def setup_workspace(settings: Settings, run_id: str, cwd: Path) -> Path:
    if settings.use_worktree:
        root = Path(git_out(["rev-parse", "--show-toplevel"], cwd))
        worktree = root.parent / f"{root.name}-auto-{run_id}"
        git_out(
            ["worktree", "add", "-b", f"auto/{run_id}", str(worktree)], cwd
        )
        return worktree
    if settings.auto_branch:
        git_out(["switch", "-c", f"auto/{run_id}"], cwd)
    return cwd
