# Python Port — Plan 3 of 6: Run State and Archive I/O Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the resumable run state (settings snapshot, run lock, stage ledger, cross-stage restores) and the run archive I/O (artifact sequencing, meta files, git snapshots, metrics, log sections).

**Architecture:** Plan 3 of the series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. Two format
changes approved by the spec: the settings snapshot moves from
`resume.conf` key=value to `settings.json`, and the stage ledger moves to
`ledger.json`. All state writes stay atomic (temp file + `os.replace`).
`RunState` owns `.workflow/state/<run-id>/`; `RunArchive` owns
`.workflow/runs/<run-id>/`. Neither imports engines or workflow — they are
leaf modules the orchestration (plan 5) composes.

**Tech Stack:** Python 3.12+, stdlib only (`json`, `os`, `re`, `subprocess` for git snapshots), pytest with temp git repos.

## Global Constraints

- Runtime dependencies: none (stdlib only); pytest is dev-group only.
- The bash files are FROZEN: never edit `adversarial-ai-coding.sh` or `tests/*.sh`.
- Behavior parity with the cited bash lines, except the two approved format
  changes (JSON snapshot, JSON ledger) and divergences this plan documents.
  Bash's silent-failure semantics matter; error paths that bash fails
  closed on (refusing to resume) must raise `RunStateError` with messages
  preserving the bash wording users may grep for.
- All state and archive writes that replace a file go through temp file +
  `os.replace` in the same directory.
- Artifact directory layout and names are IDENTICAL to bash (spec
  constraint): `NNN-name` sequence, `.meta.json` sidecars, `logs/001-run.log`.
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By.
- `uv run pytest -q` green after every task. Tests that need git create
  throwaway repos via the `new_repo` fixture defined in Task 1 and never
  touch this repository.
- Machine note: clear `PYTHONHOME`/`PYTHONPATH` if `uv run` misbehaves.

## File Structure

```
src/adversarial_ai_coding/runstate.py   # Tasks 1-3
src/adversarial_ai_coding/archive.py    # Tasks 4-5 (extends plan 1's pure helpers)
tests/conftest.py                       # Task 1 (new_repo fixture)
tests/test_runstate_snapshot.py         # Task 1
tests/test_runstate_resume.py           # Task 2
tests/test_runstate_crossstage.py       # Task 3
tests/test_archive_io.py                # Task 4
tests/test_archive_git.py               # Task 5
```

## Bash-Function Mapping (this plan's parity ledger)

| bash | Python |
|---|---|
| `parse_resume_conf` :135 | `runstate.load_snapshot` (settings.json) |
| `write_resume_conf` :178 | `runstate.write_snapshot` + `runstate.snapshot_values` |
| `resume_check_immutable` :222 | `runstate.check_immutable` |
| `list_run_state_ids` :81 | `runstate.list_run_state_ids` |
| `resume_load` :231 | `runstate.resolve_resume_id` + `RunState.resume` |
| `init_run_state` :272 | `RunState.create` |
| `acquire_run_lock` :125 / `release_run_lock` :86 | `RunState.acquire_lock` / `RunState.release_lock` |
| `stage_done` :1386 / `end_stage` ledger+head write :1413 | `RunState.stage_done` / `RunState.record_stage` (skip/echo logic stays in plan 5's `begin_stage`) |
| `restore_or_record_acceptance_base` :832 | `runstate.restore_or_record_acceptance_base` |
| `plan_tasks` :794, `ensure_task_queue` :799, `pop_task_queue` :813, `mark_plan_task_done` :820 | same names in `runstate` |
| `init_live_state` :667 | `runstate.init_live_state` |
| `"completed" marker` :2001 | `RunState.mark_completed` / `RunState.is_completed` |
| `art_path` :454 | `RunArchive.art_path` |
| `write_meta` :470 | `RunArchive.write_meta` |
| `archive_snapshot` :486 / `archive_text` :497 | `RunArchive.archive_snapshot` / `archive_text` |
| `archive_task` :537 | `RunArchive.archive_task` |
| `archive_git_state` :560 | `RunArchive.archive_git_state` |
| `archive_engine_attempt` :1118 | `RunArchive.archive_engine_attempt` |
| `establish_run_archive` :593 | `archive.establish_run_archive` |
| `write_run_metadata` :608 / `write_log_metadata` :634 | `RunArchive.write_run_metadata` / `write_log_metadata` |
| `log_section` :652 | `RunArchive.log_section` |
| `metric` :366 | `RunArchive.metric` |
| `abs_path` :585 | `Path.resolve()` at call sites (no port needed) |

Deliberate divergences (document in code, pin with tests):
- Snapshot format: `settings.json` (schema field, same keys as the bash
  allowlist, unknown keys still refused). Values MAY contain newlines —
  bash refused them only because of its line format; a test pins the
  round-trip.
- Ledger format: `ledger.json` (`{"schema": 1, "stages": [...]}`).
- `write_meta`/`write_run_metadata` use `json.dumps` instead of jq; key
  order follows the bash jq object literally so archived files diff
  cleanly against bash-era runs.

---

### Task 1: Snapshot load/write and immutable checks (`runstate.py`)

Bash reference: `adversarial-ai-coding.sh:135-229` and the snapshot key
list at `:184-207`.
Bash tests ported: `tests/helpers.test.sh:696-731`.

**Files:**
- Create: `src/adversarial_ai_coding/runstate.py`
- Create: `tests/conftest.py`
- Test: `tests/test_runstate_snapshot.py`

**Interfaces:**
- Consumes: `config.Settings` (read-only, for `snapshot_values`).
- Produces:
  - `runstate.RunStateError(Exception)` — every refuse-to-resume path.
  - `runstate.SNAPSHOT_KEYS: tuple[str, ...]` — exactly the bash conf keys:
    `("spec_dir", "dual_spec", "auto_branch", "use_worktree", "branch",
    "engine_a", "engine_b", "engine_a_args", "engine_b_args", "model_a",
    "model_b", "claude_args", "codex_args", "agy_args", "max_rounds",
    "human_gate", "open_pr", "tools", "gate_cmd", "build_gate_cmd",
    "task_arg", "task_source_kind", "task_source_path")`
  - `runstate.load_snapshot(state_dir: Path) -> dict[str, str]` — reads
    `settings.json`; returns an UPPERCASE-keyed mapping (`{"SPEC_DIR": ...}`)
    ready for `Settings.from_env(..., snapshot=...)`. Raises
    `RunStateError` on: missing file, invalid JSON, `schema != 1`, unknown
    key, non-string value.
  - `runstate.write_snapshot(state_dir: Path, values: Mapping[str, str]) -> None`
    — atomic write of `{"schema": 1, **values}` (lowercase keys).
  - `runstate.snapshot_values(settings: Settings, *, branch: str, gate_cmd: str, build_gate_cmd: str, task_arg: str, task_source_kind: str, task_source_path: str) -> dict[str, str]`
    — composes the lowercase dict from a `Settings` (bools back to "1"/"0",
    ints to str); `task_arg` keeps only its first line (bash :183, kept for
    display parity even though JSON could hold more).
  - `runstate.check_immutable(env: Mapping[str, str], snapshot: Mapping[str, str]) -> None`
    — for SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE: an env value that is
    set, non-empty, and different from the snapshot raises `RunStateError`
    with the bash wording (":222-229").

- [ ] **Step 1: Write the shared git fixture**

`tests/conftest.py`:

```python
"""Shared fixtures. new_repo ports the bash suite's temp-repo helper."""

import subprocess

import pytest


@pytest.fixture
def new_repo(tmp_path):
    """A throwaway git repo with one commit, like helpers.test.sh new_repo."""
    def _git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True, text=True)

    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)],
                   check=True, capture_output=True)
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "test")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    return tmp_path
```

- [ ] **Step 2: Write the failing tests**

`tests/test_runstate_snapshot.py`:

```python
"""Ports tests/helpers.test.sh:696-731 (conf parser/writer) onto settings.json."""

import json

import pytest

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runstate import (
    RunStateError,
    check_immutable,
    load_snapshot,
    snapshot_values,
    write_snapshot,
)


def make_settings(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def write_raw(state_dir, payload):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "settings.json").write_text(payload, encoding="utf-8")


def test_write_parse_roundtrip_keeps_spaces_and_quotes(tmp_path):
    # helpers.test.sh: "resume conf:write/parse roundtrip keeps spaces and quotes"
    s = make_settings({"SPEC_DIR": "specs/x y",
                       "CODEX_ARGS": '-c model="x,y" --flag "quoted value"'})
    values = snapshot_values(
        s, branch="main", gate_cmd="go test ./...", build_gate_cmd="",
        task_arg="task.md", task_source_kind="file",
        task_source_path="/tmp/task dir/task.md",
    )
    write_snapshot(tmp_path / "st", values)
    snap = load_snapshot(tmp_path / "st")
    assert snap["SPEC_DIR"] == "specs/x y"
    assert snap["CODEX_ARGS"] == '-c model="x,y" --flag "quoted value"'
    assert snap["GATE_CMD"] == "go test ./..."
    assert snap["TASK_SOURCE_PATH"] == "/tmp/task dir/task.md"


def test_newline_values_round_trip(tmp_path):
    # Divergence from bash (which refused newlines due to its line format):
    # JSON holds them safely; task_arg still keeps only its first line.
    s = make_settings({"TOOLS": "Bash(git *)"})
    values = snapshot_values(s, branch="b", gate_cmd="a\nb", build_gate_cmd="",
                             task_arg="line1\nline2", task_source_kind="literal",
                             task_source_path="")
    assert values["task_arg"] == "line1"
    write_snapshot(tmp_path / "st", values)
    assert load_snapshot(tmp_path / "st")["GATE_CMD"] == "a\nb"


def test_unknown_key_is_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"schema": 1, "evil_key": "x"}))
    with pytest.raises(RunStateError, match="unknown key"):
        load_snapshot(tmp_path / "st")


def test_missing_schema_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"spec_dir": "specs/x"}))
    with pytest.raises(RunStateError, match="schema"):
        load_snapshot(tmp_path / "st")


def test_wrong_schema_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"schema": 2, "spec_dir": "x"}))
    with pytest.raises(RunStateError, match="schema"):
        load_snapshot(tmp_path / "st")


def test_empty_or_invalid_json_rejected(tmp_path):
    write_raw(tmp_path / "st", "")
    with pytest.raises(RunStateError):
        load_snapshot(tmp_path / "st")
    write_raw(tmp_path / "st", "truncated line without equals")
    with pytest.raises(RunStateError):
        load_snapshot(tmp_path / "st")


def test_missing_file_rejected(tmp_path):
    with pytest.raises(RunStateError, match="Missing resume settings snapshot"):
        load_snapshot(tmp_path / "st")


def test_snapshot_feeds_settings_from_env():
    s = make_settings({"AGENT_A": "agy", "MAX_ROUNDS": "5"})
    values = snapshot_values(s, branch="b", gate_cmd="", build_gate_cmd="",
                             task_arg="", task_source_kind="literal",
                             task_source_path="")
    assert values["engine_a"] == "agy"
    assert values["max_rounds"] == "5"
    assert values["auto_branch"] == "1"
    assert values["use_worktree"] == "0"


def test_check_immutable_conflict():
    # helpers.test.sh: "resume load:immutable field conflict is rejected"
    snap = {"DUAL_SPEC": "0", "SPEC_DIR": "specs/r"}
    with pytest.raises(RunStateError, match="DUAL_SPEC=1 conflicts"):
        check_immutable({"DUAL_SPEC": "1"}, snap)
    check_immutable({"DUAL_SPEC": "0"}, snap)   # same value: fine
    check_immutable({"DUAL_SPEC": ""}, snap)    # empty: fine
    check_immutable({}, snap)                   # unset: fine
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_runstate_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.runstate'`

- [ ] **Step 4: Write `src/adversarial_ai_coding/runstate.py` (snapshot part)**

```python
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
    "spec_dir", "dual_spec", "auto_branch", "use_worktree", "branch",
    "engine_a", "engine_b", "engine_a_args", "engine_b_args", "model_a",
    "model_b", "claude_args", "codex_args", "agy_args", "max_rounds",
    "human_gate", "open_pr", "tools", "gate_cmd", "build_gate_cmd",
    "task_arg", "task_source_kind", "task_source_path",
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
        "engine_a": settings.engine_a,
        "engine_b": settings.engine_b,
        "engine_a_args": settings.engine_a_args,
        "engine_b_args": settings.engine_b_args,
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
            f"{path}: not valid JSON ({exc}); the state may be truncated. Refusing to resume."
        ) from None
    if not isinstance(payload, dict) or payload.get("schema") != 1:
        raise RunStateError(
            f"{path}: schema must be 1 (got {payload.get('schema') if isinstance(payload, dict) else payload!r}); refusing to resume."
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
            raise RunStateError(f"{path}: key [{key}] must be a string; refusing to resume.")
        snapshot[key.upper()] = value
    return snapshot


def check_immutable(env: Mapping[str, str], snapshot: Mapping[str, str]) -> None:
    for key in IMMUTABLE_KEYS:
        current = env.get(key, "")
        recorded = snapshot.get(key)
        if recorded is None or not current or current == recorded:
            continue
        raise RunStateError(
            f"!! {key}={current} conflicts with the resumed run's snapshot ({key}={recorded}).\n"
            "   SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE decide the stage graph "
            "and cannot change across resume.\n"
            f"   Unset {key} to keep the snapshot value, or start a fresh run."
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_runstate_snapshot.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/conftest.py tests/test_runstate_snapshot.py
git commit -m "feat: port resume snapshot to settings.json

Replace the bash resume.conf key=value format with settings.json per
the approved spec. The key allowlist, schema gate, unknown-key refusal,
and immutable-field checks carry over with their bash error wording;
values may now contain newlines, which the line-based format had to
refuse. Writes are atomic via temp file + os.replace."
```

---

### Task 2: Run resolution, locking, create/resume lifecycle

Bash reference: `adversarial-ai-coding.sh:81-133` (ids, lock),
`:231-283` (resume_load, init_run_state), `:2001-2003` (completed marker).
Bash tests ported: `tests/helpers.test.sh:733-790`.

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py` (append)
- Test: `tests/test_runstate_resume.py`

**Interfaces:**
- Produces:
  - `runstate.list_run_state_ids(root: Path) -> list[str]` — newest first
    (reverse lexical sort, matching `ls -1 | sort -r`).
  - `runstate.resolve_resume_id(root: Path, resume_run: str) -> str` —
    handles `last` (newest without a `completed` marker), validates
    `^[A-Za-z0-9_-]+$`, checks existence and completion. Raises
    `RunStateError` with messages containing the known-id list, the
    "already completed" wording, and the worktree hint from bash.
  - `@dataclass runstate.RunState:`
    fields `state_dir: Path`, `run_id: str`, `locked: bool = False`.
    Methods:
    - `RunState.create(root: Path, run_id: str, task_text: str) -> RunState`
      — atomic `mkdir` claim (collision raises with the bash wording),
      acquires the lock, writes `task.txt`, creates an empty ledger.
    - `RunState.resume(root: Path, resume_run: str) -> RunState` — resolve,
      then acquire the lock (`state_dir/lock` mkdir; busy raises with the
      "rm -r ... lock" hint).
    - `acquire_lock()` / `release_lock()` — mkdir/rmdir primitives; release
      is idempotent and never raises.
    - `task_text() -> str` — reads `task.txt`; raises `RunStateError`
      ("the run state is damaged") when missing.
    - `mark_completed()` / `is_completed() -> bool` — the `completed` marker.
- Note: bash installs EXIT/INT/TERM/HUP traps inside `acquire_run_lock`;
  in Python the equivalent (atexit/except paths printing the resume hint
  and releasing the lock) is `cli.py`'s job — plan 5. `release_lock` here
  must therefore be safe to call multiple times.

- [ ] **Step 1: Write the failing tests**

`tests/test_runstate_resume.py`:

```python
"""Ports tests/helpers.test.sh:733-790 (RESUME_RUN resolution and locking)."""

import pytest

from adversarial_ai_coding.runstate import (
    RunState,
    RunStateError,
    list_run_state_ids,
    resolve_resume_id,
    write_snapshot,
)


def make_state(root, run_id, completed=False):
    d = root / run_id
    d.mkdir(parents=True)
    write_snapshot(d, {})
    if completed:
        (d / "completed").touch()
    return d


def test_path_traversal_id_is_rejected(tmp_path):
    with pytest.raises(RunStateError, match="Invalid RESUME_RUN"):
        resolve_resume_id(tmp_path, "../../x")


def test_unknown_id_fails_and_lists_available_runs(tmp_path):
    make_state(tmp_path, "aaa-run")
    with pytest.raises(RunStateError, match="aaa-run"):
        resolve_resume_id(tmp_path, "nope")


def test_completed_run_is_refused(tmp_path):
    make_state(tmp_path, "aaa-run", completed=True)
    with pytest.raises(RunStateError, match="already completed"):
        resolve_resume_id(tmp_path, "aaa-run")


def test_last_picks_newest_unfinished(tmp_path):
    make_state(tmp_path, "20260101-000000")
    make_state(tmp_path, "20260102-000000", completed=True)
    assert resolve_resume_id(tmp_path, "last") == "20260101-000000"


def test_last_with_everything_completed_fails(tmp_path):
    make_state(tmp_path, "20260101-000000", completed=True)
    with pytest.raises(RunStateError, match="no unfinished run"):
        resolve_resume_id(tmp_path, "last")


def test_list_ids_newest_first(tmp_path):
    make_state(tmp_path, "20260101-000000")
    make_state(tmp_path, "20260103-000000")
    make_state(tmp_path, "20260102-000000")
    assert list_run_state_ids(tmp_path) == [
        "20260103-000000", "20260102-000000", "20260101-000000",
    ]


def test_busy_lock_is_refused_with_removal_hint(tmp_path):
    d = make_state(tmp_path, "r4")
    (d / "lock").mkdir()
    with pytest.raises(RunStateError, match=r"rm -r .*lock"):
        RunState.resume(tmp_path, "r4")


def test_resume_acquires_and_releases_lock(tmp_path):
    make_state(tmp_path, "r1")
    state = RunState.resume(tmp_path, "r1")
    assert (state.state_dir / "lock").is_dir()
    state.release_lock()
    assert not (state.state_dir / "lock").exists()
    state.release_lock()  # idempotent


def test_create_claims_fresh_state(tmp_path):
    state = RunState.create(tmp_path, "run-1", "the task\n")
    assert state.task_text() == "the task\n"
    assert not state.is_completed()
    state.mark_completed()
    assert state.is_completed()


def test_create_same_second_collision_fails_clearly(tmp_path):
    RunState.create(tmp_path, "xdup", "t").release_lock()
    with pytest.raises(RunStateError, match="already exists"):
        RunState.create(tmp_path, "xdup", "t")


def test_missing_task_snapshot_is_damaged_state(tmp_path):
    make_state(tmp_path, "r1")
    state = RunState.resume(tmp_path, "r1")
    with pytest.raises(RunStateError, match="damaged"):
        state.task_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runstate_resume.py -q`
Expected: FAIL — ImportError on the new names.

- [ ] **Step 3: Append to `src/adversarial_ai_coding/runstate.py`**

```python
import re
from dataclasses import dataclass

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def list_run_state_ids(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted((p.name for p in root.iterdir()), reverse=True)


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
            f"!! Invalid RESUME_RUN [{run_id}]: only letters, digits, - and _ are allowed."
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
        _atomic_write(state_dir / "task.txt", task_text if task_text.endswith("\n") else task_text + "\n")
        state._write_ledger([])
        return state

    @classmethod
    def resume(cls, root: Path, resume_run: str) -> "RunState":
        run_id = resolve_resume_id(root, resume_run)
        state = cls(state_dir=root / run_id, run_id=run_id)
        state.acquire_lock()
        return state

    # -- lock ---------------------------------------------------------------
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

    # -- task / completion ----------------------------------------------------
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
```

(The `_write_ledger` helper arrives in Task 3's code below; while
implementing Task 2 first, stub it as `def _write_ledger(self, stages):
_atomic_write(self.state_dir / "ledger.json", json.dumps({"schema": 1,
"stages": stages}) + "\n")` — Task 3 keeps exactly that shape.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runstate_resume.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_runstate_resume.py
git commit -m "feat: port run resolution, locking, and state lifecycle

Port RESUME_RUN resolution (id validation, last-unfinished selection,
completed refusal with known-id listings), the mkdir run lock with its
manual-removal hint, fresh-state claiming with same-second collision
detection, the task snapshot reader that fails closed on damaged state,
and the completed marker."
```

---

### Task 3: Stage ledger, last-head, acceptance base, task queue, live state

Bash reference: `adversarial-ai-coding.sh:667-687` (init_live_state),
`:794-849` (plan_tasks, queue, mark_plan_task_done, acceptance base),
`:1386-1418` (stage_done, end_stage's ledger/head writes).
Bash tests ported: `tests/helpers.test.sh:792-806` (ledger primitives),
`:846-856` (init_live_state), `:942-1001` (acceptance base, queue,
checkbox).

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py` (append)
- Test: `tests/test_runstate_crossstage.py`

**Interfaces:**
- Produces (RunState methods unless noted):
  - `stage_done(name: str) -> bool`, `record_stage(name: str, head_sha: str) -> None`
    (appends to `ledger.json`, atomically rewrites `last-head`),
    `completed_stages() -> list[str]`, `read_last_head() -> str | None`.
    The skip/echo/artifact-check logic of `begin_stage` stays in plan 5.
  - `restore_or_record_acceptance_base(state: RunState | None, head_sha: Callable[[], str]) -> str`
    — module function; with no claimed state just returns `head_sha()`
    (bash :846-848).
  - `plan_tasks(plan_path: Path) -> list[str]` — module function, pure.
  - `ensure_task_queue(state: RunState, plan_path: Path) -> None`,
    `remaining_tasks(state: RunState) -> list[str]`,
    `pop_task_queue(state: RunState) -> None` — the queue file keeps the
    bash name `tasks-remaining`, plain text lines (it is control flow, not
    a snapshot; format unchanged).
  - `mark_plan_task_done(plan_path: Path, task: str) -> None` — module
    function; flips the exact `- [ ] <task>` line once.
  - `init_live_state(wf: Path, resume: bool) -> None` — module function;
    clears transients, keeps durable files on resume (C3 list, bash
    :672-686).

- [ ] **Step 1: Write the failing tests**

`tests/test_runstate_crossstage.py`:

```python
"""Ports helpers.test.sh:792-806, 846-856, 942-1001 (cross-stage state)."""

from adversarial_ai_coding.runstate import (
    RunState,
    ensure_task_queue,
    init_live_state,
    mark_plan_task_done,
    plan_tasks,
    pop_task_queue,
    remaining_tasks,
    restore_or_record_acceptance_base,
)


def claimed(tmp_path):
    return RunState.create(tmp_path / "state", "run", "task\n")


def test_record_stage_and_head_checkpoint(tmp_path):
    st = claimed(tmp_path)
    assert not st.stage_done("stage-one")
    st.record_stage("stage-one", "abc123")
    assert st.stage_done("stage-one")
    assert not st.stage_done("stage-two")
    assert st.read_last_head() == "abc123"
    st.record_stage("stage-two", "def456")
    assert st.completed_stages() == ["stage-one", "stage-two"]
    assert st.read_last_head() == "def456"


def test_acceptance_base_persisted_value_is_reused(tmp_path):
    st = claimed(tmp_path)
    (st.state_dir / "acceptance-test-base").write_text("cafebabe\n", encoding="utf-8")
    assert restore_or_record_acceptance_base(st, lambda: "NEW") == "cafebabe"


def test_acceptance_base_first_entry_records_and_persists(tmp_path):
    st = claimed(tmp_path)
    assert restore_or_record_acceptance_base(st, lambda: "headsha") == "headsha"
    raw = (st.state_dir / "acceptance-test-base").read_text(encoding="utf-8")
    assert raw.strip() == "headsha"


def test_acceptance_base_without_state_uses_head(tmp_path):
    assert restore_or_record_acceptance_base(None, lambda: "live-head") == "live-head"


def test_plan_tasks_only_unfinished(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("# plan\n\n- [ ] task one\n- [x] finished task\n- [ ] task two\nplain text\n",
                    encoding="utf-8")
    assert plan_tasks(plan) == ["task one", "task two"]
    assert plan_tasks(tmp_path / "missing.md") == []


def test_task_queue_created_from_plan(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("- [ ] task one\n- [x] done task\n- [ ] task two\n", encoding="utf-8")
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == ["task one", "task two"]


def test_task_queue_existing_not_rebuilt_and_pop(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("- [ ] task one\n", encoding="utf-8")
    (st.state_dir / "tasks-remaining").write_text("custom remaining task\n", encoding="utf-8")
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == ["custom remaining task"]
    pop_task_queue(st)
    assert remaining_tasks(st) == []
    # An existing EMPTY queue means all tasks committed: no fallback rebuild.
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == []


def test_task_queue_fallback_without_checkboxes(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan2.md"
    plan.write_text("prose only, no checkbox list\n", encoding="utf-8")
    ensure_task_queue(st, plan)
    tasks = remaining_tasks(st)
    assert len(tasks) == 1
    assert "Complete the full implementation" in tasks[0]
    assert "plan2.md" in tasks[0]


def test_mark_plan_task_done_exact_line_once(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text("- [ ] task one\n- [ ] task one extra\nplain line\n", encoding="utf-8")
    mark_plan_task_done(plan, "task one")
    mark_plan_task_done(plan, "task one")  # idempotent
    assert plan.read_text(encoding="utf-8") == (
        "- [x] task one\n- [ ] task one extra\nplain line\n"
    )


def test_init_live_state_resume_keeps_durables_clears_transients(tmp_path):
    wf = tmp_path / ".workflow"
    wf.mkdir()
    names = ["suggestions.md", "protected-tests.txt", "protected-base.sha",
             "spec-merge-request.md", "review.md", "verdict.json",
             "last-engine-output.txt", "pr-body.md"]
    for name in names:
        (wf / name).write_text("x\n", encoding="utf-8")
    init_live_state(wf, resume=True)
    for durable in ["suggestions.md", "protected-tests.txt", "protected-base.sha",
                    "spec-merge-request.md"]:
        assert (wf / durable).is_file()
    for transient in ["review.md", "verdict.json", "last-engine-output.txt", "pr-body.md"]:
        assert not (wf / transient).exists()


def test_init_live_state_fresh_clears_everything(tmp_path):
    wf = tmp_path / ".workflow"
    wf.mkdir()
    for name in ["suggestions.md", "review.md"]:
        (wf / name).write_text("x\n", encoding="utf-8")
    init_live_state(wf, resume=False)
    assert not (wf / "suggestions.md").exists()
    assert not (wf / "review.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runstate_crossstage.py -q`
Expected: FAIL — ImportError on the new names.

- [ ] **Step 3: Append to `src/adversarial_ai_coding/runstate.py`**

Add the methods to `RunState` and the module functions:

```python
    # -- stage ledger (ledger.json) -------------------------------------------
    def _read_ledger(self) -> list[str]:
        path = self.state_dir / "ledger.json"
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise RunStateError(
                f"!! {path} is not valid JSON; the state may be damaged. Start a fresh run."
            ) from None
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            raise RunStateError(f"!! {path}: unknown ledger schema; start a fresh run.")
        stages = payload.get("stages", [])
        return [str(s) for s in stages]

    def _write_ledger(self, stages: list[str]) -> None:
        _atomic_write(self.state_dir / "ledger.json",
                      json.dumps({"schema": 1, "stages": stages}) + "\n")

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


def restore_or_record_acceptance_base(state: "RunState | None",
                                      head_sha: "Callable[[], str]") -> str:
    # C4: without persistence, an interrupt between the acceptance commit and
    # the protected-list write would recompute an empty diff on resume and
    # silently disable test protection (sh:832-849).
    if state is None:
        return head_sha()
    path = state.state_dir / "acceptance-test-base"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    base = head_sha()
    _atomic_write(path, base + "\n")
    return base


def plan_tasks(plan_path: Path) -> list[str]:
    if not plan_path.is_file():
        return []
    tasks = []
    for line in plan_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- [ ] "):
            tasks.append(line[len("- [ ] "):])
    return tasks


def _queue_path(state: "RunState") -> Path:
    return state.state_dir / "tasks-remaining"


def ensure_task_queue(state: "RunState", plan_path: Path) -> None:
    # The script-held queue is the control flow; plan.md checkboxes are UI
    # only. An existing EMPTY queue means every task already committed (C2).
    queue = _queue_path(state)
    if queue.is_file():
        return
    tasks = plan_tasks(plan_path)
    if not tasks:
        import sys
        print('(warning: plan.md has no "- [ ] " task list; falling back to '
              "one whole-plan implementation task)", file=sys.stderr)
        tasks = [f"Complete the full implementation described in {plan_path}"]
    _atomic_write(queue, "".join(t + "\n" for t in tasks))


def remaining_tasks(state: "RunState") -> list[str]:
    queue = _queue_path(state)
    if not queue.is_file():
        return []
    return [l for l in queue.read_text(encoding="utf-8").splitlines() if l]


def pop_task_queue(state: "RunState") -> None:
    tasks = remaining_tasks(state)
    _atomic_write(_queue_path(state), "".join(t + "\n" for t in tasks[1:]))


def mark_plan_task_done(plan_path: Path, task: str) -> None:
    if not plan_path.is_file():
        return
    lines = plan_path.read_text(encoding="utf-8").splitlines(keepends=True)
    target = f"- [ ] {task}"
    for i, line in enumerate(lines):
        if line.rstrip("\n") == target:
            lines[i] = line.replace("- [ ] ", "- [x] ", 1)
            break
    _atomic_write(plan_path, "".join(lines))


def init_live_state(wf: Path, *, resume: bool) -> None:
    # Self-healing transients are always cleared; a resume must keep the
    # durable files later stages depend on, otherwise resuming deletes its
    # own inputs (C3, sh:667-687).
    wf.mkdir(parents=True, exist_ok=True)
    files = ["review.md", "verdict.json", "last-engine-output.txt", "pr-body.md"]
    if not resume:
        files += ["suggestions.md", "protected-tests.txt", "protected-base.sha",
                  "spec-merge-request.md"]
    for name in files:
        (wf / name).unlink(missing_ok=True)
```

Add `Callable` to the `typing` import at the top of the module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runstate_crossstage.py -q` then `uv run pytest -q`
Expected: all PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_runstate_crossstage.py
git commit -m "feat: port stage ledger and cross-stage state restores

Port the stage ledger (now ledger.json) with last-head checkpoints, the
persisted acceptance-test base (C4), the write-code task queue with its
no-rebuild-when-empty rule and whole-plan fallback (C2), exact-line
plan checkbox flipping, and init_live_state's resume-aware split of
transient versus durable .workflow files (C3)."
```

---

### Task 4: Run archive I/O (`archive.py`): artifacts, meta, log, metrics

Bash reference: `adversarial-ai-coding.sh:454-535` (art_path, write_meta,
archive_snapshot/text), `:537-558` (archive_task), `:593-665`
(establish_run_archive, run/log metadata, log_section), `:366-374` (metric).
Bash tests ported: `tests/helpers.test.sh:296-350` (metric, art_path,
write_meta, archive_task).

**Files:**
- Modify: `src/adversarial_ai_coding/archive.py` (append)
- Test: `tests/test_archive_io.py`

**Interfaces:**
- Consumes: plan 1 archive helpers, `engines.engine_model` /
  `engines.resolve_model_args`, `config.Settings`.
- Produces:
  - `@dataclass archive.RunArchive:` fields `run_dir: Path`, `run_id: str`,
    `settings: Settings`, `log_path: Path`, `metrics_path: Path`.
  - `archive.establish_run_archive(runs_dir: Path, run_id: str, settings: Settings) -> RunArchive`
    — collision-suffixed run dir (`<id>`, `<id>-2`, `<id>-3`, ...), creates
    `logs/`, log at `logs/001-run.log`, metrics at `metrics.csv` (bash
    :593-606).
  - RunArchive methods (signatures below are binding):
    - `art_path(name: str) -> Path` — `NNN-name`, sequence persisted in
      `.artifact-seq` so numbering survives resume.
    - `write_meta(artifact: Path, role: str = "workflow", engine: str = "workflow", stage: str = "startup", round: int = 0, now: datetime | None = None) -> None`
      — writes `<artifact>.meta.json` with the exact key order of the bash
      jq object: generated_at, generator_role, engine, model, model_args,
      stage, round, run_id, artifact. `model`/`model_args` are derived via
      `engine_model`/`resolve_model_args`; `round` serializes as a string
      (bash passes it as --arg).
    - `archive_snapshot(src: Path, name: str, role, engine, stage, round) -> Path | None`
      — returns None when src is missing (bash returns 0 silently).
    - `archive_text(name: str, text: str, role, engine, stage, round) -> Path`
    - `archive_task(task_arg: str, kind: str, source_path: str, resolved: str) -> None`
      — writes `task-source.md` (with the kind/argument/path header and the
      fenced resolved text) and `task.txt`.
    - `archive_engine_attempt(role: str, engine: str, slug: str, attempt: int, rc: int, engine_out: Path, stage: str, round: int) -> None`
      — copies engine_out (or writes the placeholder line) to
      `<slug>-attempt-<n>-rc<rc>.raw` + meta.
    - `write_run_metadata() / write_log_metadata()` — JSON files matching
      the bash jq key order.
    - `log_section(title: str, role, engine, stage, round, echo: Callable[[str], None] = print) -> None`
      — appends the 80-dash banner to the log and echoes it.
    - `metric(role: str, engine: str, round: int, seconds: int, cost: str, stage: str) -> None`
      — creates header on first write; quoted CSV row (plan 1 helpers).

- [ ] **Step 1: Write the failing tests**

`tests/test_archive_io.py`:

```python
"""Ports helpers.test.sh:296-350 (metric, art_path, write_meta, archive_task)."""

import csv
import json
from datetime import datetime, timedelta, timezone

from adversarial_ai_coding.archive import RunArchive, establish_run_archive
from adversarial_ai_coding.config import Settings

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))


def make_archive(tmp_path, env=None) -> RunArchive:
    settings = Settings.from_env(env or {}, run_id="test")
    return establish_run_archive(tmp_path / "runs", "test", settings)


def test_establish_run_archive_collision_suffix(tmp_path):
    a1 = make_archive(tmp_path)
    a2 = make_archive(tmp_path)
    a3 = make_archive(tmp_path)
    assert a1.run_dir.name == "test"
    assert a2.run_dir.name == "test-2"
    assert a3.run_dir.name == "test-3"
    assert a1.log_path == a1.run_dir / "logs" / "001-run.log"
    assert a1.metrics_path == a1.run_dir / "metrics.csv"


def test_art_path_increments_and_survives_reload(tmp_path):
    # helpers.test.sh: "art_path:increments sequence"
    a = make_archive(tmp_path)
    p1 = a.art_path("first.txt")
    p2 = a.art_path("second.txt")
    assert p1.name == "001-first.txt"
    assert p2.name == "002-second.txt"
    # A new RunArchive over the same dir (resume) continues the sequence.
    b = RunArchive(run_dir=a.run_dir, run_id="test", settings=a.settings,
                   log_path=a.log_path, metrics_path=a.metrics_path)
    assert b.art_path("third.txt").name == "003-third.txt"


def test_write_meta_matches_bash_fields(tmp_path):
    # helpers.test.sh: "write_meta/archive_snapshot:write required metadata"
    a = make_archive(tmp_path, {"ENGINE_A": "claude", "ENGINE_B": "codex"})
    src = tmp_path / "src.txt"
    src.write_text("data\n", encoding="utf-8")
    dst = a.archive_snapshot(src, "snap.txt", role="worker", engine="claude",
                             stage="stage", round=3, now=FIXED)
    meta = json.loads((dst.parent / (dst.name + ".meta.json")).read_text(encoding="utf-8"))
    assert meta["generated_at"] == "2026-01-02T03:04:05+0800"
    assert meta["generator_role"] == "worker"
    assert meta["engine"] == "claude"
    assert meta["stage"] == "stage"
    assert meta["round"] == "3"
    assert meta["run_id"] == "test"
    assert list(meta.keys()) == ["generated_at", "generator_role", "engine",
                                 "model", "model_args", "stage", "round",
                                 "run_id", "artifact"]


def test_archive_snapshot_missing_source_is_noop(tmp_path):
    a = make_archive(tmp_path)
    assert a.archive_snapshot(tmp_path / "absent.txt", "x.txt") is None


def test_archive_task_file_kind(tmp_path):
    # helpers.test.sh: "archive_task:saves file task source and resolved text"
    a = make_archive(tmp_path)
    a.archive_task("task.md", "file", "C:/abs/task.md", "file task\n")
    source = (a.run_dir / "001-task-source.md").read_text(encoding="utf-8")
    assert "- kind: file" in source
    assert "- path: C:/abs/task.md" in source
    assert "file task" in source
    assert (a.run_dir / "002-task.txt").read_text(encoding="utf-8") == "file task\n"


def test_archive_task_literal_kind(tmp_path):
    a = make_archive(tmp_path)
    a.archive_task("literal task", "literal", "", "literal task")
    source = (a.run_dir / "001-task-source.md").read_text(encoding="utf-8")
    assert "- kind: literal" in source
    assert "- path:" not in source


def test_archive_engine_attempt_names_and_placeholder(tmp_path):
    # helpers.test.sh: "engine_call:saves raw output for every retry attempt"
    a = make_archive(tmp_path)
    out = tmp_path / "engine-out.txt"
    out.write_text("raw engine output\n", encoding="utf-8")
    a.archive_engine_attempt("worker", "claude", "worker-stage-r1", 1, 1, out,
                             stage="stage", round=1)
    saved = a.run_dir / "001-worker-stage-r1-attempt-1-rc1.raw"
    assert saved.read_text(encoding="utf-8") == "raw engine output\n"
    meta = json.loads((a.run_dir / "001-worker-stage-r1-attempt-1-rc1.raw.meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["generator_role"] == "worker" and meta["engine"] == "claude"
    a.archive_engine_attempt("worker", "claude", "worker-stage-r1", 2, 1,
                             tmp_path / "gone.txt", stage="stage", round=1)
    placeholder = a.run_dir / "002-worker-stage-r1-attempt-2-rc1.raw"
    assert "ENGINE_OUT was not written" in placeholder.read_text(encoding="utf-8")


def test_metric_header_and_rows(tmp_path):
    # helpers.test.sh: "metric:header plus two rows" / "CSV header is correct"
    a = make_archive(tmp_path, {"ENGINE_A": "claude", "ENGINE_B": "codex",
                                "CODEX_ARGS": '-c model="x,y" --flag "quoted value"'})
    a.metric("worker", "claude", 1, 12, "0.05", stage="stage1")
    a.metric("reviewer", "codex", 2, 30, "", stage="stage1")
    lines = a.metrics_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0] == ("run_id,stage,role,engine,round,duration_s,cost_usd,"
                        "model,model_args,generated_at")
    row = next(csv.reader([lines[2]]))
    assert row[3] == "codex"
    assert row[8] == '-c model="x,y" --flag "quoted value"'
    assert len(row) == 10


def test_log_section_banner(tmp_path):
    a = make_archive(tmp_path)
    echoed = []
    a.log_section("AI call", "worker", "claude", "stage", 2, echo=echoed.append,
                  now=FIXED)
    text = a.log_path.read_text(encoding="utf-8")
    assert "-" * 80 in text
    assert "[2026-01-02T03:04:05+0800] AI call | role=worker engine=claude" in text
    assert "stage=stage round=2" in text
    assert echoed  # banner also went to the console sink


def test_run_and_log_metadata(tmp_path):
    a = make_archive(tmp_path, {"ENGINE_A": "claude", "ENGINE_B": "codex"})
    a.write_run_metadata(spec_dir="specs/test", wf=".workflow", now=FIXED)
    payload = json.loads((a.run_dir / "001-run-metadata.json").read_text(encoding="utf-8"))
    assert payload["run_id"] == "test"
    assert payload["engine_a"] == "claude"
    a.write_log_metadata(now=FIXED)
    log_meta = json.loads((a.log_path.parent / (a.log_path.name + ".meta.json"))
                          .read_text(encoding="utf-8"))
    assert log_meta["generator_role"] == "workflow"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_archive_io.py -q`
Expected: FAIL — ImportError on RunArchive / establish_run_archive.

- [ ] **Step 3: Append to `src/adversarial_ai_coding/archive.py`**

```python
import json
import shutil as _shutil
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .engines import engine_model, resolve_model_args

METRICS_FIELDNAMES = METRICS_HEADER  # alias; header defined in plan 1


@dataclass
class RunArchive:
    """One run's archive under .workflow/runs/<run-id>[-N]/ (sh:593-606)."""

    run_dir: Path
    run_id: str
    settings: Settings
    log_path: Path
    metrics_path: Path

    # -- artifact sequencing (sh:454-468) ------------------------------------
    def art_path(self, name: str) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        seq_file = self.run_dir / ".artifact-seq"
        seq = int(seq_file.read_text(encoding="utf-8").strip()) if seq_file.is_file() else 0
        seq += 1
        seq_file.write_text(f"{seq}\n", encoding="utf-8")
        return self.run_dir / f"{seq:03d}-{name}"

    # -- meta sidecars (sh:470-484) -------------------------------------------
    def write_meta(self, artifact: Path, role: str = "workflow",
                   engine: str = "workflow", stage: str = "startup",
                   round: int = 0, now: "datetime | None" = None) -> None:
        payload = {
            "generated_at": generated_at(now),
            "generator_role": role,
            "engine": engine,
            "model": engine_model(engine, self.settings),
            "model_args": resolve_model_args(engine, self.settings),
            "stage": stage,
            "round": str(round),
            "run_id": self.run_id,
            "artifact": str(artifact),
        }
        artifact.with_name(artifact.name + ".meta.json").write_text(
            json.dumps(payload), encoding="utf-8")

    # -- snapshots (sh:486-505) ------------------------------------------------
    def archive_snapshot(self, src: Path, name: str, role: str = "workflow",
                         engine: str = "workflow", stage: str = "startup",
                         round: int = 0, now: "datetime | None" = None) -> Path | None:
        if not src.is_file():
            return None
        dst = self.art_path(name)
        _shutil.copyfile(src, dst)
        self.write_meta(dst, role, engine, stage, round, now)
        return dst

    def archive_text(self, name: str, text: str, role: str = "workflow",
                     engine: str = "workflow", stage: str = "startup",
                     round: int = 0, now: "datetime | None" = None) -> Path:
        dst = self.art_path(name)
        dst.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        self.write_meta(dst, role, engine, stage, round, now)
        return dst

    # -- task provenance (sh:537-558) -------------------------------------------
    def archive_task(self, task_arg: str, kind: str, source_path: str,
                     resolved: str) -> None:
        lines = ["# Task Source", "", f"- kind: {kind}", f"- argument: {task_arg}"]
        if kind == "file":
            lines.append(f"- path: {source_path}")
        lines += ["", "```", resolved.rstrip("\n"), "```"]
        src_art = self.art_path("task-source.md")
        src_art.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.write_meta(src_art)
        task_art = self.art_path("task.txt")
        task_art.write_text(resolved.rstrip("\n") + "\n", encoding="utf-8")
        self.write_meta(task_art)

    # -- engine attempts (sh:1118-1129) ------------------------------------------
    def archive_engine_attempt(self, role: str, engine: str, slug: str,
                               attempt: int, rc: int, engine_out: Path,
                               stage: str, round: int) -> None:
        dst = self.art_path(f"{slug}-attempt-{attempt}-rc{rc}.raw")
        if engine_out.is_file():
            _shutil.copyfile(engine_out, dst)
        else:
            dst.write_text("(ENGINE_OUT was not written for this attempt)\n",
                           encoding="utf-8")
        self.write_meta(dst, role, engine, stage, round)

    # -- run/log metadata (sh:608-650) ---------------------------------------------
    def write_run_metadata(self, *, spec_dir: str, wf: str,
                           now: "datetime | None" = None) -> None:
        dst = self.art_path("run-metadata.json")
        s = self.settings
        payload = {
            "generated_at": generated_at(now),
            "run_id": self.run_id,
            "spec_dir": spec_dir,
            "wf": wf,
            "runs_dir": str(self.run_dir.parent),
            "wf_run": str(self.run_dir),
            "log": str(self.log_path),
            "metrics": str(self.metrics_path),
            "engine_a": s.engine_a,
            "model_a": engine_model(s.engine_a, s),
            "args_a": resolve_model_args(s.engine_a, s),
            "engine_b": s.engine_b,
            "model_b": engine_model(s.engine_b, s),
            "args_b": resolve_model_args(s.engine_b, s),
            "dual_spec": "1" if s.dual_spec else "0",
            "max_rounds": str(s.max_rounds),
            "auto_branch": "1" if s.auto_branch else "0",
            "use_worktree": "1" if s.use_worktree else "0",
        }
        dst.write_text(json.dumps(payload), encoding="utf-8")

    def write_log_metadata(self, now: "datetime | None" = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        s = self.settings
        payload = {
            "generated_at": generated_at(now),
            "generator_role": "workflow",
            "run_id": self.run_id,
            "log_path": str(self.log_path),
            "engine_a": s.engine_a,
            "model_a": engine_model(s.engine_a, s),
            "args_a": resolve_model_args(s.engine_a, s),
            "engine_b": s.engine_b,
            "model_b": engine_model(s.engine_b, s),
            "args_b": resolve_model_args(s.engine_b, s),
            "dual_spec": "1" if s.dual_spec else "0",
        }
        self.log_path.with_name(self.log_path.name + ".meta.json").write_text(
            json.dumps(payload), encoding="utf-8")

    # -- log banner (sh:652-665) -------------------------------------------------
    def log_section(self, title: str, role: str = "workflow",
                    engine: str = "workflow", stage: str = "startup",
                    round: int = 0, echo: Callable[[str], None] = print,
                    now: "datetime | None" = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        banner = (
            "\n" + "-" * 80 + "\n"
            f"[{generated_at(now)}] {title} | role={role} engine={engine} "
            f"model={engine_model(engine, self.settings)} "
            f"args={resolve_model_args(engine, self.settings)} "
            f"stage={stage} round={round}\n" + "-" * 80
        )
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(banner + "\n")
        echo(banner)

    # -- metrics (sh:366-374) ------------------------------------------------------
    def metric(self, role: str, engine: str, round: int, seconds: int,
               cost: str, stage: str, now: "datetime | None" = None) -> None:
        if not self.metrics_path.parent.is_dir():
            return
        if not self.metrics_path.is_file():
            self.metrics_path.write_text(",".join(METRICS_HEADER) + "\n",
                                         encoding="utf-8")
        write_csv_row(self.metrics_path, [
            self.run_id, stage, role, engine, round, seconds, cost,
            engine_model(engine, self.settings),
            resolve_model_args(engine, self.settings),
            generated_at(now),
        ])


def establish_run_archive(runs_dir: Path, run_id: str,
                          settings: Settings) -> RunArchive:
    base = runs_dir / run_id
    candidate = base
    n = 2
    while candidate.exists():
        candidate = runs_dir / f"{run_id}-{n}"
        n += 1
    logs = candidate / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return RunArchive(run_dir=candidate, run_id=run_id, settings=settings,
                      log_path=logs / "001-run.log",
                      metrics_path=candidate / "metrics.csv")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_archive_io.py -q` then `uv run pytest -q`
Expected: all PASS, whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/archive.py tests/test_archive_io.py
git commit -m "feat: port run archive artifacts, meta, log, and metrics

Port establish_run_archive with collision suffixes, the persistent
artifact sequence, meta sidecars with the exact bash jq key order,
text/snapshot/task/engine-attempt archiving, run and log metadata, the
log section banner, and the metric CSV writer built on the plan 1
helpers. All methods take explicit stage/round/now parameters instead
of bash's globals."
```

---

### Task 5: Git state snapshots (`archive_git_state`)

Bash reference: `adversarial-ai-coding.sh:560-583`.
Bash tests ported: `tests/helpers.test.sh:352-362`.

**Files:**
- Modify: `src/adversarial_ai_coding/archive.py` (append method)
- Test: `tests/test_archive_git.py`

**Interfaces:**
- Produces: `RunArchive.archive_git_state(role: str, engine: str, slug: str, stage: str, round: int, cwd: Path | None = None) -> None`
  — no-op outside a git work tree; writes `<slug>-git-status.txt`
  (porcelain) and `<slug>-git-diff.patch` (tracked `git diff --binary HEAD`
  plus each untracked file via `git diff --no-index --binary /dev/null`),
  both with meta sidecars; leaves index/status untouched. On Windows,
  `/dev/null` in the `--no-index` diff is replaced by `os.devnull` ("NUL"),
  and git prints the same patch content.

- [ ] **Step 1: Write the failing tests**

`tests/test_archive_git.py`:

```python
"""Ports helpers.test.sh:352-362 (archive_git_state side effects)."""

import subprocess

from adversarial_ai_coding.archive import establish_run_archive
from adversarial_ai_coding.config import Settings


def porcelain(repo):
    return subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                          capture_output=True, text=True, check=True).stdout


def test_archive_git_state_no_side_effects_and_untracked_content(new_repo):
    (new_repo / "base.txt").write_text("changed\n", encoding="utf-8")
    (new_repo / "new.txt").write_text("new content\n", encoding="utf-8")
    settings = Settings.from_env({}, run_id="test")
    archive = establish_run_archive(new_repo / ".workflow" / "runs", "test", settings)
    before = porcelain(new_repo)
    archive.archive_git_state("worker", "claude", "worker-code-r2",
                              stage="code", round=2, cwd=new_repo)
    after = porcelain(new_repo)
    assert before == after  # no index/status side effects
    patch = (archive.run_dir / "002-worker-code-r2-git-diff.patch").read_text(encoding="utf-8")
    assert "new content" in patch      # untracked file content captured
    assert "changed" in patch          # tracked modification captured
    status = (archive.run_dir / "001-worker-code-r2-git-status.txt").read_text(encoding="utf-8")
    assert "base.txt" in status and "new.txt" in status


def test_archive_git_state_outside_repo_is_noop(tmp_path):
    settings = Settings.from_env({}, run_id="test")
    archive = establish_run_archive(tmp_path / "runs", "test", settings)
    archive.archive_git_state("worker", "claude", "slug", stage="s", round=1,
                              cwd=tmp_path)
    assert not list(archive.run_dir.glob("*git-status*"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_archive_git.py -q`
Expected: FAIL — AttributeError: no archive_git_state.

- [ ] **Step 3: Append the method to `RunArchive`**

```python
    def archive_git_state(self, role: str = "worker", engine: str = "workflow",
                          slug: str = "git-state", stage: str = "startup",
                          round: int = 0, cwd: Path | None = None) -> None:
        import os
        import subprocess

        def git(*args, check=False):
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  check=check)

        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return
        status_art = self.art_path(f"{slug}-git-status.txt")
        status_art.write_text(git("status", "--porcelain").stdout, encoding="utf-8")
        self.write_meta(status_art, role, engine, stage, round)

        diff_art = self.art_path(f"{slug}-git-diff.patch")
        chunks = ["# git diff --binary HEAD --\n",
                  git("diff", "--binary", "HEAD", "--").stdout, "\n",
                  "# untracked files\n"]
        listing = git("ls-files", "--others", "--exclude-standard", "-z").stdout
        for name in filter(None, listing.split("\0")):
            chunks += [f"\n## {name}\n",
                       git("diff", "--no-index", "--binary", "--",
                           os.devnull, name).stdout]
        diff_art.write_text("".join(chunks), encoding="utf-8")
        self.write_meta(diff_art, role, engine, stage, round)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/archive.py tests/test_archive_git.py
git commit -m "feat: port git state snapshots into the run archive

Port archive_git_state: porcelain status plus a patch combining the
tracked diff against HEAD and every untracked file rendered via git
diff --no-index against the null device (os.devnull for Windows). The
capture has no index or status side effects, verified by test."
```

---

## Verification at the End of This Plan

Run: `uv run pytest -q`
Expected: whole suite green (plans 1-2 tests plus ~35 new).

## Not in This Plan (deliberately)

- `begin_stage`/`end_stage` skip-and-echo orchestration, `print_resume_hint`,
  traps/exit-code handling: plan 5 (workflow/cli).
- `verify_last_head`, `resume_workspace`, `setup_workspace`: plan 4 (gitops).
- Wiring `RetryEvents.archive_attempt` to `RunArchive.archive_engine_attempt`:
  plan 5 (workflow context assembly).
