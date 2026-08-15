"""Resumable run state under aac/.run/state/<run-id>/.

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
from typing import Callable, Mapping

from .config import Settings
from .phases import Phase, parse_phases

SNAPSHOT_FILE = "settings.json"
SNAPSHOT_KEYS = (
    "spec_dir",
    "dual_spec",
    "import_spec",
    "import_plan",
    "import_review",
    "phases",
    "phase_review",
    "auto_branch",
    "use_worktree",
    "branch",
    "agent_a",
    "agent_b",
    "impl_agent",
    "impl_model",
    "impl_args",
    "agent_a_args",
    "agent_b_args",
    "model_a",
    "model_b",
    "claude_args",
    "codex_args",
    "agy_args",
    "opencode_args",
    "max_rounds",
    "human_gate",
    "human_gate_plan",
    "open_pr",
    "tools",
    "gate_cmd",
    "build_gate_cmd",
    "phase_gate_cmd",
    "task_arg",
    "task_source_kind",
    "task_source_path",
)
IMMUTABLE_KEYS = (
    "SPEC_DIR",
    "DUAL_SPEC",
    "AUTO_BRANCH",
    "USE_WORKTREE",
    "PHASES",
    "IMPORT_SPEC",
    "IMPORT_PLAN",
    "IMPORT_REVIEW",
)


class RunStateError(Exception):
    """A state problem that must stop the run before any AI call."""

    def __init__(self, template: str, **fields: object) -> None:
        from .config import render_template

        self.template = template
        self.fields = fields
        super().__init__(render_template(template, fields))


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
    phase_gate_cmd: str,
    task_arg: str,
    task_source_kind: str,
    task_source_path: str,
) -> dict[str, str]:
    def flag(value: bool) -> str:
        return "1" if value else "0"

    return {
        "spec_dir": settings.spec_dir,
        "dual_spec": flag(settings.dual_spec),
        "import_spec": settings.import_spec,
        "import_plan": settings.import_plan,
        "import_review": flag(settings.import_review),
        "phases": flag(settings.phases),
        "phase_review": flag(settings.phase_review),
        "auto_branch": flag(settings.auto_branch),
        "use_worktree": flag(settings.use_worktree),
        "branch": branch,
        "agent_a": settings.agent_a,
        "agent_b": settings.agent_b,
        "impl_agent": settings.impl_agent,
        "impl_model": settings.impl_model,
        "impl_args": settings.impl_args,
        "agent_a_args": settings.agent_a_args,
        "agent_b_args": settings.agent_b_args,
        "model_a": settings.model_a,
        "model_b": settings.model_b,
        "claude_args": settings.claude_args,
        "codex_args": settings.codex_args,
        "agy_args": settings.agy_args,
        "opencode_args": settings.opencode_args,
        "max_rounds": str(settings.max_rounds),
        "human_gate": flag(settings.human_gate),
        "human_gate_plan": flag(settings.human_gate_plan),
        "open_pr": flag(settings.open_pr),
        "tools": settings.tools,
        "gate_cmd": gate_cmd,
        "build_gate_cmd": build_gate_cmd,
        "phase_gate_cmd": phase_gate_cmd,
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


def enable_snapshot_phases(state_dir: Path) -> None:
    """Record the spec-gate Phased ATDD flip so resume sees PHASES=1.

    The snapshot is the resume source of truth for PHASES. The flip must
    land atomically before the plan stage can run under phased templates;
    losing it would let a resumed attempt silently run the single-shot flow.
    """

    path = state_dir / SNAPSHOT_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        load_snapshot(state_dir)
        payload["phases"] = "1"
        _atomic_write(path, json.dumps(payload, indent=2) + "\n")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, RunStateError) as exc:
        raise RunStateError(
            f"!! {path}: cannot record the Phased ATDD flip ({exc}).\n"
            "   Stopping here so the snapshot and this attempt cannot "
            "disagree about PHASES.\n"
            "   The spec stage is not recorded complete, so a resume runs "
            "it again and offers Phased ATDD again."
        ) from None


def check_immutable(
    env: Mapping[str, str], snapshot: Mapping[str, str]
) -> None:
    _MISSING_KEY_DEFAULTS = {
        # Snapshots written before these features have no such keys; those
        # runs necessarily ran without them, so enabling one on resume would
        # change stage behavior and must be refused.
        "PHASES": "0",
        "IMPORT_SPEC": "",
        "IMPORT_PLAN": "",
        "IMPORT_REVIEW": "1",
    }
    for key in IMMUTABLE_KEYS:
        current = env.get(key, "")
        recorded = snapshot.get(key)
        if recorded is None and key in _MISSING_KEY_DEFAULTS:
            recorded = _MISSING_KEY_DEFAULTS[key]
        if recorded is None or not current or current == recorded:
            continue
        raise RunStateError(
            f"!! {key}={current} conflicts with the resumed run's snapshot "
            f"({key}={recorded}).\n"
            "   SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE/PHASES and the "
            "IMPORT_* variables decide stage behavior and cannot change "
            "across resume.\n"
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

    def _read_ledger(self) -> list[str]:
        path = self.state_dir / "ledger.json"
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RunStateError(
                f"!! {path} is not valid JSON; the state may be damaged. "
                "Start a fresh run."
            ) from None
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise RunStateError(
                f"!! {path}: unknown ledger schema; start a fresh run."
            )
        stages = payload.get("stages", [])
        return [str(stage) for stage in stages]

    def _write_ledger(self, stages: list[str]) -> None:
        payload = {"schema": 1, "stages": stages}
        _atomic_write(self.state_dir / "ledger.json", json.dumps(payload) + "\n")

    def stage_done(self, name: str) -> bool:
        return name in self._read_ledger()

    def completed_stages(self) -> list[str]:
        return self._read_ledger()

    def record_stage(self, name: str, head_sha: str) -> None:
        stages = self._read_ledger()
        stages.append(name)
        self._write_ledger(stages)
        _atomic_write(self.state_dir / "last-head", head_sha + "\n")

    def read_last_head(self) -> str | None:
        path = self.state_dir / "last-head"
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8").strip()

    def record_import_archive(self, kind: str, archive_path: Path) -> None:
        _atomic_write(
            self.state_dir / f"imported-{kind}-archive-path",
            str(archive_path.resolve()) + "\n",
        )

    def import_archive_path(self, kind: str) -> Path | None:
        record = self.state_dir / f"imported-{kind}-archive-path"
        if not record.is_file():
            return None
        archive_path = Path(record.read_text(encoding="utf-8").strip())
        return archive_path if archive_path.is_file() else None


def restore_or_record_base(
    state: RunState | None, name: str, head_sha: Callable[[], str]
) -> str:
    if state is None:
        return head_sha()
    path = state.state_dir / name
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    base = head_sha()
    _atomic_write(path, base + "\n")
    return base


def restore_or_record_acceptance_base(
    state: RunState | None, head_sha: Callable[[], str]
) -> str:
    # Without persistence, an interrupt between the acceptance commit and the
    # protected-list write would recompute an empty diff on resume and silently
    # disable test protection (C4, sh:832-849).
    return restore_or_record_base(state, "acceptance-test-base", head_sha)


def checkpoint_done(state: RunState, name: str) -> bool:
    return (state.state_dir / name).is_file()


def record_checkpoint(state: RunState, name: str) -> None:
    # Durable sub-stage marker: survives resume like the task queues.
    _atomic_write(state.state_dir / name, "done\n")


PHASES_FILE = "phases.json"


def _validated_phase(path: Path, entry: object, expected_number: int) -> Phase:
    def bad(reason: str) -> RunStateError:
        return RunStateError(
            f"!! {path}: {reason}; the state may be damaged. Start a fresh run."
        )

    if not isinstance(entry, dict):
        raise bad("phase entry is not an object")
    number = entry.get("number")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number != expected_number
    ):
        raise bad(f"phase number must be {expected_number}")
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise bad(f"phase {expected_number} title must be a non-empty string")
    guard = entry.get("regression_guard")
    if not isinstance(guard, bool):
        raise bad(f"phase {expected_number} regression_guard must be a boolean")
    tasks = entry.get("tasks")
    if (
        not isinstance(tasks, list)
        or not tasks
        or not all(isinstance(task, str) and task.strip() for task in tasks)
    ):
        raise bad(
            f"phase {expected_number} tasks must be a non-empty list of "
            "non-empty strings"
        )
    return Phase(
        number=number, title=title, regression_guard=guard, tasks=tuple(tasks)
    )


def save_phases(state: RunState, phases) -> None:
    payload = {
        "schema": 1,
        "phases": [
            {
                "number": phase.number,
                "title": phase.title,
                "regression_guard": phase.regression_guard,
                "tasks": list(phase.tasks),
            }
            for phase in phases
        ],
    }
    _atomic_write(
        state.state_dir / PHASES_FILE, json.dumps(payload, indent=2) + "\n"
    )


def load_phases(state: RunState) -> tuple[Phase, ...] | None:
    path = state.state_dir / PHASES_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise RunStateError(
            f"!! {path} is not valid JSON; the state may be damaged. "
            "Start a fresh run."
        ) from None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RunStateError(f"!! {path}: unknown phases schema; start a fresh run.")
    entries = payload.get("phases")
    if not isinstance(entries, list) or not entries:
        raise RunStateError(
            f"!! {path}: phases must be a non-empty list; the state may be "
            "damaged. Start a fresh run."
        )
    return tuple(
        _validated_phase(path, entry, index + 1)
        for index, entry in enumerate(entries)
    )


def ensure_phases(state: RunState, plan_path: Path) -> tuple[Phase, ...]:
    # The persisted structure is control flow; plan.md is UI after this point.
    saved = load_phases(state)
    if saved is not None:
        return saved
    phases = parse_phases(plan_path)
    save_phases(state, phases)
    return phases


def plan_tasks(plan_path: Path) -> list[str]:
    if not plan_path.is_file():
        return []
    tasks = []
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- [ ] "):
            tasks.append(line[len("- [ ] ") :])
    return tasks


def _queue_path(state: RunState, name: str = "tasks-remaining") -> Path:
    return state.state_dir / name


def phase_queue_name(number: int) -> str:
    return f"tasks-remaining-phase-{number:02d}"


def ensure_named_task_queue(state: RunState, name: str, tasks: list[str]) -> None:
    queue = _queue_path(state, name)
    if queue.is_file():
        return
    _atomic_write(queue, "".join(task + "\n" for task in tasks))


def ensure_task_queue(
    state: RunState,
    plan_path: Path,
    echo_err: Callable[..., None] | None = None,
) -> None:
    # The script-held queue is control flow; plan.md checkboxes are UI only.
    # An existing EMPTY queue means every task already committed (C2).
    queue = _queue_path(state)
    if queue.is_file():
        return
    tasks = plan_tasks(plan_path)
    if not tasks:
        from .i18n import emit

        def _fallback_err(template: str, **fields: object) -> None:
            import sys

            from .config import render_template

            print(render_template(template, fields), file=sys.stderr)

        emit(
            echo_err or _fallback_err,
            '(warning: plan.md has no "- [ ] " task list; falling back to '
            "one whole-plan implementation task)",
        )
        tasks = [f"Complete the full implementation described in {plan_path}"]
    _atomic_write(queue, "".join(task + "\n" for task in tasks))


def remaining_tasks(state: RunState, name: str = "tasks-remaining") -> list[str]:
    queue = _queue_path(state, name)
    if not queue.is_file():
        return []
    return [line for line in queue.read_text(encoding="utf-8").splitlines() if line]


def pop_task_queue(state: RunState, name: str = "tasks-remaining") -> None:
    tasks = remaining_tasks(state, name)
    _atomic_write(_queue_path(state, name), "".join(task + "\n" for task in tasks[1:]))


def mark_plan_task_done(plan_path: Path, task: str) -> None:
    if not plan_path.is_file():
        return
    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = f"- [ ] {task}"
    for index, line in enumerate(lines):
        if line.rstrip("\n") == target:
            lines[index] = line.replace("- [ ] ", "- [x] ", 1)
            break
    _atomic_write(plan_path, "".join(lines))


def init_live_state(wf: Path, *, resume: bool) -> None:
    # Self-healing transients are always cleared; a resume must keep durable
    # files later stages depend on, otherwise resuming deletes its own inputs
    # (C3, sh:667-687).
    wf.mkdir(parents=True, exist_ok=True)
    files = ["review.md", "verdict.json", "last-agent-output.txt", "pr-body.md"]
    if not resume:
        files += [
            "suggestions.md",
            "protected-tests.txt",
            "protected-base.sha",
            "spec-merge-request.md",
        ]
    for name in files:
        (wf / name).unlink(missing_ok=True)
