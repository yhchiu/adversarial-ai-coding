# Phased ATDD (PHASES=1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in `PHASES=1` mode where the plan is split into vertical
phases, each phase gets its own JIT-written protected acceptance tests, a
deterministic red check, per-task commits, and a phase gate, per the approved
spec `docs/superpowers/specs/2026-07-17-phased-atdd-design.md`.

**Architecture:** A new `phases.py` module parses the phased plan format; a
new `phaseflow.py` module drives the per-phase stages by calling `workflow.py`
primitives through the module object (`wf.work`, `wf.review_loop_ref`, ...) so
existing monkeypatch seams keep working — the same pattern `dual_spec.py`
uses. `run_workflow` branches on `settings.phases`: the `PHASES=0` path is
byte-identical to today.

**Tech Stack:** Python 3.12+, stdlib only, pytest (dev-only), uv.

## Global Constraints

- Python 3.12+, stdlib only at runtime; pytest is dev-only.
- `PHASES=0` behavior must stay identical; all existing tests keep passing
  (the only allowed edit to existing tests: add the new `phase_gate_cmd`
  keyword where `snapshot_values(` is called).
- Commits: Conventional Commits in simple English, detailed body, and NO
  `Co-Authored-By` trailer (repo rule, see `resources/AGENTS.template.md`).
- ASCII only in prompts, plans, and test data.
- Windows-compatible: `pathlib` for paths; shell commands run through
  `gates.run_shell` (platform shell).
- Run tests with `uv run pytest -q` from the repo root. On this machine, if
  imports fail under uv, clear `PYTHONHOME` and `PYTHONPATH` first (a system
  Atrust Python 2.7 environment breaks venvs).
- Full suite must pass at the end of every task, before its commit.

---

### Task 1: PHASES / PHASE_GATE_CMD / PHASE_REVIEW settings plumbing

**Files:**
- Modify: `src/adversarial_ai_coding/config.py`
- Modify: `src/adversarial_ai_coding/runstate.py` (SNAPSHOT_KEYS, IMMUTABLE_KEYS, `snapshot_values`, `check_immutable`)
- Modify: `src/adversarial_ai_coding/cli.py`
- Modify: `src/adversarial_ai_coding/workflow.py` (one new `WorkflowContext` field)
- Test: `tests/test_config.py`, `tests/test_runstate_snapshot.py`

**Interfaces:**
- Consumes: existing `Settings.from_env(env, run_id, snapshot)`, `snapshot_values(settings, *, branch, gate_cmd, build_gate_cmd, task_arg, task_source_kind, task_source_path)`.
- Produces: `Settings.phases: bool`, `Settings.phase_review: bool`,
  `snapshot_values(..., phase_gate_cmd: str)` (new required keyword),
  snapshot keys `phases` / `phase_review` / `phase_gate_cmd`,
  `IMMUTABLE_KEYS` containing `"PHASES"`,
  `WorkflowContext.phase_gate_cmd: str = ""`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
def test_phase_settings_defaults_and_parsing():
    settings = Settings.from_env({}, run_id="r")
    assert settings.phases is False
    assert settings.phase_review is False
    settings = Settings.from_env({"PHASES": "1", "PHASE_REVIEW": "1"}, run_id="r")
    assert settings.phases is True
    assert settings.phase_review is True


def test_phase_settings_resume_from_snapshot():
    settings = Settings.from_env(
        {}, run_id="r", snapshot={"PHASES": "1", "PHASE_REVIEW": "1"}
    )
    assert settings.phases is True
    assert settings.phase_review is True
```

Append to `tests/test_runstate_snapshot.py` (match its existing imports;
add `check_immutable`, `snapshot_values`, `RunStateError` if missing):

```python
def test_snapshot_values_records_phase_settings():
    from adversarial_ai_coding.config import Settings

    settings = Settings.from_env({"PHASES": "1", "PHASE_REVIEW": "1"}, run_id="r")
    values = snapshot_values(
        settings,
        branch="b",
        gate_cmd="g",
        build_gate_cmd="bg",
        phase_gate_cmd="pg",
        task_arg="t",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["phases"] == "1"
    assert values["phase_review"] == "1"
    assert values["phase_gate_cmd"] == "pg"


def test_snapshot_round_trips_phase_keys(tmp_path):
    values = {key: "" for key in SNAPSHOT_KEYS}
    values.update(
        {"phases": "1", "phase_review": "1", "phase_gate_cmd": "go test ./..."}
    )
    write_snapshot(tmp_path, values)
    snapshot = load_snapshot(tmp_path)
    assert snapshot["PHASES"] == "1"
    assert snapshot["PHASE_REVIEW"] == "1"
    assert snapshot["PHASE_GATE_CMD"] == "go test ./..."


def test_check_immutable_refuses_phases_conflict():
    with pytest.raises(RunStateError, match="PHASES=1 conflicts"):
        check_immutable({"PHASES": "1"}, {"PHASES": "0"})
    check_immutable({"PHASES": "1"}, {"PHASES": "1"})
    check_immutable({}, {"PHASES": "0"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_config.py tests/test_runstate_snapshot.py`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'phases'`, `TypeError: snapshot_values() got an unexpected keyword argument`).

- [ ] **Step 3: Implement**

`config.py` — add two fields after `dual_spec: bool`:

```python
    dual_spec: bool
    phases: bool
    phase_review: bool
```

and in `from_env`, after the `dual_spec=` line:

```python
            phases=persisted("PHASES", "0") == "1",
            phase_review=persisted("PHASE_REVIEW", "0") == "1",
```

`runstate.py` — in `SNAPSHOT_KEYS`, after `"dual_spec",` add:

```python
    "phases",
    "phase_review",
```

and after `"build_gate_cmd",` add:

```python
    "phase_gate_cmd",
```

Change `IMMUTABLE_KEYS`:

```python
IMMUTABLE_KEYS = ("SPEC_DIR", "DUAL_SPEC", "AUTO_BRANCH", "USE_WORKTREE", "PHASES")
```

In `snapshot_values`, add the keyword parameter `phase_gate_cmd: str` (in the
keyword-only group, after `build_gate_cmd: str`) and add to the returned dict,
after `"dual_spec": ...`:

```python
        "phases": flag(settings.phases),
        "phase_review": flag(settings.phase_review),
```

and after `"build_gate_cmd": build_gate_cmd,`:

```python
        "phase_gate_cmd": phase_gate_cmd,
```

In `check_immutable`, update the explanation line:

```python
            "   SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE/PHASES decide the "
            "stage graph and cannot change across resume.\n"
```

`workflow.py` — in `WorkflowContext`, after `build_gate_cmd: str = ""`:

```python
    phase_gate_cmd: str = ""
```

`cli.py` — after the `build_gate_cmd = (...)` block:

```python
        phase_gate_cmd = (
            env.get("PHASE_GATE_CMD") or snapshot.get("PHASE_GATE_CMD") or ""
        )
```

Pass it to the context (after `build_gate_cmd=build_gate_cmd,`):

```python
            phase_gate_cmd=phase_gate_cmd,
```

and to `snapshot_values` (after `build_gate_cmd=build_gate_cmd,`):

```python
                phase_gate_cmd=phase_gate_cmd,
```

Extend the settings banner (the `print("Workflow settings:...")` call) to end
with `PHASES`:

```python
        print(
            f"Workflow settings:A={settings.agent_a}  B={settings.agent_b}  "
            f"DUAL_SPEC={'1' if settings.dual_spec else '0'}  "
            f"MAX_ROUNDS={settings.max_rounds}  SPEC_DIR={settings.spec_dir}  "
            f"PHASES={'1' if settings.phases else '0'}"
        )
```

- [ ] **Step 4: Fix other snapshot_values call sites**

Run: `rg -n "snapshot_values\(" src tests`
Add `phase_gate_cmd=""` to every call that does not already pass it: one in
`cli.py` (already handled in Step 3) and five in
`tests/test_runstate_snapshot.py`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat(config): add PHASES, PHASE_GATE_CMD and PHASE_REVIEW settings" -m "PHASES=1 opts into the phased ATDD flow and is immutable across resume because it decides the stage graph, like DUAL_SPEC. PHASE_GATE_CMD is resolved env-then-snapshot with no auto-detection; an empty value falls back to GATE_CMD at use time. PHASE_REVIEW=1 will enable the optional per-phase diff review. All three persist in the schema-1 settings snapshot; WorkflowContext carries phase_gate_cmd alongside the other gate commands."
```

---

### Task 2: Phased plan parser (`phases.py`)

**Files:**
- Create: `src/adversarial_ai_coding/phases.py`
- Test: `tests/test_phases.py`

**Interfaces:**
- Consumes: nothing from the package (stdlib only).
- Produces: `Phase(number: int, title: str, regression_guard: bool, tasks: tuple[str, ...])` (frozen dataclass), `parse_phases(plan_path: Path) -> tuple[Phase, ...]`, `PhasePlanError(Exception)`, `TASK_PREFIX = "- [ ] "`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phases.py`:

```python
"""Phased plan parsing: headings, markers, and structure problems."""

import pytest

from adversarial_ai_coding.phases import Phase, PhasePlanError, parse_phases

VALID = """# Plan

Intro prose is allowed outside phases.

## Phase 1: feature works
Acceptance: the CLI prints the result.
- [ ] add the flag
- [ ] emit output

## Phase 2: old behavior unchanged (regression-guard)
Acceptance: output without the flag is unchanged.
- [ ] add regression fixture
"""


def _write(tmp_path, text):
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return plan


def test_parses_phases_titles_guard_and_tasks(tmp_path):
    phases = parse_phases(_write(tmp_path, VALID))
    assert phases == (
        Phase(
            number=1,
            title="feature works",
            regression_guard=False,
            tasks=("add the flag", "emit output"),
        ),
        Phase(
            number=2,
            title="old behavior unchanged",
            regression_guard=True,
            tasks=("add regression fixture",),
        ),
    )


def test_missing_acceptance_line_is_a_problem(tmp_path):
    text = "## Phase 1: x\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="no 'Acceptance:' line"):
        parse_phases(_write(tmp_path, text))


def test_phase_without_tasks_is_a_problem(tmp_path):
    text = "## Phase 1: x\nAcceptance: y.\n"
    with pytest.raises(PhasePlanError, match="no '- \\[ \\] ' task"):
        parse_phases(_write(tmp_path, text))


def test_task_outside_any_phase_is_a_problem(tmp_path):
    text = "- [ ] stray\n## Phase 1: x\nAcceptance: y.\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="task outside any phase: stray"):
        parse_phases(_write(tmp_path, text))


def test_non_sequential_numbering_is_a_problem(tmp_path):
    text = (
        "## Phase 1: x\nAcceptance: y.\n- [ ] t\n"
        "## Phase 3: z\nAcceptance: y.\n- [ ] t\n"
    )
    with pytest.raises(PhasePlanError, match="found Phase 3, expected Phase 2"):
        parse_phases(_write(tmp_path, text))


def test_empty_title_is_a_problem(tmp_path):
    text = "## Phase 1: \nAcceptance: y.\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="empty title"):
        parse_phases(_write(tmp_path, text))


def test_no_headings_is_a_problem(tmp_path):
    with pytest.raises(PhasePlanError, match="no '## Phase N: <title>' headings"):
        parse_phases(_write(tmp_path, "# Plan\n\nprose only\n"))


def test_missing_file_is_a_problem(tmp_path):
    with pytest.raises(PhasePlanError, match="plan file not found"):
        parse_phases(tmp_path / "absent.md")


def test_all_problems_are_reported_together(tmp_path):
    text = "- [ ] stray\n## Phase 2: x\n"
    with pytest.raises(PhasePlanError) as excinfo:
        parse_phases(_write(tmp_path, text))
    message = str(excinfo.value)
    assert "task outside any phase" in message
    assert "expected Phase 1" in message
    assert "no 'Acceptance:' line" in message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_phases.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'adversarial_ai_coding.phases'`.

- [ ] **Step 3: Implement**

Create `src/adversarial_ai_coding/phases.py`:

```python
"""Phased plan parsing (Phased ATDD, PHASES=1).

plan.md is split into "## Phase N: <title>" sections. Each phase needs an
observable "Acceptance:" line and at least one "- [ ] " task. A trailing
"(regression-guard)" on the title flips the red-check expectation: those
tests must pass immediately instead of starting red.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TASK_PREFIX = "- [ ] "
_PHASE_HEADING = re.compile(
    r"^## Phase (?P<number>\d+):(?P<title>.*?)(?P<guard>\(regression-guard\))?\s*$"
)


class PhasePlanError(Exception):
    """plan.md does not have a usable phased structure."""


@dataclass(frozen=True)
class Phase:
    number: int
    title: str
    regression_guard: bool
    tasks: tuple[str, ...]


def parse_phases(plan_path: Path) -> tuple[Phase, ...]:
    if not plan_path.is_file():
        raise PhasePlanError(f"plan file not found: {plan_path}")
    problems: list[str] = []
    phases: list[Phase] = []
    current: dict | None = None

    def close(section: dict | None) -> None:
        if section is None:
            return
        if not section["title"]:
            problems.append(f"Phase {section['number']} has an empty title")
        if not section["acceptance"]:
            problems.append(
                f"Phase {section['number']} has no 'Acceptance:' line"
            )
        if not section["tasks"]:
            problems.append(f"Phase {section['number']} has no '- [ ] ' task")
        phases.append(
            Phase(
                number=section["number"],
                title=section["title"],
                regression_guard=section["guard"],
                tasks=tuple(section["tasks"]),
            )
        )

    for line in plan_path.read_text(encoding="utf-8").splitlines():
        heading = _PHASE_HEADING.match(line)
        if heading:
            close(current)
            number = int(heading.group("number"))
            expected = len(phases) + 1
            if number != expected:
                problems.append(
                    "Phase numbering must be sequential: "
                    f"found Phase {number}, expected Phase {expected}"
                )
            current = {
                "number": number,
                "title": heading.group("title").strip(),
                "guard": heading.group("guard") is not None,
                "acceptance": False,
                "tasks": [],
            }
            continue
        if line.startswith(TASK_PREFIX):
            if current is None:
                problems.append(
                    f"task outside any phase: {line[len(TASK_PREFIX):]}"
                )
            else:
                current["tasks"].append(line[len(TASK_PREFIX):])
            continue
        if line.startswith("Acceptance:") and current is not None:
            current["acceptance"] = True
    close(current)
    if not phases and not problems:
        problems.append("no '## Phase N: <title>' headings found")
    if problems:
        raise PhasePlanError(
            "plan.md is not a valid phased plan:\n"
            + "".join(f"- {problem}\n" for problem in problems)
        )
    return tuple(phases)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_phases.py`
Expected: all pass. Then `uv run pytest -q` (full suite) must also pass.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/phases.py tests/test_phases.py
git commit -m "feat(phases): add the phased plan parser" -m "Parse plan.md into Phase records: sequential '## Phase N: <title>' headings, an observable 'Acceptance:' line and at least one '- [ ] ' task per phase, and an optional trailing '(regression-guard)' marker that flips the red-check expectation. All structure problems are collected and reported together in one PhasePlanError so a single repair round can fix the whole plan."
```

---

### Task 3: Phase persistence and per-phase queues in run state

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py`
- Test: `tests/test_phased_state.py` (new)

**Interfaces:**
- Consumes: `Phase`, `parse_phases`, `PhasePlanError` from Task 2; existing `RunState`, `_atomic_write`, `_queue_path`.
- Produces:
  - `save_phases(state: RunState, phases) -> None` / `load_phases(state) -> tuple[Phase, ...] | None` / `ensure_phases(state: RunState, plan_path: Path) -> tuple[Phase, ...]` (file `phases.json`, schema 1)
  - `phase_queue_name(number: int) -> str` (returns `tasks-remaining-phase-NN`)
  - `ensure_named_task_queue(state: RunState, name: str, tasks: list[str]) -> None`
  - `remaining_tasks(state, name="tasks-remaining")` and `pop_task_queue(state, name="tasks-remaining")` (default keeps existing callers working)
  - `restore_or_record_base(state: RunState | None, name: str, head_sha: Callable[[], str]) -> str`; `restore_or_record_acceptance_base` now delegates to it with name `"acceptance-test-base"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phased_state.py`:

```python
"""Phase structure persistence and per-phase task queues."""

import pytest

from adversarial_ai_coding.phases import Phase
from adversarial_ai_coding.runstate import (
    RunState,
    RunStateError,
    ensure_named_task_queue,
    ensure_phases,
    load_phases,
    phase_queue_name,
    pop_task_queue,
    remaining_tasks,
    restore_or_record_base,
    save_phases,
)

PHASES = (
    Phase(number=1, title="one", regression_guard=False, tasks=("a", "b")),
    Phase(number=2, title="two", regression_guard=True, tasks=("c",)),
)


@pytest.fixture
def state(tmp_path):
    return RunState.create(tmp_path / "state", "run", "t\n")


def test_save_and_load_phases_round_trip(state):
    save_phases(state, PHASES)
    assert load_phases(state) == PHASES


def test_load_phases_returns_none_when_absent(state):
    assert load_phases(state) is None


def test_load_phases_refuses_damaged_json(state):
    (state.state_dir / "phases.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(RunStateError, match="not valid JSON"):
        load_phases(state)


def test_ensure_phases_parses_once_then_trusts_state(state, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Phase 1: one\nAcceptance: x.\n- [ ] a\n- [ ] b\n"
        "## Phase 2: two (regression-guard)\nAcceptance: y.\n- [ ] c\n",
        encoding="utf-8",
    )
    assert ensure_phases(state, plan) == PHASES
    plan.write_text("garbage, no phases\n", encoding="utf-8")
    assert ensure_phases(state, plan) == PHASES


def test_phase_queue_name_is_zero_padded():
    assert phase_queue_name(3) == "tasks-remaining-phase-03"


def test_named_queues_are_independent(state):
    ensure_named_task_queue(state, phase_queue_name(1), ["a", "b"])
    ensure_named_task_queue(state, phase_queue_name(2), ["c"])
    pop_task_queue(state, phase_queue_name(1))
    assert remaining_tasks(state, phase_queue_name(1)) == ["b"]
    assert remaining_tasks(state, phase_queue_name(2)) == ["c"]
    # ensure is idempotent: an existing (even empty) queue is kept
    pop_task_queue(state, phase_queue_name(2))
    ensure_named_task_queue(state, phase_queue_name(2), ["c"])
    assert remaining_tasks(state, phase_queue_name(2)) == []


def test_restore_or_record_base_is_per_name(state):
    shas = iter(["sha-one", "sha-two"])
    assert restore_or_record_base(state, "phase-01-test-base", lambda: next(shas)) == "sha-one"
    assert restore_or_record_base(state, "phase-02-test-base", lambda: next(shas)) == "sha-two"
    assert (
        restore_or_record_base(state, "phase-01-test-base", lambda: "never")
        == "sha-one"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_phased_state.py`
Expected: FAIL with ImportError on the new names.

- [ ] **Step 3: Implement**

In `runstate.py`, add near the top imports:

```python
from .phases import Phase, parse_phases
```

Add after `restore_or_record_acceptance_base` (and rewrite that function to
delegate, keeping its warning comment):

```python
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
```

```python
def restore_or_record_acceptance_base(
    state: RunState | None, head_sha: Callable[[], str]
) -> str:
    # Without persistence, an interrupt between the acceptance commit and the
    # protected-list write would recompute an empty diff on resume and silently
    # disable test protection (C4, sh:832-849).
    return restore_or_record_base(state, "acceptance-test-base", head_sha)
```

Add phase persistence:

```python
PHASES_FILE = "phases.json"


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
    return tuple(
        Phase(
            number=int(entry["number"]),
            title=str(entry["title"]),
            regression_guard=bool(entry["regression_guard"]),
            tasks=tuple(str(task) for task in entry["tasks"]),
        )
        for entry in payload.get("phases", [])
    )


def ensure_phases(state: RunState, plan_path: Path) -> tuple[Phase, ...]:
    # The persisted structure is control flow; plan.md is UI after this point.
    saved = load_phases(state)
    if saved is not None:
        return saved
    phases = parse_phases(plan_path)
    save_phases(state, phases)
    return phases
```

Generalize the queue helpers (keep `ensure_task_queue` and `plan_tasks`
unchanged except for the `_queue_path` signature):

```python
def _queue_path(state: RunState, name: str = "tasks-remaining") -> Path:
    return state.state_dir / name


def phase_queue_name(number: int) -> str:
    return f"tasks-remaining-phase-{number:02d}"


def ensure_named_task_queue(state: RunState, name: str, tasks: list[str]) -> None:
    queue = _queue_path(state, name)
    if queue.is_file():
        return
    _atomic_write(queue, "".join(task + "\n" for task in tasks))


def remaining_tasks(state: RunState, name: str = "tasks-remaining") -> list[str]:
    queue = _queue_path(state, name)
    if not queue.is_file():
        return []
    return [line for line in queue.read_text(encoding="utf-8").splitlines() if line]


def pop_task_queue(state: RunState, name: str = "tasks-remaining") -> None:
    tasks = remaining_tasks(state, name)
    _atomic_write(_queue_path(state, name), "".join(task + "\n" for task in tasks[1:]))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_phased_state.py`
Expected: all pass. Then `uv run pytest -q` (full suite; existing
`test_runstate_crossstage.py` proves the default-name path still works).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_phased_state.py
git commit -m "feat(runstate): persist phase structure and per-phase task queues" -m "Store the parsed phase list in phases.json (schema 1) so a resumed run trusts state and never re-parses plan.md, matching the task-queue snapshot philosophy. Task queues and diff bases become name-parameterized: each phase gets tasks-remaining-phase-NN and phase-NN-test-base files while the default names keep the PHASES=0 path byte-identical."
```

---

### Task 4: Phased prompt templates

**Files:**
- Create: `resources/prompts/write-implementation-plan-phased.md`
- Create: `resources/prompts/review-scope-plan-phased.md`
- Create: `resources/prompts/phased-plan-invalid.md`
- Create: `resources/prompts/write-phase-tests.md`
- Create: `resources/prompts/phase-red-check-failed.md`
- Create: `resources/prompts/review-scope-phase.md`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `render_prompt(prompts_dir, name, replacements)`.
- Produces: template names and their placeholder sets, exactly as listed in
  the test below. Later tasks render them with those variables. The first
  line of `write-phase-tests.md` must start with
  `Write acceptance tests for exactly one phase of the plan: "{{PHASE_TITLE}}"`
  (the fake agent and archives key off it), and
  `write-implementation-plan-phased.md` must start with
  `Write an implementation plan` (keeps the fake agent's classifier working).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_prompts.py` (add imports if missing:
`import pytest`, `from adversarial_ai_coding.prompts import default_prompts_dir, render_prompt`):

```python
PHASED_TEMPLATES = {
    "write-implementation-plan-phased": {
        "SPEC_FILE": "specfile.md",
        "PLAN_FILE": "planfile.md",
    },
    "review-scope-plan-phased": {
        "PLAN_FILE": "planfile.md",
        "SPEC_FILE": "specfile.md",
    },
    "phased-plan-invalid": {
        "PLAN_FILE": "planfile.md",
        "PROBLEMS": "- Phase 1 has no 'Acceptance:' line",
    },
    "write-phase-tests": {
        "SPEC_FILE": "specfile.md",
        "PLAN_FILE": "planfile.md",
        "SPEC_DIR": "specdir",
        "PHASE_TITLE": "phase-title",
        "PHASES_DONE": "phases-done",
        "PROTECTED_TESTS_FILE": "protectedfile.txt",
    },
    "phase-red-check-failed": {
        "COMMAND": "gate-command",
        "EXPECTED": "expected-text",
        "PHASE_TITLE": "phase-title",
        "OUTPUT": "tail-output",
    },
    "review-scope-phase": {
        "PHASE_TITLE": "phase-title",
        "PHASE_BASE": "base-sha",
        "PLAN_FILE": "planfile.md",
    },
}


@pytest.mark.parametrize("name", sorted(PHASED_TEMPLATES))
def test_phased_templates_render_every_placeholder(name):
    rendered = render_prompt(default_prompts_dir({}), name, PHASED_TEMPLATES[name])
    assert "{{" not in rendered
    for value in PHASED_TEMPLATES[name].values():
        assert value in rendered


def test_write_phase_tests_first_line_is_stable():
    rendered = render_prompt(
        default_prompts_dir({}), "write-phase-tests", PHASED_TEMPLATES["write-phase-tests"]
    )
    assert rendered.startswith(
        'Write acceptance tests for exactly one phase of the plan: "phase-title"'
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest -q tests/test_prompts.py`
Expected: FAIL with `PromptTemplateError` (template not found).

- [ ] **Step 3: Create the templates**

`resources/prompts/write-implementation-plan-phased.md`:

```text
Write an implementation plan from {{SPEC_FILE}} and save it to {{PLAN_FILE}}. Split the work into phases using "## Phase N: <title>" headings, numbered from 1. Each phase must be a vertical functional slice with observable behavior at a stable boundary (CLI, API, public interface, or file output), never a horizontal technical layer. Bad phases: "build the database layer", "build the service layer". Good phases: "basic success case works end to end", "empty input returns a valid empty result". Each phase must contain exactly one "Acceptance:" line describing the observable behavior that proves the phase is done, followed by an implementation task list, one task per line, using the "- [ ] " checkbox format. Each task must be independently implementable and verifiable, and each task maps to one commit. Do not put "- [ ] " lines outside a phase. If a phase only locks in existing behavior that must not change, end its title with " (regression-guard)"; its tests are expected to pass immediately. Include a test strategy that decides whether unit, integration, or E2E tests are needed, with reasons.
```

`resources/prompts/review-scope-plan-phased.md`:

```text
{{PLAN_FILE}} compared with {{SPEC_FILE}}: feasibility, test coverage, whether tasks are small and independent, and whether checkbox format is correct. The plan must be split into "## Phase N: <title>" sections. Treat as blockers: a phase that is a horizontal technical layer instead of a vertical functional slice, a phase without an "Acceptance:" line describing observable behavior at a stable boundary, a phase without "- [ ] " tasks, and "- [ ] " tasks outside any phase.
```

`resources/prompts/phased-plan-invalid.md`:

```text
{{PLAN_FILE}} does not follow the required phased structure. Problems found:
{{PROBLEMS}}
Fix the plan file so phases are numbered sequentially from 1, every "## Phase N: <title>" section has an "Acceptance:" line and at least one "- [ ] " task, and no "- [ ] " task is outside a phase. Do not change the meaning of the plan; only repair the structure.
```

`resources/prompts/write-phase-tests.md`:

```text
Write acceptance tests for exactly one phase of the plan: "{{PHASE_TITLE}}". Read this phase's Acceptance line and tasks in {{PLAN_FILE}} and the spec in {{SPEC_FILE}}. Phases already completed: {{PHASES_DONE}}. Test this phase's observable behavior at its stable boundary (CLI, API, public interface, or file output); component or contract level is fine, full end-to-end is not required. Do not write tests for later phases. The implementation for this phase does not exist yet, so its tests may fail to compile or be red; this is the TDD red phase. Tests from completed phases must stay green. Do not write product code, do not modify files listed in {{PROTECTED_TESTS_FILE}}, and do not modify files under {{SPEC_DIR}}.
```

`resources/prompts/phase-red-check-failed.md`:

```text
The workflow ran the phase test check "{{COMMAND}}" for phase "{{PHASE_TITLE}}" and the result was wrong: {{EXPECTED}}. Fix the tests for this phase so the check behaves as expected. Do not write product code. Here is the output, limited to the last 150 lines:
{{OUTPUT}}
```

`resources/prompts/review-scope-phase.md`:

```text
The diff for phase "{{PHASE_TITLE}}", using git diff from {{PHASE_BASE}}: does the implementation satisfy this phase's Acceptance line in {{PLAN_FILE}}, is the change limited to this phase's scope, and is the code quality acceptable?
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_prompts.py`
Expected: all pass. Then `uv run pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add resources/prompts tests/test_prompts.py
git commit -m "feat(prompts): add the phased workflow prompt templates" -m "Six templates for PHASES=1: a phased plan writer and review scope that demand vertical slices with Acceptance lines, a deterministic structure-repair prompt, a single-phase test writer that forbids testing later phases, a red-check repair prompt for the test author, and a per-phase diff review scope. The phased plan template keeps the same opening words as the flat one and write-phase-tests has a stable first line, so prompt classification by tools and tests stays simple."
```

---

### Task 5: Extract `record_protected_tests` (replace + append)

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py` (extract from the
  `write-acceptance-tests` block, currently workflow.py:756-800)
- Test: `tests/test_protected_recording.py` (new)

**Interfaces:**
- Consumes: `git_out`, `head_sha` from gitops; `_require_regular_or_missing_control`.
- Produces: `record_protected_tests(ctx: WorkflowContext, test_base: str, *, append: bool = False) -> list[str]` — returns the newly detected names only; writes both control files, archives snapshots, logs the full protected list.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_protected_recording.py`:

```python
"""record_protected_tests: replace mode (stage 4) and append mode (phases)."""

import subprocess

from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.workflow import record_protected_tests


def _commit_file(repo, name, message):
    (repo / name).write_text(f"{name}\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message],
        check=True,
        capture_output=True,
    )


def test_replace_then_append_grows_the_list(make_ctx, new_repo):
    ctx = make_ctx()
    base_one = head_sha(new_repo)
    _commit_file(new_repo, "test_one.py", "phase 1 tests")
    assert record_protected_tests(ctx, base_one) == ["test_one.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\n"
    assert (ctx.wf / "protected-base.sha").read_text(
        encoding="utf-8"
    ).strip() == head_sha(new_repo)

    base_two = head_sha(new_repo)
    _commit_file(new_repo, "test_two.py", "phase 2 tests")
    assert record_protected_tests(ctx, base_two, append=True) == ["test_two.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\ntest_two.py\n"
    assert (ctx.wf / "protected-base.sha").read_text(
        encoding="utf-8"
    ).strip() == head_sha(new_repo)


def test_append_dedupes_and_replace_overwrites(make_ctx, new_repo):
    ctx = make_ctx()
    base = head_sha(new_repo)
    _commit_file(new_repo, "test_one.py", "tests")
    record_protected_tests(ctx, base)
    assert record_protected_tests(ctx, base, append=True) == ["test_one.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_one.py\n"
    base_two = head_sha(new_repo)
    _commit_file(new_repo, "test_two.py", "more tests")
    assert record_protected_tests(ctx, base_two) == ["test_two.py"]
    assert (ctx.wf / "protected-tests.txt").read_text(
        encoding="utf-8"
    ) == "test_two.py\n"


def test_spec_dir_files_are_excluded(make_ctx, new_repo):
    ctx = make_ctx()
    base = head_sha(new_repo)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    (ctx.spec_dir / "spec.md").write_text("spec\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(new_repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(new_repo), "commit", "-qm", "spec"],
        check=True,
        capture_output=True,
    )
    assert record_protected_tests(ctx, base) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_protected_recording.py`
Expected: FAIL with ImportError (`record_protected_tests` not defined).

- [ ] **Step 3: Implement**

Add to `workflow.py` (after `_require_regular_or_missing_control`):

```python
def record_protected_tests(
    ctx: WorkflowContext, test_base: str, *, append: bool = False
) -> list[str]:
    """Detect test files changed since test_base and write both controls.

    append=True keeps already-protected paths (phased mode grows the list one
    phase at a time); append=False replaces the list (single-shot stage 4).
    """
    from .gitops import git_out, head_sha

    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    changed = git_out(["diff", "--name-only", test_base, "HEAD"], ctx.workspace)
    root = Path(git_out(["rev-parse", "--show-toplevel"], ctx.workspace))
    try:
        spec_prefix = ctx.spec_dir.relative_to(root).as_posix().rstrip("/") + "/"
    except ValueError:
        spec_prefix = ""
    names = [
        name
        for name in changed.splitlines()
        if name and (not spec_prefix or not name.startswith(spec_prefix))
    ]
    _require_regular_or_missing_control(protected_list)
    _require_regular_or_missing_control(protected_base)
    existing: list[str] = []
    if append and protected_list.is_file():
        existing = [
            line
            for line in protected_list.read_text(encoding="utf-8").splitlines()
            if line
        ]
    merged = existing + [name for name in names if name not in existing]
    protected_list.write_text(
        "".join(name + "\n" for name in merged), encoding="utf-8"
    )
    protected_base.write_text(head_sha(ctx.workspace) + "\n", encoding="utf-8")
    ctx.archive.archive_snapshot(
        protected_list,
        "protected-tests.txt",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_snapshot(
        protected_base,
        "protected-base.sha",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )
    if merged:
        ctx.log(
            "Protected acceptance test files:\n"
            + "\n".join(f"  - {name}" for name in merged)
        )
    else:
        ctx.echo_err(
            "(warning: no acceptance-test paths were recorded; protected "
            "control files remain active)"
        )
    return names
```

In `run_workflow`, replace the inline block inside the
`write-acceptance-tests` stage — everything from `changed = git_out(` down to
the `else:` branch that prints the warning (workflow.py:756-800) — with:

```python
        record_protected_tests(ctx, test_base)
```

(The `commit_work(ctx, ctx.spec_roles.reviewer_agent, "Acceptance tests")`
line above it stays; the `end_stage(ctx)` below it stays.)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass — the extraction is behavior-preserving for PHASES=0
(existing integration tests cover the replace path end to end).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py tests/test_protected_recording.py
git commit -m "refactor(workflow): extract protected-test recording with an append mode" -m "Move the stage-4 inline logic (diff since the test base, spec-dir exclusion, control-file writes, archive snapshots, logging) into record_protected_tests. Replace mode is byte-identical to the old behavior; the new append mode merges new names after the already-protected ones without duplicates, ready for the phased flow that grows the list one phase at a time."
```

---

### Task 6: Phased plan structure check in the plan stage

**Files:**
- Create: `src/adversarial_ai_coding/phaseflow.py`
- Modify: `src/adversarial_ai_coding/workflow.py` (plan stage only)
- Test: `tests/test_phaseflow.py` (new)

**Interfaces:**
- Consumes: `parse_phases` / `PhasePlanError` (Task 2), template
  `phased-plan-invalid` (Task 4), `workflow.work`, `render_prompt`.
- Produces: `phased_plan_structure_check(ctx: WorkflowContext, plan_file: Path) -> None`.
  `phaseflow` calls every workflow primitive through the module object
  (`wf.work(...)`) so tests can keep monkeypatching `workflow` attributes.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phaseflow.py`:

```python
"""Phased stage flow: structure check, red check, and the phase loop."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.phaseflow import phased_plan_structure_check

VALID_PLAN = """# Plan

## Phase 1: feature works
Acceptance: the CLI prints the result.
- [ ] add the flag
"""


def test_structure_check_passes_valid_plan(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text(VALID_PLAN, encoding="utf-8")
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("valid plan: no repair call")
    )
    phased_plan_structure_check(ctx, plan)


def test_structure_check_repairs_then_passes(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text("# Plan\n\n- [ ] stray task\n", encoding="utf-8")
    prompts = []

    def repair(ctx_arg, agent, prompt):
        prompts.append((agent, prompt))
        plan.write_text(VALID_PLAN, encoding="utf-8")

    monkeypatch.setattr(wf_mod, "work", repair)
    phased_plan_structure_check(ctx, plan)
    assert len(prompts) == 1
    agent, prompt = prompts[0]
    assert agent == ctx.spec_roles.owner_agent
    assert "stray task" in prompt


def test_structure_check_exhaustion_aborts_and_notifies(
    make_ctx, new_repo, monkeypatch
):
    ctx = make_ctx()
    plan = new_repo / "plan.md"
    plan.write_text("prose without phases\n", encoding="utf-8")
    monkeypatch.setattr(wf_mod, "work", lambda *args: None)
    notices = []
    monkeypatch.setattr(ctx, "notify", notices.append)
    with pytest.raises(WorkflowAbort, match="invalid phased structure"):
        phased_plan_structure_check(ctx, plan)
    assert notices
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_phaseflow.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'adversarial_ai_coding.phaseflow'`.

- [ ] **Step 3: Implement**

Create `src/adversarial_ai_coding/phaseflow.py`:

```python
"""Phased ATDD stage flow (PHASES=1).

Like dual_spec.py, this module drives workflow.py primitives; every call
goes through the module object (wf.work, wf.review_loop_ref, ...) so the
existing monkeypatch seams on workflow keep working.
"""

from __future__ import annotations

from pathlib import Path

from . import workflow as wf
from .config import WorkflowAbort
from .phases import PhasePlanError, parse_phases
from .prompts import render_prompt


def phased_plan_structure_check(ctx: wf.WorkflowContext, plan_file: Path) -> None:
    """Deterministic plan-format gate: parse, send repairs to the owner, abort."""

    attempt = 1
    while True:
        try:
            parse_phases(plan_file)
        except PhasePlanError as exc:
            ctx.log(f"Phased plan structure check failed (attempt {attempt})")
            if attempt >= ctx.settings.max_rounds:
                ctx.notify(
                    f"adversarial-ai-coding:[{ctx.cur_stage}] phased plan "
                    "structure check failed repeatedly; human intervention "
                    "required"
                )
                raise WorkflowAbort(
                    f"!! [{ctx.cur_stage}] plan.md still has an invalid phased "
                    f"structure after {ctx.settings.max_rounds} attempts; "
                    f"stopping for human intervention.\n{exc}"
                )
            attempt += 1
            prompt = render_prompt(
                ctx.prompts_dir,
                "phased-plan-invalid",
                {"PLAN_FILE": str(plan_file), "PROBLEMS": str(exc)},
            )
            wf.work(ctx, ctx.spec_roles.owner_agent, prompt)
            continue
        ctx.log("Phased plan structure check passed")
        return
```

In `workflow.py`, inside the `write-implementation-plan` stage, select the
templates and insert the check. Replace:

```python
        work(
            ctx,
            ctx.spec_roles.owner_agent,
            render_prompt(
                ctx.prompts_dir,
                "write-implementation-plan",
                {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)},
            ),
        )
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-plan",
            {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)},
        )
```

with:

```python
        plan_template = (
            "write-implementation-plan-phased"
            if ctx.settings.phases
            else "write-implementation-plan"
        )
        plan_scope_template = (
            "review-scope-plan-phased"
            if ctx.settings.phases
            else "review-scope-plan"
        )
        work(
            ctx,
            ctx.spec_roles.owner_agent,
            render_prompt(
                ctx.prompts_dir,
                plan_template,
                {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)},
            ),
        )
        scope = render_prompt(
            ctx.prompts_dir,
            plan_scope_template,
            {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)},
        )
```

and between `human_gate_plan(ctx)` and
`commit_work(ctx, ctx.spec_roles.owner_agent, "Implementation plan")`:

```python
        if ctx.settings.phases:
            from .phaseflow import phased_plan_structure_check

            phased_plan_structure_check(ctx, plan_file)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_phaseflow.py` then `uv run pytest -q`.
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/phaseflow.py src/adversarial_ai_coding/workflow.py tests/test_phaseflow.py
git commit -m "feat(workflow): validate phased plan structure before commit" -m "PHASES=1 selects the phased plan templates and, after the review loop and optional human gate, runs a deterministic parse of plan.md. Parse problems go back to the owner with a repair prompt up to MAX_ROUNDS, then the run aborts resumable. This catches an unusable plan format before any acceptance test or implementation cost is paid."
```

---

### Task 7: The phased stage loop (`run_phased_stages` + `red_check`)

**Files:**
- Modify: `src/adversarial_ai_coding/phaseflow.py`
- Modify: `src/adversarial_ai_coding/workflow.py` (`run_workflow` branch)
- Test: `tests/test_phaseflow.py`

**Interfaces:**
- Consumes: everything produced by Tasks 1-6; `gates.run_shell`;
  `agents.impl_ref/agent_model/resolve_model_args/is_builtin_agent`;
  runstate phase helpers.
- Produces:
  - `red_check(ctx, phase: Phase, cmd: str) -> None` — normal phase requires
    non-zero exit, regression-guard phase requires zero; wrong result sends
    `phase-red-check-failed` to the test author (the spec reviewer slot) up
    to MAX_ROUNDS, then aborts. Empty `cmd` warns and skips.
  - `run_phased_stages(ctx, spec_file: Path, plan_file: Path) -> None` —
    stages `phase-NN-write-tests` / `phase-NN-implement` per phase.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phaseflow.py`:

```python
from adversarial_ai_coding import gates
from adversarial_ai_coding import phaseflow
from adversarial_ai_coding.phases import Phase
from adversarial_ai_coding.runstate import RunState

NORMAL = Phase(number=1, title="feature works", regression_guard=False, tasks=("t",))
GUARD = Phase(number=2, title="unchanged", regression_guard=True, tasks=("t",))


def test_red_check_passes_when_normal_phase_is_red(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (1, "red"))
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("no repair expected")
    )
    phaseflow.red_check(ctx, NORMAL, "gate")


def test_red_check_passes_when_guard_phase_is_green(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (0, "green"))
    monkeypatch.setattr(
        wf_mod, "work", lambda *args: pytest.fail("no repair expected")
    )
    phaseflow.red_check(ctx, GUARD, "gate")


def test_red_check_repairs_with_test_author_then_passes(make_ctx, monkeypatch):
    ctx = make_ctx()
    results = iter([(0, "green"), (1, "red")])
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(results))
    repairs = []
    monkeypatch.setattr(
        wf_mod, "work", lambda ctx_arg, agent, prompt: repairs.append((agent, prompt))
    )
    phaseflow.red_check(ctx, NORMAL, "gate")
    assert len(repairs) == 1
    agent, prompt = repairs[0]
    assert agent == ctx.spec_roles.reviewer_agent
    assert "must FAIL" in prompt


def test_red_check_exhaustion_aborts(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (0, "green"))
    monkeypatch.setattr(wf_mod, "work", lambda *args: None)
    notices = []
    monkeypatch.setattr(ctx, "notify", notices.append)
    with pytest.raises(WorkflowAbort, match="red check failed"):
        phaseflow.red_check(ctx, NORMAL, "gate")
    assert notices


def test_red_check_skips_without_command(make_ctx, monkeypatch):
    ctx = make_ctx()
    warnings = []
    ctx.echo_err = warnings.append
    monkeypatch.setattr(
        gates, "run_shell", lambda cmd, cwd: pytest.fail("must not run")
    )
    phaseflow.red_check(ctx, NORMAL, "")
    assert any("red check is skipped" in line for line in warnings)


PHASED_PLAN = """# Plan

## Phase 1: feature works
Acceptance: src.txt exists.
- [ ] task one
- [ ] task two

## Phase 2: old behavior unchanged (regression-guard)
Acceptance: base.txt unchanged.
- [ ] task three
"""


def test_run_phased_stages_drives_phases_in_order(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx(
        {"PHASES": "1", "IMPL_MODEL": "impl-model", "RETRY_ON_LIMIT": "0"}
    )
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.gate_cmd = "full-gate"
    ctx.build_gate_cmd = "build-gate"
    ctx.phase_gate_cmd = "phase-gate"
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.spec_dir / "plan.md"
    plan.write_text(PHASED_PLAN, encoding="utf-8")
    spec = ctx.spec_dir / "spec.md"
    spec.write_text("spec\n", encoding="utf-8")

    events = []
    monkeypatch.setattr(
        wf_mod,
        "work",
        lambda ctx_arg, agent, prompt: events.append(
            ("work", agent.slot, prompt.splitlines()[0])
        ),
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_work",
        lambda ctx_arg, agent, description: events.append(
            ("commit", agent.slot, description)
        ),
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_if_dirty",
        lambda ctx_arg, agent, description: events.append(
            ("dirty", agent.slot, description)
        ),
    )
    monkeypatch.setattr(
        wf_mod, "gate_loop_ref", lambda cmd, **kwargs: events.append(("gate", cmd))
    )
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": events.append(
            ("review", reviewer.slot, worker.slot)
        ),
    )
    red_results = iter([(1, "red"), (0, "green")])
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(red_results))

    phaseflow.run_phased_stages(ctx, spec, plan)

    assert ctx.state.completed_stages() == [
        "phase-01-write-tests",
        "phase-01-implement",
        "phase-02-write-tests",
        "phase-02-implement",
    ]
    test_writers = [
        event[1]
        for event in events
        if event[0] == "work"
        and event[2].startswith("Write acceptance tests for exactly one")
    ]
    assert test_writers == ["B", "B"]
    assert [event for event in events if event[0] == "review"] == [
        ("review", "A", "B"),
        ("review", "A", "B"),
    ]
    assert [event for event in events if event[0] == "gate"] == [
        ("gate", "build-gate"),
        ("gate", "build-gate"),
        ("gate", "phase-gate"),
        ("gate", "build-gate"),
        ("gate", "phase-gate"),
    ]
    implementers = [
        event[1]
        for event in events
        if event[0] == "work" and event[2].startswith("Implement this task")
    ]
    assert implementers == ["I", "I", "I"]
    task_commits = [
        event[2]
        for event in events
        if event[0] == "commit" and event[2].startswith('Task "')
    ]
    assert task_commits == ['Task "task one"', 'Task "task two"', 'Task "task three"']
    assert ctx.protected_controls is not None
    plan_text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in plan_text and plan_text.count("- [x]") == 3


def test_phase_review_adds_reviewer_loop_over_impl(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx(
        {
            "PHASES": "1",
            "PHASE_REVIEW": "1",
            "IMPL_MODEL": "impl-model",
            "RETRY_ON_LIMIT": "0",
        }
    )
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.phase_gate_cmd = "phase-gate"
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    plan = ctx.spec_dir / "plan.md"
    plan.write_text(
        "## Phase 1: feature works\nAcceptance: x.\n- [ ] task one\n",
        encoding="utf-8",
    )
    spec = ctx.spec_dir / "spec.md"
    spec.write_text("spec\n", encoding="utf-8")

    events = []
    monkeypatch.setattr(wf_mod, "work", lambda ctx_arg, agent, prompt: None)
    monkeypatch.setattr(
        wf_mod, "commit_work", lambda ctx_arg, agent, description: None
    )
    monkeypatch.setattr(
        wf_mod,
        "commit_if_dirty",
        lambda ctx_arg, agent, description: events.append(
            ("dirty", agent.slot, description)
        ),
    )
    monkeypatch.setattr(wf_mod, "gate_loop_ref", lambda cmd, **kwargs: None)
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": events.append(
            ("review", reviewer.slot, worker.slot)
        ),
    )
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: (1, "red"))

    phaseflow.run_phased_stages(ctx, spec, plan)

    assert ("review", "B", "I") in events
    assert ("dirty", "I", "Phase 1 review fixes") in events
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest -q tests/test_phaseflow.py`
Expected: FAIL with `AttributeError: module ... has no attribute 'red_check'`.

- [ ] **Step 3: Implement phaseflow**

Append to `src/adversarial_ai_coding/phaseflow.py` (extend the imports at the
top of the file accordingly):

```python
from .agents import (
    agent_model,
    impl_ref,
    is_builtin_agent,
    resolve_model_args,
)
from .phases import Phase


def red_check(ctx: wf.WorkflowContext, phase: Phase, cmd: str) -> None:
    """TDD-red gate run by the workflow, never trusted from AI output."""

    from .gates import run_shell

    if not cmd:
        ctx.echo_err(
            "(warning: no phase gate command; the red check is skipped. Set "
            "PHASE_GATE_CMD or GATE_CMD to enable it.)"
        )
        return
    attempt = 1
    while True:
        ctx.log(f">>> Phase red check:{cmd}")
        rc, output = run_shell(cmd, ctx.workspace)
        if phase.regression_guard:
            ok = rc == 0
            expected = (
                'this phase is marked "(regression-guard)", so its tests must '
                "PASS against current behavior, but the command failed"
            )
        else:
            ok = rc != 0
            expected = (
                "the new phase tests must FAIL (red) because the phase is not "
                "implemented yet, but the command passed. Tests that pass "
                "before the implementation exists prove nothing"
            )
        if ok:
            ctx.log("Phase red check passed")
            return
        ctx.log(f"Phase red check failed (attempt {attempt})")
        if attempt >= ctx.settings.max_rounds:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] phase red check "
                "failed repeatedly; human intervention required"
            )
            raise WorkflowAbort(
                f"!! [{ctx.cur_stage}] Phase red check failed "
                f"{ctx.settings.max_rounds} times; stopping for human "
                "intervention. Output:\n"
                + "\n".join(output.splitlines()[-50:])
            )
        attempt += 1
        prompt = render_prompt(
            ctx.prompts_dir,
            "phase-red-check-failed",
            {
                "COMMAND": cmd,
                "EXPECTED": expected,
                "PHASE_TITLE": phase.title,
                "OUTPUT": "\n".join(output.splitlines()[-150:]),
            },
        )
        wf.work(ctx, ctx.spec_roles.reviewer_agent, prompt)


def run_phased_stages(
    ctx: wf.WorkflowContext, spec_file: Path, plan_file: Path
) -> None:
    from .gitops import head_sha
    from .runstate import (
        ensure_named_task_queue,
        ensure_phases,
        mark_plan_task_done,
        phase_queue_name,
        pop_task_queue,
        remaining_tasks,
        restore_or_record_base,
    )

    if ctx.state is None:
        raise WorkflowAbort("!! PHASES=1 requires claimed run state.")
    phases = ensure_phases(ctx.state, plan_file)
    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    phase_gate = ctx.phase_gate_cmd or ctx.gate_cmd
    impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)
    ctx.log(
        "Resolved implementation: "
        f"agent={impl.name} model={agent_model(impl, ctx.settings)} "
        f"args={resolve_model_args(impl, ctx.settings)}"
    )
    if ctx.settings.impl_model and not is_builtin_agent(impl.name):
        ctx.log(
            "warning: IMPL_MODEL is ignored for custom implementation "
            f"agent {impl.name}"
        )
    done_titles: list[str] = []
    for phase in phases:
        label = f"phase-{phase.number:02d}"
        base_name = f"{label}-test-base"
        if wf.begin_stage(ctx, f"{label}-write-tests", protected_list, protected_base):
            test_base = restore_or_record_base(
                ctx.state, base_name, lambda: head_sha(ctx.workspace)
            )
            wf.work(
                ctx,
                ctx.spec_roles.reviewer_agent,
                render_prompt(
                    ctx.prompts_dir,
                    "write-phase-tests",
                    {
                        "SPEC_FILE": str(spec_file),
                        "PLAN_FILE": str(plan_file),
                        "SPEC_DIR": str(ctx.spec_dir),
                        "PHASE_TITLE": phase.title,
                        "PHASES_DONE": ", ".join(done_titles) or "none",
                        "PROTECTED_TESTS_FILE": str(protected_list),
                    },
                ),
            )
            scope = render_prompt(
                ctx.prompts_dir,
                "review-scope-acceptance-tests",
                {"TEST_BASE": test_base, "SPEC_FILE": str(spec_file)},
            )
            wf.review_loop_ref(
                ctx,
                ctx.spec_roles.owner_agent,
                ctx.spec_roles.reviewer_agent,
                scope,
            )
            red_check(ctx, phase, phase_gate)
            wf.commit_work(
                ctx,
                ctx.spec_roles.reviewer_agent,
                f"Phase {phase.number} acceptance tests",
            )
            wf.record_protected_tests(ctx, test_base, append=True)
            wf.end_stage(ctx)
        wf._activate_protected_controls(ctx)
        if wf.begin_stage(ctx, f"{label}-implement"):
            queue = phase_queue_name(phase.number)
            ensure_named_task_queue(ctx.state, queue, list(phase.tasks))
            total = len(phase.tasks)
            while remaining_tasks(ctx.state, queue):
                task_line = remaining_tasks(ctx.state, queue)[0]
                index = total - len(remaining_tasks(ctx.state, queue)) + 1
                ctx.log(
                    f"--- Phase {phase.number} task {index}/{total}:{task_line} ---"
                )
                wf.work(
                    ctx,
                    impl,
                    render_prompt(
                        ctx.prompts_dir,
                        "implement-plan-task",
                        {
                            "PLAN_FILE": str(plan_file),
                            "TASK": task_line,
                            "PROTECTED_TESTS_FILE": str(protected_list),
                        },
                    ),
                )
                wf.gate_loop_ref(
                    ctx.build_gate_cmd,
                    cwd=ctx.workspace,
                    prompts_dir=ctx.prompts_dir,
                    max_rounds=ctx.settings.max_rounds,
                    do_work=lambda prompt: wf.work(ctx, impl, prompt),
                    log=ctx.log,
                    notify=ctx.notify,
                    stage=ctx.cur_stage,
                )
                wf.commit_work(ctx, impl, f'Task "{task_line}"')
                pop_task_queue(ctx.state, queue)
                mark_plan_task_done(plan_file, task_line)
            ctx.log(
                f"--- Phase {phase.number} tasks complete; running the phase "
                "gate. All tests written so far must pass. ---"
            )
            wf.gate_loop_ref(
                phase_gate,
                cwd=ctx.workspace,
                prompts_dir=ctx.prompts_dir,
                max_rounds=ctx.settings.max_rounds,
                do_work=lambda prompt: wf.work(ctx, impl, prompt),
                log=ctx.log,
                notify=ctx.notify,
                stage=ctx.cur_stage,
            )
            if ctx.settings.phase_review:
                phase_base = restore_or_record_base(
                    ctx.state, base_name, lambda: head_sha(ctx.workspace)
                )
                scope = render_prompt(
                    ctx.prompts_dir,
                    "review-scope-phase",
                    {
                        "PHASE_TITLE": phase.title,
                        "PHASE_BASE": phase_base,
                        "PLAN_FILE": str(plan_file),
                    },
                )
                wf.review_loop_ref(ctx, ctx.spec_roles.reviewer_agent, impl, scope)
                wf.commit_if_dirty(
                    ctx, impl, f"Phase {phase.number} review fixes"
                )
            wf.end_stage(ctx)
        done_titles.append(phase.title)
```

- [ ] **Step 4: Branch `run_workflow`**

In `workflow.py`, wrap the `write-acceptance-tests` stage and the
`_activate_protected_controls(ctx)` call (currently unconditional) in the
`PHASES=0` branch, and route `PHASES=1` to the phase loop. The result:

```python
    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    if ctx.settings.phases:
        from .phaseflow import run_phased_stages

        run_phased_stages(ctx, spec_file, plan_file)
    else:
        if begin_stage(ctx, "write-acceptance-tests", protected_list, protected_base):
            ... existing body unchanged ...
            end_stage(ctx)

        _activate_protected_controls(ctx)
```

Then gate the task-loop half of the `write-code` stage:

```python
    if begin_stage(ctx, "write-code"):
        if not ctx.settings.phases:
            impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)
            ... existing "Resolved implementation" log, IMPL_MODEL warning,
                and the `if ctx.state is not None:` task loop, unchanged ...
        ctx.log(
            "--- All tasks complete; running full quality gate. Acceptance "
            "tests must pass. ---"
        )
        ... rest of the stage unchanged (full gate, branch review,
            commit_if_dirty, end_stage) ...
```

Only indentation and the two `if` lines change; no line inside the moved
blocks is edited.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest -q tests/test_phaseflow.py` then `uv run pytest -q`.
Expected: all pass; `test_stageflow.py` proves the PHASES=0 write-code
restructure is behavior-preserving.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/phaseflow.py src/adversarial_ai_coding/workflow.py tests/test_phaseflow.py
git commit -m "feat(workflow): run the phased write-tests and implement stages" -m "PHASES=1 replaces the single acceptance-test stage and the write-code task loop with a per-phase loop: B writes one phase's tests, A reviews them, the workflow runs the deterministic red check (non-zero exit required, inverted for regression-guard phases, repairs to the test author), the tests are committed and appended to the protected controls, then the implementation slot runs the per-task loop and the phase gate (PHASE_GATE_CMD or GATE_CMD). PHASE_REVIEW=1 adds a reviewer loop over the phase diff with fixes by the implementation slot. Stage names carry the phase number so the resume ledger and metrics work unchanged; the write-code stage keeps the full gate and branch review for both modes."
```

---

### Task 8: Fake agent support and offline phased integration tests

**Files:**
- Modify: `tests/fake_agent.py`
- Create: `tests/test_phased_integration.py`

**Interfaces:**
- Consumes: helpers from `tests/test_resume_integration.py` (`wf_env`,
  `run_cli`, `state_dir_of`, `calls`, `implementation_tasks`,
  `driver_workdir`) — importable because the tests directory is on sys.path
  during pytest runs.
- Produces: fake-agent kinds `write-phase-tests` (writes
  `acc/<slugged-title>.txt`) and a phased `write-plan` variant.

- [ ] **Step 1: Extend the fake agent**

In `tests/fake_agent.py`, in `classify`, insert BEFORE the existing
`if prompt.startswith("Write acceptance tests")` line (order matters):

```python
    if prompt.startswith("Write acceptance tests for exactly one phase"):
        return "write-phase-tests"
```

In `main`, replace the `write-plan` handler body with:

```python
    elif kind == "write-plan":
        target = grep_target(r"specs[\\/][^ \r\n]+[\\/]plan\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        if '"## Phase N: <title>"' in prompt:
            Path(target).write_text(
                "# Plan\n\n"
                "## Phase 1: feature works\n"
                "Acceptance: src.txt records the implementation.\n"
                "- [ ] add feature one\n"
                "- [ ] add feature two\n\n"
                "## Phase 2: old behavior unchanged (regression-guard)\n"
                "Acceptance: base.txt still says base.\n"
                "- [ ] add regression fixture\n",
                encoding="utf-8",
            )
        else:
            Path(target).write_text(
                "# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n",
                encoding="utf-8",
            )
```

Add a handler after the `write-acceptance` branch:

```python
    elif kind == "write-phase-tests":
        Path("acc").mkdir(exist_ok=True)
        title_match = re.search(r'one phase of the plan: "(.+?)"', prompt)
        title = title_match.group(1) if title_match else "phase"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        Path(f"acc/{slug}.txt").write_text("PHASE CHECK\n", encoding="utf-8")
```

- [ ] **Step 2: Write the integration tests**

Create `tests/test_phased_integration.py`:

```python
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
```

- [ ] **Step 3: Run the tests**

Run: `uv run pytest -q tests/test_phased_integration.py -v`
Expected: all pass. Reviewer-review counts: PHASES baseline is 4 (spec,
plan, branch, final acceptance); PHASE_REVIEW=1 adds one per phase = 6. If a
count differs, print `(work / "calls.log")` content and fix the flow, not
the number.

Then: `uv run pytest -q` (full suite).

- [ ] **Step 4: Commit**

```bash
git add tests/fake_agent.py tests/test_phased_integration.py
git commit -m "test(workflow): cover phased runs end to end with fake agents" -m "The fake agent learns the phased plan format and a write-phase-tests kind that creates one acceptance file per phase title. Offline scenarios cover: a full PHASES=1 run with per-phase protected-list growth and task routing, quota abort inside phase 2 with resume that never re-runs phase 1 stages, the PHASES immutability refusal on resume, and PHASE_REVIEW=1 adding exactly one reviewer loop per phase. The phase gate uses a marker-file command so the red check is genuinely red before implementation and green after."
```

---

### Task 9: Documentation (README en/zh-TW, AGENTS template)

**Files:**
- Modify: `README.md`
- Modify: `README.zh-TW.md`
- Modify: `resources/AGENTS.template.md`
- Test: `tests/test_documentation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_documentation.py`:

```python
def test_phased_mode_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "PHASES" in readme
        assert "PHASE_GATE_CMD" in readme
        assert "PHASE_REVIEW" in readme
        assert "regression-guard" in readme
    assert "regression-guard" in _read("resources/AGENTS.template.md")
```

Run: `uv run pytest -q tests/test_documentation.py` — expected FAIL.

- [ ] **Step 2: Update README.md**

1. Configuration table — add after the `DUAL_SPEC` row:

```markdown
| `PHASES` | `0` | `1` enables the phased ATDD flow: the plan is split into vertical phases, and each phase writes its own protected acceptance tests before its tasks are implemented. Decides the stage graph, so it cannot change across resume. |
| `PHASE_GATE_CMD` | empty | Gate command for the per-phase red check and phase gate. Empty falls back to `GATE_CMD`. |
| `PHASE_REVIEW` | `0` | `1` adds a reviewer pass over each phase diff, with blocker loops. Off by default because the phase gate already enforces the reviewer's protected tests. |
```

2. Mermaid pipeline diagram — after the line
`tests -. "run by the full gate" .-> branch` add:

```text
    phased["<b>4-5 · Phased loop (PHASES=1)</b><br/>per phase: B writes tests · A reviews ⟳<br/>red check · I implements tasks · phase gate"]
    plangate -. "y · PHASES=1" .-> phased
    phased -.-> branch
```

3. New section, inserted after the "Dual Spec Mode" section:

```markdown
## Phased ATDD Mode

Set `PHASES=1` to replace the single up-front acceptance-test stage with a
per-phase loop. The plan must use `## Phase N: <title>` headings; every
phase needs an `Acceptance:` line with observable behavior at a stable
boundary and at least one `- [ ]` task. Phases must be vertical functional
slices (a working behavior increment), never horizontal technical layers.
The workflow parses the plan deterministically after the plan review and
sends structure problems back to the owner before anything is implemented.

For each phase, in order:

1. B writes only this phase's acceptance tests; A reviews them.
2. The workflow runs the red check with `PHASE_GATE_CMD` (or `GATE_CMD`):
   the new tests must fail, because the phase is not implemented yet. A
   title ending in `(regression-guard)` inverts the expectation: those
   tests lock in existing behavior and must pass immediately.
3. The tests are committed and appended to the protected list; earlier
   phases' tests are never removed.
4. The implementation slot implements the phase's tasks (one commit per
   task, build gate per task), then the phase gate runs: every test
   written so far must pass. Completed phases stay green for the rest of
   the run.

Because tests are written just in time, "run everything" at a phase
boundary already means "all completed phases plus the current phase are
green" — no test tagging or per-phase selection is needed. After the last
phase, the normal full gate, branch review, and final review run
unchanged. `PHASES` cannot change across resume.
```

- [ ] **Step 3: Update README.zh-TW.md**

Mirror the three edits in Traditional Chinese, same locations. Configuration
table rows:

```markdown
| `PHASES` | `0` | `1` 啟用分階段 ATDD 流程:plan 拆成垂直 phase,每個 phase 先寫自己的受保護驗收測試再實作。此設定決定 stage 圖,resume 時不可變更。 |
| `PHASE_GATE_CMD` | 空 | 每個 phase 的 red check 與 phase gate 命令。空值時改用 `GATE_CMD`。 |
| `PHASE_REVIEW` | `0` | `1` 時每個 phase 結尾由 reviewer 審該 phase 的 diff(含 blocker 迴圈)。預設關閉,因為 phase gate 本身就是 reviewer 寫的受保護測試在把關。 |
```

Section title: `## 分階段 ATDD 模式(Phased ATDD)`. Translate the English
section faithfully; keep the terms `Acceptance:`, `(regression-guard)`,
`PHASE_GATE_CMD`, `GATE_CMD` in English.

- [ ] **Step 4: Update AGENTS.template.md**

Insert before the `## Commits` section:

```markdown
## Phased mode (PHASES=1)

- The plan splits into `## Phase N: <title>` sections. Every phase needs an
  `Acceptance:` line with observable behavior at a stable boundary and at
  least one `- [ ]` task. Phases are vertical functional slices, never
  technical layers.
- A title ending in `(regression-guard)` marks tests that must pass
  immediately; all other phase tests must be red before implementation.
- The default test level is the phase acceptance test at a stable boundary
  (component or contract level is fine). Add lower-level implementation
  tests only when a trigger holds: many input combinations or edge cases;
  parser, state machine, algorithm, or data-transformation logic;
  concurrency, timeout, retry, or cancellation behavior; failures that
  acceptance tests cannot localize or reproduce cheaply.
```

- [ ] **Step 5: Run tests and commit**

Run: `uv run pytest -q tests/test_documentation.py` then `uv run pytest -q`.
Expected: all pass.

```bash
git add README.md README.zh-TW.md resources/AGENTS.template.md tests/test_documentation.py
git commit -m "docs: document the phased ATDD mode" -m "Add PHASES, PHASE_GATE_CMD and PHASE_REVIEW to both README configuration tables, a Phased ATDD section explaining vertical phases, the red check, the regression-guard marker and append-only protection, a phased branch in the pipeline diagram, and AGENTS template rules covering the phased plan format and the trigger list for lower-level implementation tests."
```

---

### Task 10: Live E2E scenario (marker-gated)

**Files:**
- Modify: `tests/e2e/test_e2e.py`

**Interfaces:**
- Consumes: existing `make_fixture_repo`, `verify_gates`, `E2E_DEFAULTS`.

- [ ] **Step 1: Add the phased E2E test**

Append to `tests/e2e/test_e2e.py`:

```python
@pytest.mark.e2e
@needs_go
def test_full_workflow_phased_e2e():
    base = Path(os.environ.get("E2E_DIR") or tempfile.mkdtemp(prefix="wf-e2e-ph-"))
    base.mkdir(parents=True, exist_ok=True)
    print(f"== Phased E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    env = {key: os.environ.get(key, value) for key, value in E2E_DEFAULTS.items()}
    env["PHASES"] = "1"
    tool = shutil.which("adversarial-ai-coding")
    assert tool, "console script not installed; run `uv sync` first"
    proc = subprocess.run(
        [tool, "task.md"],
        cwd=repo,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    (base / "run.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    assert proc.returncode == 0, f"workflow rc={proc.returncode}; see {base}/run.log"
    log = (base / "run.log").read_text(encoding="utf-8")

    assert "All stages complete" in log
    assert "[phase-01-write-tests]" in log
    assert "[phase-01-implement]" in log
    assert "Phase red check passed" in log

    state_dirs = list((repo / ".workflow" / "state").iterdir())
    assert len(state_dirs) == 1
    ledger = json.loads(
        (state_dirs[0] / "ledger.json").read_text(encoding="utf-8")
    )
    stages = ledger["stages"]
    assert "phase-01-write-tests" in stages and "phase-01-implement" in stages
    assert "write-acceptance-tests" not in stages

    protected = repo / ".workflow" / "protected-tests.txt"
    assert protected.is_file() and protected.stat().st_size > 0
    plan = next((repo / "specs").glob("*/plan.md")).read_text(encoding="utf-8")
    assert "## Phase 1:" in plan
    assert "- [ ] " not in plan and "- [x]" in plan
    verify_gates(repo)
    print(f"Phased E2E passed; workspace kept at {base} (delete after inspection)")
```

- [ ] **Step 2: Verify the offline suite is unaffected**

Run: `uv run pytest -q`
Expected: all pass (the new test is `-m e2e` gated and skipped by default).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_e2e.py
git commit -m "test(e2e): add a marker-gated phased workflow scenario" -m "Run the real-agent workflow with PHASES=1 against the Go fixture and assert the phase stages appear in the resume ledger, the red check passed, the single acceptance-test stage did not run, the protected list is non-empty, the plan uses phase headings with all tasks checked, and the fixture gates stay green afterwards. Marker-gated like the existing E2E because it consumes real quota."
```

- [ ] **Step 4: Live run (human-triggered)**

The live E2E consumes real AI quota and MUST NOT be started from a sandboxed
shell (project rule). Ask the human to run, from a normal terminal at the
repo root:

```bash
uv run pytest -m e2e -s tests/e2e/test_e2e.py::test_full_workflow_phased_e2e
```

Report the result; do not mark this plan complete until it passes or the
human explicitly defers it.

---

## Plan Self-Review Notes

- Spec coverage: configuration (Task 1), plan format + structure check
  (Tasks 2, 4, 6), stage flow with red check and append-only protection
  (Tasks 5, 7), resume/state (Tasks 3, 8), implementation-test policy and
  docs (Task 9), live E2E (Task 10). The spec's "known limitation" (red
  check cannot tell new-red from broken-build) is accepted and documented
  in the red-check code comment path — no task needed.
- The `PHASES=0` path is regression-guarded by the existing suite plus the
  unchanged `test_resume_integration.py` scenarios.
- Type consistency: `Phase(number, title, regression_guard, tasks)` is used
  identically in Tasks 2, 3, 7; queue and base helpers keep default names
  for old callers; `record_protected_tests` returns new names only in both
  modes.
