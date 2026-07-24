# Phased ATDD Auto-Suggestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When `PHASES` is unset, the spec reviewer judges whether the task suits Phased ATDD; if it recommends it, the spec human gate asks the user whether to enable phased mode, and a yes flips `settings.phases` plus the resume snapshot atomically.

**Architecture:** A new `phased_suggestion.py` module owns the pure logic (arming check, side-file reset/read, all fail-open). The reviewer's judgment travels in `.workflow/phased-suggestion.json` — never in `verdict.json`, whose schema is frozen. Call sites append a prompt instruction block to the spec review scope only when armed; `run_review` pre-creates and archives the side file only when a per-context flag is set; `human_gate_spec` offers the flip after spec approval.

**Tech Stack:** Python 3 (uv-managed), pytest, plain-markdown prompt templates with `{{VAR}}` substitution.

**Spec:** `docs/superpowers/specs/2026-07-25-phased-atdd-auto-suggest-design.md`

## Global Constraints

- Run tests with `uv run pytest <file> -v` from the repo root. On this machine, clear the poisoned system env first in PowerShell: `$env:PYTHONHOME=""; $env:PYTHONPATH=""`.
- `verdict.json` keeps exactly `{"approved", "blockers", "suggestions"}` — never add fields to it.
- The side file is `.workflow/phased-suggestion.json`, one line of JSON: `{"phased": true|false, "reason": "one or two sentences"}`.
- Every suggestion-path failure (missing file, bad JSON, wrong types) means "no suggestion" and must never abort or fail a run.
- The workflow never flips `PHASES` automatically; `HUMAN_GATE=0` logs the recommendation only.
- Gate prompt string, exactly: `Enable Phased ATDD for this run? [y/N]:` — only `y`/`Y` enables.
- Repo docs and prompts are English; `README.zh-TW.md` / `docs/how-it-works.zh-TW.md` are Traditional Chinese.
- Commits: Conventional Commit format, detailed body, **no** `Co-Authored-By` trailer.
- The repo has unrelated untracked files (`flow-*.txt`, `docs/todos/*`). Always `git add` explicit paths; never `git add -A`.

---

### Task 1: `Settings.phases_explicit`

Records whether the launching environment explicitly set `PHASES` (any non-empty value). Arming logic later suppresses the suggestion for explicit `PHASES=0` users — it also avoids a resume trap where a flipped snapshot conflicts with a stale `PHASES=0` still exported in the user's shell.

**Files:**
- Modify: `src/adversarial_ai_coding/config.py` (Settings dataclass + `from_env`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.phases_explicit: bool` — `True` iff `env.get("PHASES")` is non-empty. Empty string behaves as unset, matching `persisted()` semantics. Never persisted to the snapshot.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_phases_explicit_tracks_environment_presence():
    from adversarial_ai_coding.config import Settings

    assert Settings.from_env({"PHASES": "0"}, run_id="t").phases_explicit
    assert Settings.from_env({"PHASES": "1"}, run_id="t").phases_explicit
    assert not Settings.from_env({}, run_id="t").phases_explicit
    # Empty string behaves as unset, matching persisted() semantics.
    assert not Settings.from_env({"PHASES": ""}, run_id="t").phases_explicit
    # A snapshot value is not "explicit": the user did not type it now.
    resumed = Settings.from_env({}, run_id="t", snapshot={"PHASES": "1"})
    assert resumed.phases is True
    assert resumed.phases_explicit is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_phases_explicit_tracks_environment_presence -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'phases_explicit'` (or TypeError on unexpected keyword)

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/config.py`, add the field right after `phases: bool` in the dataclass:

```python
    phases: bool
    phases_explicit: bool
    phase_review: bool
```

In `from_env`, right after the `phases=` line:

```python
            phases=persisted("PHASES", "0") == "1",
            # Explicit PHASES in the launching environment (empty string
            # behaves as unset, matching persisted()). Deliberately never
            # snapshotted: it describes this attempt's command line.
            phases_explicit=bool(env.get("PHASES")),
            phase_review=persisted("PHASE_REVIEW", "0") == "1",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all Settings construction goes through `from_env` keywords, so no other call site breaks)

- [ ] **Step 5: Full suite sanity check**

Run: `uv run pytest`
Expected: PASS (no existing code constructs `Settings(...)` positionally)

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/config.py tests/test_config.py
git commit -m "feat(config): track whether PHASES was set explicitly" -m "Settings.phases_explicit is true when the launching environment
contains a non-empty PHASES value. The phased ATDD suggestion at the
spec gate must respect an explicit PHASES=0 (the user already decided)
and must not arm itself in that case; suppressing it also avoids an
IMMUTABLE_KEYS conflict on resume if the shell still exports PHASES=0
after an in-run flip. The field is derived per attempt and is never
written to the resume snapshot."
```

---

### Task 2: `phased_suggestion.py` — arming check and side-file helpers

**Files:**
- Create: `src/adversarial_ai_coding/phased_suggestion.py`
- Test (create): `tests/test_phased_suggestion.py`

**Interfaces:**
- Consumes: `Settings.phases`, `Settings.phases_explicit`, `Settings.import_plan` (Task 1)
- Produces:
  - `SUGGESTION_NAME = "phased-suggestion.json"`, `DEFAULT_SUGGESTION = '{"phased": false, "reason": ""}\n'`
  - `suggestion_path(wf: Path) -> Path`
  - `suggestion_armed(settings: Settings) -> bool`
  - `reset_suggestion(wf: Path) -> None`
  - `read_suggestion(wf: Path) -> tuple[bool, str]` — `(phased, reason)`, fail-open to `(False, "")`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_phased_suggestion.py`:

```python
"""Arming and side-file logic for the spec-gate Phased ATDD suggestion."""

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.phased_suggestion import (
    DEFAULT_SUGGESTION,
    read_suggestion,
    reset_suggestion,
    suggestion_armed,
    suggestion_path,
)


def settings_for(env):
    return Settings.from_env({"RETRY_ON_LIMIT": "0", **env}, run_id="t")


def test_suggestion_armed_matrix():
    assert suggestion_armed(settings_for({}))
    # Phased already on: nothing to suggest.
    assert not suggestion_armed(settings_for({"PHASES": "1"}))
    # Explicit opt-out: respect the user's decision.
    assert not suggestion_armed(settings_for({"PHASES": "0"}))
    # Imported plan cannot retroactively become a phased plan.
    assert not suggestion_armed(
        settings_for({"IMPORT_SPEC": "s.md", "IMPORT_PLAN": "p.md"})
    )
    # Imported spec alone leaves the plan AI-written: still armed.
    assert suggestion_armed(settings_for({"IMPORT_SPEC": "s.md"}))
    # HUMAN_GATE=0 stays armed: the reviewer judges, the gate only logs.
    assert suggestion_armed(settings_for({"HUMAN_GATE": "0"}))


def test_reset_writes_the_default_no_suggestion(tmp_path):
    reset_suggestion(tmp_path)
    assert (
        suggestion_path(tmp_path).read_text(encoding="utf-8")
        == DEFAULT_SUGGESTION
    )
    assert read_suggestion(tmp_path) == (False, "")


def test_read_suggestion_fails_open(tmp_path):
    assert read_suggestion(tmp_path) == (False, "")  # missing file
    cases = [
        "not json at all",
        "[true]",
        '{"phased": "yes"}',
        '{"reason": "no phased key"}',
        '{"phased": false, "reason": "explicitly not a fit"}',
    ]
    for text in cases:
        suggestion_path(tmp_path).write_text(text, encoding="utf-8")
        assert read_suggestion(tmp_path) == (False, ""), text


def test_read_suggestion_accepts_a_recommendation(tmp_path):
    suggestion_path(tmp_path).write_text(
        '{"phased": true, "reason": "two independent features"}',
        encoding="utf-8",
    )
    assert read_suggestion(tmp_path) == (True, "two independent features")
    # A wrong-typed reason does not discard the recommendation itself.
    suggestion_path(tmp_path).write_text('{"phased": true, "reason": 5}', encoding="utf-8")
    assert read_suggestion(tmp_path) == (True, "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_phased_suggestion.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'adversarial_ai_coding.phased_suggestion'`

- [ ] **Step 3: Implement the module**

Create `src/adversarial_ai_coding/phased_suggestion.py`:

```python
"""Phased ATDD suggestion at the spec human gate.

The spec reviewer judges phased fitness as a side output of its normal
review; the judgment travels in .workflow/phased-suggestion.json and
never touches verdict.json. Everything here fails open to "no
suggestion": this mechanism must never block or fail a run.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import Settings

SUGGESTION_NAME = "phased-suggestion.json"
DEFAULT_SUGGESTION = '{"phased": false, "reason": ""}\n'


def suggestion_path(wf: Path) -> Path:
    return wf / SUGGESTION_NAME


def suggestion_armed(settings: Settings) -> bool:
    """True when the spec review should also judge phased fitness.

    Explicit PHASES in the environment means the user already decided;
    an imported plan cannot retroactively become a phased plan. HUMAN_GATE
    does not gate arming: with the gate off the recommendation is logged.
    """

    return (
        not settings.phases
        and not settings.phases_explicit
        and not settings.import_plan
    )


def reset_suggestion(wf: Path) -> None:
    suggestion_path(wf).write_text(DEFAULT_SUGGESTION, encoding="utf-8")


def read_suggestion(wf: Path) -> tuple[bool, str]:
    """(phased, reason); anything unreadable or malformed is (False, "")."""

    try:
        payload = json.loads(suggestion_path(wf).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return (False, "")
    if not isinstance(payload, dict) or payload.get("phased") is not True:
        return (False, "")
    reason = payload.get("reason")
    return (True, reason if isinstance(reason, str) else "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_phased_suggestion.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/phased_suggestion.py tests/test_phased_suggestion.py
git commit -m "feat: add phased suggestion arming and side-file helpers" -m "New phased_suggestion module owns the pure logic for the spec-gate
Phased ATDD suggestion: suggestion_armed (phases off, no explicit
PHASES in the environment, no imported plan), reset_suggestion (write
the default no-suggestion line), and read_suggestion (parse the
reviewer's judgment). read_suggestion fails open to (False, \"\") on any
missing, unreadable, or malformed file so the suggestion path can
never block a run. The side file is separate from verdict.json by
design; the verdict schema stays frozen."
```

---

### Task 3: Prompt instruction block + `run_review` reset/archive wiring

The reviewer only judges when instructed; the instruction is a prompt template appended to the spec review scope by later tasks. `run_review` pre-creates the side file before each armed round (so a reviewer that ignores the instruction yields "no suggestion", not a stale value) and archives each round's file, following the verdict conventions.

**Files:**
- Create: `resources/prompts/phased-suggestion-instruction.md`
- Modify: `src/adversarial_ai_coding/workflow.py` (WorkflowContext: one new field)
- Modify: `src/adversarial_ai_coding/review.py` (`run_review`)
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `reset_suggestion`, `suggestion_path`, `DEFAULT_SUGGESTION` (Task 2)
- Produces:
  - `WorkflowContext.phased_suggestion_active: bool = False` — call sites set it around the spec review loop; `run_review` acts only when it is `True`.
  - Prompt template `phased-suggestion-instruction` with `{{WF}}` placeholder.
  - Archive artifact `phased-suggestion-<stage-slug>-r<N>.json` per armed round.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py` (module already imports `json`, `review_mod`, `AgentResult`, `run_review`, and defines `PROMPTS` and the `make_ctx` fixture usage):

```python
def test_phased_suggestion_instruction_renders_wf_path():
    from adversarial_ai_coding.prompts import render_prompt

    text = render_prompt(
        PROMPTS, "phased-suggestion-instruction", {"WF": "X:/wf"}
    )
    assert "X:/wf/phased-suggestion.json" in text
    assert '{"phased": true|false' in text
    assert "must not influence" in text


def test_run_review_resets_and_archives_phased_suggestion(make_ctx, monkeypatch):
    from adversarial_ai_coding.phased_suggestion import suggestion_path

    ctx = make_ctx()
    ctx.cur_stage = "write-spec"
    ctx.phased_suggestion_active = True
    suggestion_path(ctx.wf).write_text(
        '{"phased": true, "reason": "stale from an earlier round"}',
        encoding="utf-8",
    )
    seen = {}

    def reviewer(name, prompt, settings, session, io):
        seen["suggestion"] = suggestion_path(ctx.wf).read_text(encoding="utf-8")
        suggestion_path(ctx.wf).write_text(
            '{"phased": true, "reason": "fits"}', encoding="utf-8"
        )
        ctx.review_path.write_text("approved\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
        )
        io.agent_out.write_text("reviewed\n", encoding="utf-8")
        return AgentResult(0, "ok")

    monkeypatch.setattr(review_mod, "run_reviewer", reviewer)
    assert run_review(ctx, ctx.ref("B"), "scope") is True
    # The stale value was replaced with the default before the reviewer ran.
    assert json.loads(seen["suggestion"]) == {"phased": False, "reason": ""}
    archived = list(
        ctx.archive.run_dir.glob("*phased-suggestion-write-spec-r1.json")
    )
    assert archived
    assert "fits" in archived[0].read_text(encoding="utf-8")


def test_run_review_ignores_suggestion_when_inactive(make_ctx, monkeypatch):
    from adversarial_ai_coding.phased_suggestion import suggestion_path

    ctx = make_ctx()
    ctx.cur_stage = "write-code"
    stale = '{"phased": true, "reason": "from the spec stage"}'
    suggestion_path(ctx.wf).write_text(stale, encoding="utf-8")
    monkeypatch.setattr(review_mod, "run_reviewer", approving_reviewer())
    run_review(ctx, ctx.ref("B"), "scope")
    # Untouched and unarchived: only the spec review manages this file.
    assert suggestion_path(ctx.wf).read_text(encoding="utf-8") == stale
    assert not list(ctx.archive.run_dir.glob("*phased-suggestion-*"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review.py -v -k phased_suggestion`
Expected: FAIL — the render test with `PromptTemplateError` (template not found); the reset test with an AttributeError on `phased_suggestion_active` or an assertion on the stale value

- [ ] **Step 3: Create the prompt template**

Create `resources/prompts/phased-suggestion-instruction.md` (single line, like `verdict-file-instruction.md`):

```markdown
- After writing your review and verdict, additionally judge whether this task suits Phased ATDD: does the spec describe two or more vertical features that can each be accepted independently at a stable seam? A single feature, a bugfix, a refactor, or a documentation-only task is not a fit. Overwrite {{WF}}/phased-suggestion.json with your built-in file editing tool, not a shell command; the file already exists with a default no-suggestion value. Use one line of JSON: {"phased": true|false, "reason": "one or two sentences"}. This judgment is separate from the verdict and must not influence approved or blockers.
```

- [ ] **Step 4: Add the context flag**

In `src/adversarial_ai_coding/workflow.py`, in the `WorkflowContext` dataclass, directly under `collect_review_suggestions: bool = True`:

```python
    collect_review_suggestions: bool = True
    phased_suggestion_active: bool = False
```

- [ ] **Step 5: Wire `run_review`**

In `src/adversarial_ai_coding/review.py`, add to the imports:

```python
from .phased_suggestion import DEFAULT_SUGGESTION, reset_suggestion, suggestion_path
```

In `run_review`, right after `ctx.verdict_path.write_text(FAILED_VERDICT, encoding="utf-8")`:

```python
    ctx.verdict_path.write_text(FAILED_VERDICT, encoding="utf-8")
    if ctx.phased_suggestion_active:
        reset_suggestion(ctx.wf)
```

Still in `run_review`, right after the `verdict-…json` `archive_snapshot` call (the one archiving `ctx.verdict_path`):

```python
    if ctx.phased_suggestion_active:
        _recover_unreadable_output(
            ctx, suggestion_path(ctx.wf), DEFAULT_SUGGESTION
        )
        if suggestion_path(ctx.wf).is_file():
            ctx.archive.archive_snapshot(
                suggestion_path(ctx.wf),
                f"phased-suggestion-{stage_slug}-r{ctx.cur_round}.json",
                "reviewer",
                agent,
                ctx.cur_stage,
                ctx.cur_round,
            )
```

(`stage_slug` already exists in scope; the `_recover_unreadable_output` guard mirrors the verdict handling for ACL-poisoned reviewer files — the fallback is the harmless default, so a poisoned suggestion silently becomes "no suggestion".)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_review.py -v`
Expected: PASS (new tests and all existing review tests)

- [ ] **Step 7: Commit**

```bash
git add resources/prompts/phased-suggestion-instruction.md src/adversarial_ai_coding/workflow.py src/adversarial_ai_coding/review.py tests/test_review.py
git commit -m "feat(review): reset and archive the phased suggestion per round" -m "run_review pre-creates .workflow/phased-suggestion.json with the
default no-suggestion value before each armed round, so a reviewer
that ignores the instruction yields no suggestion instead of a stale
one, and archives each round's file next to the verdict as
phased-suggestion-<stage>-r<N>.json. The behavior is scoped by a new
WorkflowContext.phased_suggestion_active flag so only spec reviews pay
for it; all other review stages leave the file alone. The new
phased-suggestion-instruction prompt template tells the reviewer the
fitness criterion (two or more independently acceptable vertical
features) and forbids the judgment from influencing the verdict."
```

---

### Task 4: Snapshot flip helper in `runstate.py`

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py`
- Test: `tests/test_runstate_snapshot.py`

**Interfaces:**
- Consumes: `SNAPSHOT_FILE`, `_atomic_write`, `RunStateError` (existing)
- Produces: `enable_snapshot_phases(state_dir: Path) -> None` — rewrites `settings.json` with `phases = "1"`, atomically; raises `RunStateError` if the snapshot is missing or unreadable (the flip must never be silently lost).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runstate_snapshot.py` (check the file's existing imports; add what is missing):

```python
def test_enable_snapshot_phases_flips_and_stays_resumable(tmp_path):
    import pytest

    from adversarial_ai_coding.config import Settings
    from adversarial_ai_coding.runstate import (
        RunStateError,
        check_immutable,
        enable_snapshot_phases,
        load_snapshot,
        snapshot_values,
        write_snapshot,
    )

    settings = Settings.from_env({}, run_id="t")
    write_snapshot(
        tmp_path,
        snapshot_values(
            settings,
            branch="main",
            gate_cmd="",
            build_gate_cmd="",
            phase_gate_cmd="",
            task_arg="t",
            task_source_kind="arg",
            task_source_path="",
        ),
    )
    enable_snapshot_phases(tmp_path)
    snap = load_snapshot(tmp_path)
    assert snap["PHASES"] == "1"
    # Resume with a clean environment: no immutable-key conflict, and the
    # resumed settings run phased without being "explicit".
    check_immutable({}, snap)
    resumed = Settings.from_env({}, run_id="t", snapshot=snap)
    assert resumed.phases is True
    assert resumed.phases_explicit is False
    # A stale explicit PHASES=0 in the resume environment still conflicts.
    with pytest.raises(RunStateError, match="PHASES=0 conflicts"):
        check_immutable({"PHASES": "0"}, snap)


def test_enable_snapshot_phases_requires_a_snapshot(tmp_path):
    import pytest

    from adversarial_ai_coding.runstate import (
        RunStateError,
        enable_snapshot_phases,
    )

    with pytest.raises(RunStateError, match="cannot record the Phased ATDD"):
        enable_snapshot_phases(tmp_path)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runstate_snapshot.py -v -k enable_snapshot`
Expected: FAIL with `ImportError: cannot import name 'enable_snapshot_phases'`

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/runstate.py`, after `load_snapshot`:

```python
def enable_snapshot_phases(state_dir: Path) -> None:
    """Record the spec-gate Phased ATDD flip so resume sees PHASES=1.

    The snapshot is the resume source of truth for PHASES. The flip must
    land atomically before the plan stage can run under phased templates;
    losing it would let a resumed attempt silently run the single-shot flow.
    """

    path = state_dir / SNAPSHOT_FILE
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RunStateError(
            f"{path}: cannot record the Phased ATDD flip ({exc})."
        ) from None
    payload["phases"] = "1"
    _atomic_write(path, json.dumps(payload, indent=2) + "\n")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runstate_snapshot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_runstate_snapshot.py
git commit -m "feat(runstate): record the spec-gate PHASES flip in the snapshot" -m "enable_snapshot_phases rewrites settings.json with phases=1 using the
existing atomic-write path. PHASES stays immutable across resume: the
flip updates the snapshot in the same moment the live settings change,
so every later resume reads one consistent value. Arming already
excludes runs whose environment set PHASES explicitly, so the
IMMUTABLE_KEYS check cannot conflict after a flip unless the user
re-exports PHASES=0 by hand, which still fails loudly as before. A
missing or unreadable snapshot raises RunStateError because silently
losing the flip would resume the wrong stage graph."
```

---

### Task 5: Spec-gate offer, settings flip, and the single-spec call site

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py` (`human_gate_spec`, new `offer_phased_suggestion` and `append_phased_suggestion_scope`, the spec review call site in `run_workflow`, dataclasses import)
- Test: `tests/test_stageflow.py`

**Interfaces:**
- Consumes: `suggestion_armed`, `read_suggestion` (Task 2), `enable_snapshot_phases` (Task 4), `WorkflowContext.phased_suggestion_active` (Task 3)
- Produces:
  - `append_phased_suggestion_scope(ctx: WorkflowContext, scope: str) -> str` — when armed, appends the rendered instruction block and sets `ctx.phased_suggestion_active = True`; otherwise returns `scope` unchanged. Task 6 reuses it from `dual_spec.py`.
  - `offer_phased_suggestion(ctx: WorkflowContext) -> None` — called by `human_gate_spec` in both single and dual paths.
  - `human_gate_spec` now runs the offer even when `HUMAN_GATE=0` (log-only there).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stageflow.py` (it already has `make_ctx`, `new_repo`, `with_state`, `human_gate_spec`, `WorkflowAbort`, `pytest` imported; add the new imports inside the tests to keep the header untouched):

```python
def _write_suggestion(ctx, phased=True, reason="two independent features"):
    import json

    (ctx.wf / "phased-suggestion.json").write_text(
        json.dumps({"phased": phased, "reason": reason}), encoding="utf-8"
    )


def _write_settings_snapshot(ctx):
    from adversarial_ai_coding.runstate import snapshot_values, write_snapshot

    write_snapshot(
        ctx.state.state_dir,
        snapshot_values(
            ctx.settings,
            branch="main",
            gate_cmd="",
            build_gate_cmd="",
            phase_gate_cmd="",
            task_arg="t",
            task_source_kind="arg",
            task_source_path="",
        ),
    )


def test_spec_gate_offer_flips_settings_and_snapshot(make_ctx, new_repo):
    from adversarial_ai_coding.runstate import load_snapshot

    ctx = with_state(
        make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"}), new_repo
    )
    _write_settings_snapshot(ctx)
    _write_suggestion(ctx)
    asked = []
    answers = iter(["y", "y"])
    ctx.ask = lambda prompt: (asked.append(prompt), next(answers))[1]
    human_gate_spec(ctx)
    assert ctx.settings.phases is True
    assert len(asked) == 2
    assert "Enable Phased ATDD" in asked[1]
    assert load_snapshot(ctx.state.state_dir)["PHASES"] == "1"


def test_spec_gate_offer_declined_changes_nothing(make_ctx, new_repo):
    from adversarial_ai_coding.runstate import load_snapshot

    ctx = with_state(
        make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"}), new_repo
    )
    _write_settings_snapshot(ctx)
    _write_suggestion(ctx)
    answers = iter(["y", "n"])
    ctx.ask = lambda prompt: next(answers)
    human_gate_spec(ctx)
    assert ctx.settings.phases is False
    assert load_snapshot(ctx.state.state_dir)["PHASES"] == "0"


def test_spec_gate_stays_silent_without_a_recommendation(make_ctx, new_repo):
    ctx = make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx, phased=False)
    asked = []
    ctx.ask = lambda prompt: (asked.append(prompt), "y")[1]
    human_gate_spec(ctx)
    assert len(asked) == 1  # only the spec approval question


def test_spec_gate_respects_explicit_phases_zero(make_ctx, new_repo):
    ctx = make_ctx({"PHASES": "0", "HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx)
    asked = []
    ctx.ask = lambda prompt: (asked.append(prompt), "y")[1]
    human_gate_spec(ctx)
    assert len(asked) == 1
    assert ctx.settings.phases is False


def test_spec_gate_logs_only_without_human_gate(make_ctx, new_repo):
    ctx = make_ctx({"HUMAN_GATE": "0", "RETRY_ON_LIMIT": "0"})
    _write_suggestion(ctx, reason="fits nicely")
    logged = []
    ctx.echo = logged.append
    ctx.ask = lambda prompt: pytest.fail("HUMAN_GATE=0 must never ask")
    human_gate_spec(ctx)
    assert ctx.settings.phases is False
    assert any(
        "reviewer suggests Phased ATDD: fits nicely" in line for line in logged
    )


def test_append_phased_suggestion_scope_only_when_armed(make_ctx):
    from adversarial_ai_coding.workflow import append_phased_suggestion_scope

    ctx = make_ctx()
    scope = append_phased_suggestion_scope(ctx, "base scope\n")
    assert scope.startswith("base scope\n")
    assert "phased-suggestion.json" in scope
    assert ctx.phased_suggestion_active is True

    ctx2 = make_ctx({"PHASES": "0", "RETRY_ON_LIMIT": "0"})
    assert append_phased_suggestion_scope(ctx2, "base scope\n") == "base scope\n"
    assert ctx2.phased_suggestion_active is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stageflow.py -v -k "spec_gate or append_phased"`
Expected: FAIL — `ImportError` for `append_phased_suggestion_scope`; the gate tests fail on `len(asked)` (only 1 ask happens) or on `ctx.settings.phases` staying `False`

- [ ] **Step 3: Implement in `workflow.py`**

Change the dataclasses import at the top of the file:

```python
from dataclasses import dataclass, field, replace
```

Replace `human_gate_spec` and add the two new functions directly below it:

```python
def human_gate_spec(ctx: WorkflowContext) -> None:
    if ctx.settings.human_gate:
        _human_approval(
            ctx,
            subject="spec",
            path=ctx.spec_dir / "spec.md",
            focus="the Assumptions and Open Questions section.",
        )
    offer_phased_suggestion(ctx)


def offer_phased_suggestion(ctx: WorkflowContext) -> None:
    """Offer to enable Phased ATDD when the spec reviewer recommended it.

    Runs after spec approval and before write-implementation-plan — the
    first stage whose behavior depends on PHASES — so the flip never
    invalidates a completed stage. Every failure mode of the suggestion
    file means "no suggestion"; this must never block a run.
    """

    from .phased_suggestion import read_suggestion, suggestion_armed

    if not suggestion_armed(ctx.settings):
        return
    phased, reason = read_suggestion(ctx.wf)
    if not phased:
        return
    if not ctx.settings.human_gate:
        ctx.log(
            f"reviewer suggests Phased ATDD: {reason}; HUMAN_GATE=0, not asking"
        )
        return
    ctx.echo("")
    ctx.echo(f"### Reviewer suggests Phased ATDD: {reason}")
    answer = ctx.ask("Enable Phased ATDD for this run? [y/N]:")
    if answer not in ("y", "Y"):
        ctx.log("Phased ATDD suggestion declined; keeping the single-shot flow")
        return
    ctx.settings = replace(ctx.settings, phases=True)
    if ctx.state is not None:
        from .runstate import enable_snapshot_phases

        enable_snapshot_phases(ctx.state.state_dir)
    ctx.log("Phased ATDD enabled at the spec gate")
    ctx.notify("adversarial-ai-coding: Phased ATDD enabled at the spec gate")


def append_phased_suggestion_scope(ctx: WorkflowContext, scope: str) -> str:
    """Arm the spec review to also judge phased fitness, when applicable."""

    from .phased_suggestion import suggestion_armed

    if not suggestion_armed(ctx.settings):
        return scope
    ctx.phased_suggestion_active = True
    return scope + render_prompt(
        ctx.prompts_dir, "phased-suggestion-instruction", {"WF": str(ctx.wf)}
    )
```

- [ ] **Step 4: Wire the single-spec call site**

In `run_workflow`, the spec review block currently reads:

```python
            if not ctx.settings.import_spec or ctx.settings.import_review:
                scope = render_prompt(
                    ctx.prompts_dir,
                    "review-scope-spec",
                    {"SPEC_FILE": str(spec_file)},
                )
                review_loop_ref(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    ctx.spec_roles.owner_agent,
                    scope,
                )
```

Change it to:

```python
            if not ctx.settings.import_spec or ctx.settings.import_review:
                scope = append_phased_suggestion_scope(
                    ctx,
                    render_prompt(
                        ctx.prompts_dir,
                        "review-scope-spec",
                        {"SPEC_FILE": str(spec_file)},
                    ),
                )
                review_loop_ref(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    ctx.spec_roles.owner_agent,
                    scope,
                )
                ctx.phased_suggestion_active = False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_stageflow.py -v`
Expected: PASS — including the two pre-existing gate tests (`test_human_gate_disabled_passes` and `test_human_gate_approval_and_abort` still hold: with no suggestion file, `read_suggestion` fails open and the offer stays silent)

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py tests/test_stageflow.py
git commit -m "feat(workflow): offer Phased ATDD at the spec human gate" -m "After the spec is approved at the human gate, the workflow reads the
reviewer's phased-suggestion.json; on a positive recommendation it
shows the reason and asks 'Enable Phased ATDD for this run? [y/N]'.
A yes replaces settings with phases=True and records the flip in the
resume snapshot atomically, before write-implementation-plan where the
stage graph first diverges on PHASES. With HUMAN_GATE=0 the
recommendation is logged and nothing is flipped. The single-spec
review call site arms the reviewer via append_phased_suggestion_scope,
which appends the instruction block only when the suggestion is armed
(phases off, no explicit PHASES, no imported plan)."
```

---

### Task 6: Dual-spec final review call site

**Files:**
- Modify: `src/adversarial_ai_coding/dual_spec.py` (`apply_dual_spec_decision`)
- Test: `tests/test_dual_spec.py`

**Interfaces:**
- Consumes: `append_phased_suggestion_scope` (Task 5) — `dual_spec.py` already imports names from `.workflow`; extend that import.
- Produces: the dual-spec final review scope carries the instruction block when armed; candidate reviews (`review-spec-a`/`review-spec-b`) never do.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dual_spec.py` (it already has `make_ctx`, `ds`, `dual_ctx`, `monkeypatch` patterns — mirror `test_apply_adopt`'s setup):

```python
def test_apply_decision_arms_phased_suggestion_on_final_review(
    make_ctx, monkeypatch
):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-a.md").write_text("candidate A\n", encoding="utf-8")
    (ctx.spec_dir / "spec-b.md").write_text("candidate B\n", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(
        ds,
        "review_loop",
        lambda ctx, reviewer, worker, scope: seen.setdefault("scope", scope),
    )
    monkeypatch.setattr(ds, "human_gate_spec", lambda ctx: None)
    ds.apply_dual_spec_decision(ctx, "adopt-a", "task text")
    assert "phased-suggestion.json" in seen["scope"]
    # The flag is scoped to the loop: cleared before the human gate runs.
    assert ctx.phased_suggestion_active is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_dual_spec.py::test_apply_decision_arms_phased_suggestion_on_final_review -v`
Expected: FAIL with `AssertionError` — `"phased-suggestion.json" in seen["scope"]` is false

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/dual_spec.py`, add `append_phased_suggestion_scope` to the existing `from .workflow import (...)` block.

In `apply_dual_spec_decision`, the tail currently reads:

```python
    review_loop(
        ctx,
        ctx.spec_roles.reviewer_agent,
        ctx.spec_roles.owner_agent,
        dual_spec_final_review_scope(ctx, decision),
    )
    human_gate_spec(ctx)
```

Change it to:

```python
    scope = append_phased_suggestion_scope(
        ctx, dual_spec_final_review_scope(ctx, decision)
    )
    review_loop(
        ctx,
        ctx.spec_roles.reviewer_agent,
        ctx.spec_roles.owner_agent,
        scope,
    )
    ctx.phased_suggestion_active = False
    human_gate_spec(ctx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dual_spec.py -v`
Expected: PASS — the pre-existing `test_apply_adopt_*` and `test_apply_merge_*` tests still pass (they assert on reviewer/worker identity and on `"block approval" in scope`, both preserved because the instruction is appended after the base scope)

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/dual_spec.py tests/test_dual_spec.py
git commit -m "feat(dual-spec): arm the phased suggestion on the final review" -m "The dual-spec final review (of the adopted or merged spec.md) now
carries the phased-suggestion instruction block under the same arming
rules as the single-spec path, so human_gate_spec can offer the flip
right after. Candidate spec reviews are deliberately untouched: only
the final spec's reviewer judges fitness, so at most one suggestion
exists per attempt."
```

---

### Task 7: Offline integration tests through the real CLI

**Files:**
- Modify: `tests/fake_agent.py` (review branch writes the suggestion when instructed)
- Test: `tests/test_phased_integration.py`

**Interfaces:**
- Consumes: the full feature (Tasks 1-6); `wf_env`/`run_cli`/`state_dir_of`/`driver_workdir` helpers and the `EXPECTED_STAGES` list already in `tests/test_phased_integration.py`; the simulated-terminal pattern from `test_plan_gate_asks_and_commits_the_human_edit`.
- Produces: `FAKE_PHASED_SUGGESTION` env knob for the fake reviewer.

- [ ] **Step 1: Teach the fake reviewer to follow the instruction**

In `tests/fake_agent.py`, the review branch currently reads:

```python
    if kind in ("review", "review-dual-final"):
        Path(".workflow").mkdir(exist_ok=True)
        Path(".workflow/review.md").write_text(
            f"approved by {name}\n", encoding="utf-8"
        )
        Path(".workflow/verdict.json").write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}\n',
            encoding="utf-8",
        )
```

Add, inside the same branch, after the verdict write:

```python
        if "phased-suggestion.json" in prompt:
            Path(".workflow/phased-suggestion.json").write_text(
                os.environ.get(
                    "FAKE_PHASED_SUGGESTION",
                    '{"phased": true, "reason": "two independent features"}',
                )
                + "\n",
                encoding="utf-8",
            )
```

(Writes only when the prompt carries the instruction — the fake mirrors a compliant real reviewer, which also proves non-spec reviews never receive it.)

- [ ] **Step 2: Write the failing integration tests**

Append to `tests/test_phased_integration.py`. Add the missing imports at the top of the file:

```python
from adversarial_ai_coding import cli
```

Then the tests:

```python
def test_spec_gate_suggestion_flips_run_to_phased(new_repo, tmp_path, monkeypatch):
    """PHASES unset; the reviewer suggests phased; the human says y twice."""

    import json

    from adversarial_ai_coding import workflow as wf_mod

    work = driver_workdir(tmp_path)
    work.mkdir()
    (work / "check_impl.py").write_text(
        "import pathlib, sys\n"
        "sys.exit(0 if pathlib.Path('src.txt').exists() else 1)\n",
        encoding="utf-8",
    )
    env = wf_env(
        work,
        HUMAN_GATE="1",
        PHASE_GATE_CMD=f'"{sys.executable}" "{work / "check_impl.py"}"',
    )
    assert "PHASES" not in env  # the whole point: nobody set it
    asked = []
    answers = iter(["y", "y"])

    def fake_input(prompt=""):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(new_repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert cli.main(["demo task"], env, stdin_isatty=True) == 0
    assert len(asked) == 2
    assert "Enable Phased ATDD" in asked[1]
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    assert st.completed_stages() == EXPECTED_STAGES
    snap = json.loads((state / "settings.json").read_text(encoding="utf-8"))
    assert snap["phases"] == "1"


def test_spec_gate_suggestion_declined_stays_single_shot(
    new_repo, tmp_path, monkeypatch
):
    import json

    from adversarial_ai_coding import workflow as wf_mod

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, HUMAN_GATE="1")
    answers = iter(["y", "n"])
    monkeypatch.setattr(wf_mod.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.chdir(new_repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert cli.main(["demo task"], env, stdin_isatty=True) == 0
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    stages = st.completed_stages()
    assert "write-acceptance-tests" in stages
    assert not any(stage.startswith("phase-") for stage in stages)
    snap = json.loads((state / "settings.json").read_text(encoding="utf-8"))
    assert snap["phases"] == "0"
```

- [ ] **Step 3: Run the new tests**

Run: `uv run pytest tests/test_phased_integration.py -v -k spec_gate_suggestion`
Expected: PASS (Tasks 1-6 are already in place; if either fails, debug the feature, not the test)

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS — existing integration tests are unaffected because `wf_env` defaults to `HUMAN_GATE=0`, where the gate never asks, and the fake reviewer writes the suggestion only when the prompt instructs it

- [ ] **Step 5: Commit**

```bash
git add tests/fake_agent.py tests/test_phased_integration.py
git commit -m "test: cover the spec-gate phased flip end to end" -m "Two offline CLI runs with fake agents and a simulated terminal: one
accepts the reviewer's phased suggestion at the spec gate and must
complete the exact phased stage list with phases=1 recorded in the
snapshot; one declines and must complete the single-shot flow with the
snapshot untouched. The fake reviewer writes phased-suggestion.json
only when the review prompt carries the instruction block, which also
proves non-spec reviews never receive it."
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `README.zh-TW.md`, `docs/how-it-works.md`, `docs/how-it-works.zh-TW.md`, `resources/AGENTS.template.md`, `docs/python-port-parity.md`
- Test: `tests/test_documentation.py`

(The untracked `flow-*.txt` files in the repo root are scratch, not repo docs — leave them alone.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_documentation.py`:

```python
def test_phased_suggestion_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "phased-suggestion.json" in readme
        assert "Enable Phased ATDD for this run?" in readme
    assert "phased-suggestion.json" in _read("resources/AGENTS.template.md")
    assert "phased-suggestion.json" in _read("docs/python-port-parity.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_documentation.py::test_phased_suggestion_is_documented_bilingually -v`
Expected: FAIL on the first `assert`

- [ ] **Step 3: Update README.md**

In the default-pipeline ASCII diagram near the top of the file, the spec
stage reads:

```text
Spec (A writes, B reviews)
human gate
commit
```

Change the `human gate` line to:

```text
human gate (may offer Phased ATDD; see below)
```

In the environment-variable table, extend the `PHASES` row (keep the existing text, append before the closing `|`):

```
 When `PHASES` is unset, the spec reviewer also judges fitness and the spec human gate may offer to enable it (see [Phased ATDD Mode](#phased-atdd-mode)).
```

In the `## Phased ATDD Mode` section, replace the closing sentence `` `PHASES` cannot change across resume. `` with:

```markdown
`PHASES` cannot change across resume: the value is snapshotted at run
start and conflicting resume environments are rejected. There is one
sanctioned in-run switch. When `PHASES` is unset and no plan is
imported, the spec reviewer also judges whether the task suits phased
mode — two or more vertical features that can each be accepted
independently — and writes its judgment to
`.workflow/phased-suggestion.json`. If it recommends phased, the spec
human gate shows the reason and asks `Enable Phased ATDD for this run?
[y/N]`. Answering `y` enables phased mode and rewrites the run
snapshot atomically, so every later resume still sees one consistent
value. With `HUMAN_GATE=0` the recommendation is only logged; nothing
is ever enabled automatically. An explicit `PHASES=0` in the
environment disables the suggestion entirely.
```

- [ ] **Step 4: Update README.zh-TW.md**

In the zh-TW default-pipeline ASCII diagram, the spec stage reads:

```text
Spec(A 寫、B review)
Human Gate
commit
```

Change the `Human Gate` line to:

```text
Human Gate(可能提議開啟 Phased ATDD;見下文)
```

Find the `PHASES` row in the zh-TW environment-variable table and append the same sentence in Traditional Chinese:

```
 未設定 `PHASES` 時,spec 審查者會一併判斷是否適合,spec human gate 可能提議開啟(見 Phased ATDD 模式章節)。
```

Find the Phased ATDD section's closing statement about `PHASES` 不可跨 resume 變更, and replace it with:

```markdown
`PHASES` 不可跨 resume 變更:值在 run 開始時寫入 snapshot,resume 時環境
變數衝突會被拒絕。唯一被允許的 run 中切換:當 `PHASES` 未設定且沒有匯入
plan 時,spec 審查者會一併判斷任務是否適合 phased 模式(兩個以上可獨立
驗收的 vertical feature),並把判斷寫入 `.workflow/phased-suggestion.json`。
若建議採用,spec human gate 會顯示理由並詢問 `Enable Phased ATDD for this
run? [y/N]`;回答 `y` 即開啟 phased 模式並原子性改寫 run snapshot,之後
的 resume 仍然只看到單一一致的值。`HUMAN_GATE=0` 時只記錄建議,絕不自動
開啟;環境變數明確設 `PHASES=0` 則完全不啟動建議機制。
```

- [ ] **Step 5: Update the remaining docs**

`docs/how-it-works.md` — in the section describing the spec stage / human gate, add one sentence:

```markdown
When `PHASES` is unset, the spec reviewer also writes a phased-fitness
judgment to `.workflow/phased-suggestion.json`, and the spec human gate
may offer to enable Phased ATDD before the plan is written.
```

`docs/how-it-works.zh-TW.md` — same place, in Traditional Chinese:

```markdown
`PHASES` 未設定時,spec 審查者會把 phased 適合度判斷寫入
`.workflow/phased-suggestion.json`,spec human gate 可能在 plan 撰寫前
提議開啟 Phased ATDD。
```

`resources/AGENTS.template.md` — add a new section after `## Verdict (.workflow/verdict.json)`:

```markdown
## Phased suggestion (.workflow/phased-suggestion.json)

When the review prompt asks for it, the spec reviewer also writes
.workflow/phased-suggestion.json as one line of JSON:
{"phased": true|false, "reason": "one or two sentences"}. This judgment
is separate from the verdict: never put it in verdict.json, and never
let it influence approved or blockers.
```

`docs/python-port-parity.md` — add a row to the divergence table, styled after the `HUMAN_GATE_PLAN` row:

```markdown
| The spec gate can enable Phased ATDD when the reviewer suggests it | Python-only addition, not a port gap: with `PHASES` unset, the spec reviewer writes a fitness judgment to `.workflow/phased-suggestion.json` and the gate may offer the flip; the snapshot is rewritten atomically so `PHASES` stays consistent across resume. Off whenever `PHASES` is set explicitly, so the bash-equivalent flow is unchanged. | `tests/test_stageflow.py::test_spec_gate_offer_flips_settings_and_snapshot`, `tests/test_phased_integration.py::test_spec_gate_suggestion_flips_run_to_phased` |
```

- [ ] **Step 6: Run the documentation tests**

Run: `uv run pytest tests/test_documentation.py -v`
Expected: PASS

- [ ] **Step 7: Full suite once more**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add README.md README.zh-TW.md docs/how-it-works.md docs/how-it-works.zh-TW.md resources/AGENTS.template.md docs/python-port-parity.md tests/test_documentation.py
git commit -m "docs: document the spec-gate Phased ATDD suggestion" -m "Both READMEs explain the one sanctioned in-run PHASES switch: when
PHASES is unset and no plan is imported, the spec reviewer writes a
fitness judgment to .workflow/phased-suggestion.json and the spec
human gate may offer to enable phased mode, rewriting the snapshot
atomically so resume stays consistent. AGENTS.template.md tells
reviewers to keep the judgment out of verdict.json. The parity doc
records this as a Python-only addition. A documentation test pins the
bilingual coverage."
```

---

## Self-Review Notes

- **Spec coverage:** Arming conditions → Tasks 1-2; reviewer judgment/transport (instruction block, pre-create, per-round archive) → Task 3; gate interaction, flip, snapshot rewrite, HUMAN_GATE=0 log-only → Tasks 4-5; dual-spec final review only → Task 6; error handling (fail-open) → Tasks 2, 3, 5 tests; integration → Task 7; documentation → Task 8. The "no re-ask after decline" property is structural (stage ledger) and covered by the existing resume machinery.
- **Type consistency:** `read_suggestion` returns `tuple[bool, str]` everywhere; `append_phased_suggestion_scope(ctx, scope) -> str`; `enable_snapshot_phases(state_dir: Path) -> None`; the flag is `ctx.phased_suggestion_active`.
- Existing tests that touch `human_gate_spec` and `apply_dual_spec_decision` keep passing without edits (verified against their assertions in Tasks 5-6).
