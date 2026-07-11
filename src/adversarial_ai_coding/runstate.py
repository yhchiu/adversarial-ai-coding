"""Resumable run state under .workflow/state/<run-id>/.

Port of adversarial-ai-coding.sh:66-330 (state block) plus the cross-stage
restore helpers. Format change approved by the spec: the settings snapshot
is settings.json and the stage ledger is ledger.json; both refuse unknown
schemas. The snapshot is parsed as data only, never executed (sh:67-69).
"""

from __future__ import annotations

import json
import os
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
