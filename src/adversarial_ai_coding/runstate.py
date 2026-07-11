"""Resumable run state under .workflow/state/<run-id>/.

Port of adversarial-ai-coding.sh:66-330 (state block) plus the cross-stage
restore helpers. Format change approved by the spec: the settings snapshot
is settings.json and the stage ledger is ledger.json; both refuse unknown
schemas. The snapshot is parsed as data only, never executed (sh:67-69).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .config import Settings

SNAPSHOT_FILE = "settings.json"
SNAPSHOT_KEYS = (
    "spec_dir",
    "dual_spec",
    "auto_branch",
    "use_worktree",
    "branch",
    "agent_a",
    "agent_b",
    "agent_a_args",
    "agent_b_args",
    "model_a",
    "model_b",
    "claude_args",
    "codex_args",
    "agy_args",
    "max_rounds",
    "human_gate",
    "open_pr",
    "tools",
    "gate_cmd",
    "build_gate_cmd",
    "task_arg",
    "task_source_kind",
    "task_source_path",
)
IMMUTABLE_KEYS = ("SPEC_DIR", "DUAL_SPEC", "AUTO_BRANCH", "USE_WORKTREE")


class RunStateError(Exception):
    """A state problem that must stop the run before any AI call."""


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def snapshot_values(
    settings: Settings,
    *,
    branch: str,
    gate_cmd: str,
    build_gate_cmd: str,
    task_arg: str,
    task_source_kind: str,
    task_source_path: str,
) -> dict[str, str]:
    def flag(value: bool) -> str:
        return "1" if value else "0"

    return {
        "spec_dir": settings.spec_dir,
        "dual_spec": flag(settings.dual_spec),
        "auto_branch": flag(settings.auto_branch),
        "use_worktree": flag(settings.use_worktree),
        "branch": branch,
        "agent_a": settings.agent_a,
        "agent_b": settings.agent_b,
        "agent_a_args": settings.agent_a_args,
        "agent_b_args": settings.agent_b_args,
        "model_a": settings.model_a,
        "model_b": settings.model_b,
        "claude_args": settings.claude_args,
        "codex_args": settings.codex_args,
        "agy_args": settings.agy_args,
        "max_rounds": str(settings.max_rounds),
        "human_gate": flag(settings.human_gate),
        "open_pr": flag(settings.open_pr),
        "tools": settings.tools,
        "gate_cmd": gate_cmd,
        "build_gate_cmd": build_gate_cmd,
        # Informational only; keep the first line so display stays one-line (sh:183).
        "task_arg": task_arg.split("\n", 1)[0],
        "task_source_kind": task_source_kind,
        "task_source_path": task_source_path,
    }


def write_snapshot(state_dir: Path, values: Mapping[str, str]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {"schema": 1, **values}
    _atomic_write(state_dir / SNAPSHOT_FILE, json.dumps(payload, indent=2) + "\n")


def load_snapshot(state_dir: Path) -> dict[str, str]:
    path = state_dir / SNAPSHOT_FILE
    if not path.is_file():
        raise RunStateError(f"Missing resume settings snapshot: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunStateError(
            f"{path}: not valid JSON ({exc}); the state may be truncated. "
            "Refusing to resume."
        ) from None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        schema = payload.get("schema") if isinstance(payload, dict) else payload
        raise RunStateError(
            f"{path}: schema must be 1 (got {schema!r}); refusing to resume."
        )
    snapshot: dict[str, str] = {}
    for key, value in payload.items():
        if key == "schema":
            continue
        if key not in SNAPSHOT_KEYS:
            raise RunStateError(
                f"{path}: unknown key [{key}]; the state may be truncated or "
                "written by a newer version. Refusing to resume."
            )
        if not isinstance(value, str):
            raise RunStateError(
                f"{path}: key [{key}] must be a string; refusing to resume."
            )
        snapshot[key.upper()] = value
    return snapshot


def check_immutable(
    env: Mapping[str, str], snapshot: Mapping[str, str]
) -> None:
    for key in IMMUTABLE_KEYS:
        current = env.get(key, "")
        recorded = snapshot.get(key)
        if recorded is None or not current or current == recorded:
            continue
        raise RunStateError(
            f"!! {key}={current} conflicts with the resumed run's snapshot "
            f"({key}={recorded}).\n"
            "   SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE decide the stage graph "
            "and cannot change across resume.\n"
            f"   Unset {key} to keep the snapshot value, or start a fresh run."
        )


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def list_run_state_ids(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted((path.name for path in root.iterdir()), reverse=True)


def resolve_resume_id(root: Path, resume_run: str) -> str:
    run_id = resume_run
    if run_id == "last":
        run_id = ""
        for candidate in list_run_state_ids(root):
            if (root / candidate / "completed").is_file():
                continue
            run_id = candidate
            break
        if not run_id:
            raise RunStateError(
                f"!! RESUME_RUN=last: no unfinished run found under {root}.\n"
                f"   Known run ids: {' '.join(list_run_state_ids(root))}"
            )
    if not _RUN_ID_RE.match(run_id):
        raise RunStateError(
            f"!! Invalid RESUME_RUN [{run_id}]: only letters, digits, - and _ "
            "are allowed."
        )
    state_dir = root / run_id
    if not state_dir.is_dir():
        raise RunStateError(
            f"!! No run state at {state_dir}.\n"
            f"   Known run ids: {' '.join(list_run_state_ids(root))}\n"
            "   If that run used USE_WORKTREE=1, cd into its worktree first; "
            "the state lives there."
        )
    if (state_dir / "completed").is_file():
        raise RunStateError(f"!! Run {run_id} already completed; nothing to resume.")
    return run_id


@dataclass
class RunState:
    state_dir: Path
    run_id: str
    locked: bool = False

    @classmethod
    def create(cls, root: Path, run_id: str, task_text: str) -> "RunState":
        root.mkdir(parents=True, exist_ok=True)
        state_dir = root / run_id
        try:
            state_dir.mkdir()
        except FileExistsError:
            raise RunStateError(
                f"!! Run state {state_dir} already exists (same-second run id "
                "collision?). Rerun for a fresh id."
            ) from None
        state = cls(state_dir=state_dir, run_id=run_id)
        state.acquire_lock()
        normalized_task = task_text if task_text.endswith("\n") else task_text + "\n"
        _atomic_write(state_dir / "task.txt", normalized_task)
        state._write_ledger([])
        return state

    @classmethod
    def resume(cls, root: Path, resume_run: str) -> "RunState":
        run_id = resolve_resume_id(root, resume_run)
        state = cls(state_dir=root / run_id, run_id=run_id)
        state.acquire_lock()
        return state

    def acquire_lock(self) -> None:
        lock = self.state_dir / "lock"
        try:
            lock.mkdir()
        except FileExistsError:
            raise RunStateError(
                f"!! Run state {self.state_dir} is locked; another attempt may "
                "still be running.\n   If you are sure the previous attempt is "
                f"dead, remove the lock: rm -r {lock}"
            ) from None
        self.locked = True

    def release_lock(self) -> None:
        if not self.locked:
            return
        try:
            (self.state_dir / "lock").rmdir()
        except OSError:
            pass
        self.locked = False

    def task_text(self) -> str:
        path = self.state_dir / "task.txt"
        if not path.is_file():
            raise RunStateError(
                f"!! Missing {path}; the run state is damaged. Start a fresh run."
            )
        return path.read_text(encoding="utf-8")

    def mark_completed(self) -> None:
        (self.state_dir / "completed").touch()

    def is_completed(self) -> bool:
        return (self.state_dir / "completed").is_file()

    def _write_ledger(self, stages: list[str]) -> None:
        payload = {"schema": 1, "stages": stages}
        _atomic_write(self.state_dir / "ledger.json", json.dumps(payload) + "\n")
