# Import External Spec and Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the workflow accept externally written `spec.md` / `plan.md` files (`IMPORT_SPEC`, `IMPORT_PLAN`) and skip the corresponding "write" step, while keeping adversarial review by default (`IMPORT_REVIEW`, default `1`).

**Architecture:** A new `imports.py` module owns deterministic validation (preflight, before any AI call) and the stage-time copy/archive step. `run_workflow` branches only on the "write" half of the two authoring stages; review loop, human gates, commits, stage ledger, and resume stay untouched. The three new settings are snapshotted and immutable across resume. `finish()` reports honest provenance in `pr-body.md`.

**Tech Stack:** Python 3.12+ stdlib only (pytest is dev-only), uv-managed package.

**Spec:** `docs/superpowers/specs/2026-07-19-import-spec-plan-design.md`

## Global Constraints

- Environment variable names are exactly `IMPORT_SPEC`, `IMPORT_PLAN`, `IMPORT_REVIEW`. Do NOT use `SPEC_FILE`/`PLAN_FILE` (those are prompt-template placeholders).
- No new runtime dependencies; stdlib only.
- Config errors raise `SettingsError`; in-stage failures raise `WorkflowAbort` with a `!! ` message prefix (existing conventions).
- All source, tests, and docs stay ASCII-only except the existing Traditional Chinese docs (`README.zh-TW.md`, `docs/how-it-works.zh-TW.md`).
- Run tests with `uv run pytest -q`. On this machine, clear `PYTHONHOME`/`PYTHONPATH` first (PowerShell: `$env:PYTHONHOME=''; $env:PYTHONPATH=''`). E2E live tests are `-m e2e` gated and excluded by default.
- One task = one commit. Conventional Commit messages with a detailed body; do NOT add a `Co-Authored-By` trailer.
- `IMPORT_REVIEW=0` never skips: human gates, deterministic format checks, the phased structure check, or commits.

---

### Task 1: Import settings in config

**Files:**
- Modify: `src/adversarial_ai_coding/config.py`
- Test: `tests/test_config.py` (append)

**Interfaces:**
- Consumes: existing `Settings.from_env(env, run_id, snapshot)` and its `persisted()` resolution (env, then snapshot, then default).
- Produces: `Settings.import_spec: str` (default `""`), `Settings.import_plan: str` (default `""`), `Settings.import_review: bool` (default `True`). Later tasks read exactly these attribute names.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_config.py`:

```python
def test_import_settings_default_off():
    settings = Settings.from_env({}, run_id="r")
    assert settings.import_spec == ""
    assert settings.import_plan == ""
    assert settings.import_review is True


def test_import_settings_from_env_and_snapshot():
    settings = Settings.from_env(
        {"IMPORT_SPEC": "ext/spec.md", "IMPORT_REVIEW": "0"},
        run_id="r",
        snapshot={"IMPORT_PLAN": "ext/plan.md"},
    )
    assert settings.import_spec == "ext/spec.md"
    assert settings.import_plan == "ext/plan.md"
    assert settings.import_review is False
```

(`tests/test_config.py` already imports `Settings`; if the import line differs, reuse whatever name that file imports.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL with `TypeError` (unexpected/missing dataclass fields) or `AttributeError: import_spec`.

- [ ] **Step 3: Implement** — in `src/adversarial_ai_coding/config.py`, add three fields to the `Settings` dataclass directly after `dual_spec: bool`:

```python
    dual_spec: bool
    import_spec: str
    import_plan: str
    import_review: bool
```

and in `from_env`, directly after the `dual_spec=...` line:

```python
            import_spec=persisted("IMPORT_SPEC", ""),
            import_plan=persisted("IMPORT_PLAN", ""),
            import_review=persisted("IMPORT_REVIEW", "1") == "1",
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (existing `Settings(...)` construction goes through `from_env`, so no other call sites break; if any test constructs `Settings` positionally, fix that call site by keyword).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/config.py tests/test_config.py
git commit -m "feat(config): add IMPORT_SPEC, IMPORT_PLAN, IMPORT_REVIEW settings

Three new persisted settings for importing externally written spec and
plan files. IMPORT_REVIEW defaults to 1 so imported artifacts keep the
adversarial review loop unless the user explicitly opts out. Values
resolve env-first, then resume snapshot, then default, like every other
persisted key. Design: docs/superpowers/specs/
2026-07-19-import-spec-plan-design.md"
```

---

### Task 2: Validators and preflight in imports.py

**Files:**
- Create: `src/adversarial_ai_coding/imports.py`
- Create: `tests/test_imports.py`

**Interfaces:**
- Consumes: `SettingsError` from `.config`; `TASK_PREFIX`, `parse_phases`, `PhasePlanError` from `.phases`; `Settings.import_spec/import_plan/import_review/dual_spec/phases` (Task 1).
- Produces: `validate_import_spec(path: Path) -> None`, `validate_import_plan(path: Path, phases: bool) -> None`, `import_preflight(settings, env, *, fresh_run: bool) -> None`. All raise `SettingsError` on failure. Task 4 calls `import_preflight` from the CLI and reuses both validators from `stage_import`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_imports.py`:

```python
"""Unit tests for import validation and preflight (IMPORT_SPEC/IMPORT_PLAN)."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.imports import (
    import_preflight,
    validate_import_plan,
    validate_import_spec,
)

GOOD_SPEC = "# Spec\n\nBody.\n\n## Assumptions and Open Questions\n\n- none\n"
GOOD_PLAN = "# Plan\n\n- [ ] one task\n"
PHASED_PLAN = (
    "# Plan\n\n## Phase 1: works\nAcceptance: observable.\n- [ ] do it\n"
)


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_spec_validation(tmp_path):
    validate_import_spec(_write(tmp_path / "s.md", GOOD_SPEC))
    with pytest.raises(SettingsError, match="file not found"):
        validate_import_spec(tmp_path / "missing.md")
    with pytest.raises(SettingsError, match="empty"):
        validate_import_spec(_write(tmp_path / "e.md", "  \n"))
    with pytest.raises(SettingsError, match="Assumptions"):
        validate_import_spec(
            _write(tmp_path / "n.md", "# Spec\n\nno required section\n")
        )


def test_spec_heading_is_case_insensitive_and_order_free(tmp_path):
    validate_import_spec(
        _write(
            tmp_path / "s.md",
            "# Spec\n\n### OPEN QUESTIONS and assumptions\n\n- none\n",
        )
    )


def test_plan_validation_basic(tmp_path):
    validate_import_plan(_write(tmp_path / "p.md", GOOD_PLAN), phases=False)
    with pytest.raises(SettingsError, match="task line"):
        validate_import_plan(
            _write(tmp_path / "done.md", "# Plan\n\n- [x] already done\n"),
            phases=False,
        )


def test_plan_validation_phased(tmp_path):
    validate_import_plan(_write(tmp_path / "p.md", PHASED_PLAN), phases=True)
    with pytest.raises(SettingsError, match="phased plan"):
        validate_import_plan(_write(tmp_path / "b.md", GOOD_PLAN), phases=True)


def test_preflight_combination_rules(tmp_path):
    spec = _write(tmp_path / "s.md", GOOD_SPEC)

    env = {"IMPORT_PLAN": str(tmp_path / "p.md")}
    with pytest.raises(SettingsError, match="IMPORT_PLAN requires IMPORT_SPEC"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)

    env = {"IMPORT_REVIEW": "0"}
    with pytest.raises(SettingsError, match="IMPORT_REVIEW"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)

    env = {"IMPORT_SPEC": str(spec), "DUAL_SPEC": "1"}
    with pytest.raises(SettingsError, match="DUAL_SPEC"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)


def test_preflight_validates_files_only_on_fresh_runs(tmp_path):
    env = {"IMPORT_SPEC": str(tmp_path / "gone.md")}
    settings = Settings.from_env(env, run_id="r")
    with pytest.raises(SettingsError, match="file not found"):
        import_preflight(settings, env, fresh_run=True)
    import_preflight(settings, env, fresh_run=False)


def test_preflight_accepts_good_import(tmp_path):
    spec = _write(tmp_path / "s.md", GOOD_SPEC)
    plan = _write(tmp_path / "p.md", GOOD_PLAN)
    env = {"IMPORT_SPEC": str(spec), "IMPORT_PLAN": str(plan)}
    import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_imports.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'adversarial_ai_coding.imports'`.

- [ ] **Step 3: Implement** — create `src/adversarial_ai_coding/imports.py`:

```python
"""Import of externally written spec/plan files (IMPORT_SPEC/IMPORT_PLAN).

Design: docs/superpowers/specs/2026-07-19-import-spec-plan-design.md.
Deterministic validation runs at preflight, before any paid AI call. File
validation is fresh-run only: a resumed run re-validates at stage time,
because a completed import stage must not fail resume when the source
file has since been deleted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .config import Settings, SettingsError
from .phases import TASK_PREFIX, PhasePlanError, parse_phases

CONTRACT_HINT = "See docs/import-format.md for the import format contract."


def _read_import_file(path: Path, var: str) -> str:
    if not path.is_file():
        raise SettingsError(f"{var}={path}: file not found.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SettingsError(f"{var}={path}: unreadable ({exc}).") from None
    if not text.strip():
        raise SettingsError(f"{var}={path}: file is empty.")
    return text


def _has_assumptions_heading(text: str) -> bool:
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        lower = line.lower()
        if "assumptions" in lower and "open questions" in lower:
            return True
    return False


def validate_import_spec(path: Path) -> None:
    text = _read_import_file(path, "IMPORT_SPEC")
    if not _has_assumptions_heading(text):
        raise SettingsError(
            f"IMPORT_SPEC={path}: the spec must contain a Markdown heading "
            "whose text includes both 'Assumptions' and 'Open Questions' "
            "(case-insensitive), for example "
            f"'## Assumptions and Open Questions'. {CONTRACT_HINT}"
        )


def validate_import_plan(path: Path, phases: bool) -> None:
    text = _read_import_file(path, "IMPORT_PLAN")
    if phases:
        try:
            parse_phases(path)
        except PhasePlanError as exc:
            raise SettingsError(
                f"IMPORT_PLAN={path}: not a valid phased plan "
                f"(PHASES=1):\n{exc}\n{CONTRACT_HINT}"
            ) from None
    elif not any(
        line.startswith(TASK_PREFIX) for line in text.splitlines()
    ):
        raise SettingsError(
            f"IMPORT_PLAN={path}: the plan must contain at least one "
            f"'{TASK_PREFIX}' task line. {CONTRACT_HINT}"
        )


def import_preflight(
    settings: Settings, env: Mapping[str, str], *, fresh_run: bool
) -> None:
    """Reject bad import config before workspace setup and any AI call."""

    if env.get("IMPORT_REVIEW") and not settings.import_spec:
        raise SettingsError(
            "IMPORT_REVIEW is set but IMPORT_SPEC is not. Unset "
            "IMPORT_REVIEW, or provide IMPORT_SPEC."
        )
    if settings.import_plan and not settings.import_spec:
        raise SettingsError(
            "IMPORT_PLAN requires IMPORT_SPEC: a plan is written against a "
            "spec, and the workflow does not reconstruct a spec from a plan."
        )
    if settings.import_spec and settings.dual_spec:
        raise SettingsError(
            "IMPORT_SPEC and DUAL_SPEC=1 are incompatible: dual candidate "
            "specs and an imported spec contradict each other. Disable one."
        )
    if not fresh_run or not settings.import_spec:
        return
    validate_import_spec(Path(settings.import_spec))
    if settings.import_plan:
        validate_import_plan(Path(settings.import_plan), settings.phases)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_imports.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/imports.py tests/test_imports.py
git commit -m "feat(imports): validate imported spec and plan before any AI call

New imports module with deterministic validators and the startup
preflight. Rules: IMPORT_PLAN requires IMPORT_SPEC; IMPORT_REVIEW
without IMPORT_SPEC is a config error; IMPORT_SPEC conflicts with
DUAL_SPEC=1. The spec needs an Assumptions and Open Questions heading;
a basic plan needs at least one '- [ ] ' task; a phased plan must pass
parse_phases. File checks run on fresh runs only, because resumed runs
re-validate at stage time instead."
```

---

### Task 3: Snapshot and immutability across resume

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py`
- Test: `tests/test_runstate_snapshot.py` (append)

**Interfaces:**
- Consumes: `Settings.import_spec/import_plan/import_review` (Task 1).
- Produces: `SNAPSHOT_KEYS` includes `"import_spec"`, `"import_plan"`, `"import_review"`; `snapshot_values()` emits them; `check_immutable` treats `IMPORT_SPEC`, `IMPORT_PLAN`, `IMPORT_REVIEW` as immutable, with missing-key back-compat (`""`, `""`, `"1"`).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_runstate_snapshot.py` (reuse that file's existing imports; add any of `Settings`, `check_immutable`, `load_snapshot`, `snapshot_values`, `write_snapshot`, `RunStateError`, `pytest` that are missing):

```python
def test_snapshot_round_trips_import_settings(tmp_path):
    settings = Settings.from_env(
        {
            "IMPORT_SPEC": "ext/spec.md",
            "IMPORT_PLAN": "ext/plan.md",
            "IMPORT_REVIEW": "0",
        },
        run_id="r",
    )
    values = snapshot_values(
        settings,
        branch="auto/r",
        gate_cmd="",
        build_gate_cmd="",
        phase_gate_cmd="",
        task_arg="t",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["import_spec"] == "ext/spec.md"
    assert values["import_plan"] == "ext/plan.md"
    assert values["import_review"] == "0"
    write_snapshot(tmp_path, values)
    snap = load_snapshot(tmp_path)
    resumed = Settings.from_env({}, run_id="r2", snapshot=snap)
    assert resumed.import_spec == "ext/spec.md"
    assert resumed.import_plan == "ext/plan.md"
    assert resumed.import_review is False


def test_import_keys_are_immutable_on_resume():
    snap = {"IMPORT_SPEC": "a.md", "IMPORT_PLAN": "", "IMPORT_REVIEW": "1"}
    check_immutable({"IMPORT_SPEC": "a.md"}, snap)
    with pytest.raises(RunStateError, match="IMPORT_SPEC"):
        check_immutable({"IMPORT_SPEC": "b.md"}, snap)
    with pytest.raises(RunStateError, match="IMPORT_REVIEW"):
        check_immutable({"IMPORT_REVIEW": "0"}, snap)


def test_import_missing_from_old_snapshot_refuses_enabling():
    check_immutable({}, {})
    with pytest.raises(RunStateError, match="IMPORT_SPEC"):
        check_immutable({"IMPORT_SPEC": "a.md"}, {})
    with pytest.raises(RunStateError, match="IMPORT_REVIEW"):
        check_immutable({"IMPORT_REVIEW": "0"}, {})
    check_immutable({"IMPORT_REVIEW": "1"}, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runstate_snapshot.py -q`
Expected: FAIL — `KeyError: 'import_spec'` in the round-trip test, and no `RunStateError` raised in the immutability tests.

- [ ] **Step 3: Implement** — in `src/adversarial_ai_coding/runstate.py`:

In `SNAPSHOT_KEYS`, after `"dual_spec",` add:

```python
    "import_spec",
    "import_plan",
    "import_review",
```

Replace the `IMMUTABLE_KEYS` line with:

```python
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
```

In `snapshot_values`, after the `"dual_spec": flag(settings.dual_spec),` line add:

```python
        "import_spec": settings.import_spec,
        "import_plan": settings.import_plan,
        "import_review": flag(settings.import_review),
```

In `check_immutable`, replace the entire `for key in IMMUTABLE_KEYS:` loop (including the loop line and the old `PHASES` special case) with:

```python
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
```

- [ ] **Step 4: Run the full suite** (resume tests exercise these paths widely)

Run: `uv run pytest -q`
Expected: PASS. If an existing test asserts the old immutable error text `decide the stage graph`, update that assertion to the new wording.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_runstate_snapshot.py
git commit -m "feat(runstate): persist and freeze import settings across resume

import_spec, import_plan, and import_review join the resume snapshot,
and IMPORT_SPEC/IMPORT_PLAN/IMPORT_REVIEW become immutable keys: a
resume reads them back from the snapshot, and a conflicting environment
value refuses to resume. Snapshots from before this feature default to
no-import (and review on), so import cannot be enabled mid-run on an
old state directory."
```

---

### Task 4: Wire import into the workflow and CLI

**Files:**
- Modify: `src/adversarial_ai_coding/imports.py` (add `stage_import`)
- Modify: `src/adversarial_ai_coding/workflow.py` (`run_workflow` spec and plan stages)
- Modify: `src/adversarial_ai_coding/cli.py` (preflight call + settings echo)
- Test: `tests/test_imports.py` (append), Create: `tests/test_import_integration.py`

**Interfaces:**
- Consumes: `import_preflight`, `validate_import_spec`, `validate_import_plan` (Task 2); `ctx.settings.import_*` (Task 1); `ctx.archive.archive_snapshot(src, name, role, agent, stage, round)` and `ctx.archive.art_path(name)`; `begin_stage`/`end_stage`/`review_loop_ref`/`work`/`human_gate_*` as-is.
- Produces: `stage_import(ctx, kind: str, src_str: str, dst: Path) -> None` in `imports.py` (raises `WorkflowAbort`); archive artifact names `imported-spec.md` / `imported-plan.md`; log lines `Imported spec from <path> (review: on|off)` (same for plan) — the E2E task greps these.

- [ ] **Step 1: Write the failing unit test** — append to `tests/test_imports.py` (also add `from adversarial_ai_coding.config import WorkflowAbort` to its imports; the `make_ctx`/`new_repo` fixtures come from `tests/conftest.py`):

```python
def test_stage_import_copies_archives_and_aborts_on_missing(make_ctx, tmp_path):
    from adversarial_ai_coding.imports import stage_import

    ctx = make_ctx({"IMPORT_SPEC": "unused", "RETRY_ON_LIMIT": "0"})
    src = tmp_path / "ext-spec.md"
    src.write_text(GOOD_SPEC, encoding="utf-8")
    dst = ctx.spec_dir / "spec.md"
    stage_import(ctx, "spec", str(src), dst)
    assert dst.read_text(encoding="utf-8") == GOOD_SPEC
    assert src.read_text(encoding="utf-8") == GOOD_SPEC
    assert list(ctx.archive.run_dir.glob("*imported-spec.md"))
    with pytest.raises(WorkflowAbort, match="archived copy"):
        stage_import(ctx, "spec", str(tmp_path / "gone.md"), dst)
```

- [ ] **Step 2: Write the failing integration tests** — create `tests/test_import_integration.py`:

```python
"""Import-mode integration: cli.main end-to-end with fake agents.

Reuses the resume-suite harness. Fake-agent call counts prove which AI
steps ran: a full basic run has exactly 4 reviewer 'review' calls (spec,
plan, branch, final acceptance); import with IMPORT_REVIEW=0 drops the
spec and plan reviews, leaving 2.
"""

from pathlib import Path

from test_resume_integration import (
    calls,
    driver_workdir,
    run_cli,
    state_dir_of,
    wf_env,
)

SPEC_TEXT = (
    "# Spec: demo feature\n\nDemo feature description.\n\n"
    "## Assumptions and Open Questions\n\n- none\n"
)
PLAN_TEXT = "# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n"


def import_files(work: Path, plan: bool = False) -> dict:
    spec = work / "external-spec.md"
    spec.write_text(SPEC_TEXT, encoding="utf-8")
    overrides = {"IMPORT_SPEC": str(spec)}
    if plan:
        plan_file = work / "external-plan.md"
        plan_file.write_text(PLAN_TEXT, encoding="utf-8")
        overrides["IMPORT_PLAN"] = str(plan_file)
    return overrides


def test_import_spec_skips_write_and_keeps_review(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 1
    assert calls(work, "fake-reviewer review") == 4
    spec = next((new_repo / "specs").glob("*/spec.md"))
    assert spec.read_text(encoding="utf-8") == SPEC_TEXT
    assert (work / "external-spec.md").read_text(encoding="utf-8") == SPEC_TEXT
    run_dir = Path(
        (new_repo / ".workflow" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-spec.md"))


def test_import_spec_and_plan_review_off(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work, plan=True), IMPORT_REVIEW="0")
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 0
    assert calls(work, "fake-reviewer review") == 2
    plan = next((new_repo / "specs").glob("*/plan.md"))
    text = plan.read_text(encoding="utf-8")
    assert "- [x]" in text and "- [ ] " not in text
    run_dir = Path(
        (new_repo / ".workflow" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-plan.md"))


def test_import_preflight_fails_before_any_agent_call(
    new_repo, tmp_path, monkeypatch
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    bad_spec = work / "no-assumptions.md"
    bad_spec.write_text("# Spec\n\nNo required section.\n", encoding="utf-8")
    env = wf_env(work, IMPORT_SPEC=str(bad_spec))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 1
    assert not (work / "calls.log").is_file()

    env = wf_env(work, IMPORT_PLAN=str(work / "external-plan.md"))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 1
    assert not (work / "calls.log").is_file()


def test_import_run_resumes_from_snapshot(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work, plan=True))
    (work / "abort-on").write_text("write-acceptance\n", encoding="utf-8")
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 75
    state = state_dir_of(new_repo)
    (work / "abort-on").unlink()

    resume_env = {
        key: value
        for key, value in env.items()
        if not key.startswith("IMPORT_")
    }
    resume_env["RESUME_RUN"] = state.name

    conflict_env = dict(
        resume_env, IMPORT_SPEC=str(work / "somewhere-else.md")
    )
    assert run_cli(new_repo, conflict_env, args=[], monkeypatch=monkeypatch) == 1

    assert run_cli(new_repo, resume_env, args=[], monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_imports.py tests/test_import_integration.py -q`
Expected: FAIL — `ImportError: cannot import name 'stage_import'`, and the integration tests fail because `write-spec` is still called by the worker (count 1, not 0).

- [ ] **Step 4: Implement `stage_import`** — append to `src/adversarial_ai_coding/imports.py` (add `import shutil` below `from __future__ import annotations`, and `WorkflowAbort` to the `.config` import):

```python
def stage_import(ctx, kind: str, src_str: str, dst: Path) -> None:
    """Copy an imported artifact into the spec dir and archive the original.

    Runs inside the write stage: a resumed run that redoes the stage
    re-validates and re-copies. The source file is never modified.
    """

    src = Path(src_str)
    archive_name = f"imported-{kind}.md"
    try:
        if kind == "spec":
            validate_import_spec(src)
        else:
            validate_import_plan(src, ctx.settings.phases)
    except SettingsError as exc:
        raise WorkflowAbort(
            f"!! Cannot import the {kind}: {exc}\n"
            "   If an earlier attempt of this run imported it, the "
            f"archived copy is {ctx.archive.art_path(archive_name)}."
        ) from None
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    ctx.archive.archive_snapshot(
        src, archive_name, "workflow", None, ctx.cur_stage, ctx.cur_round
    )
    review = "on" if ctx.settings.import_review else "off"
    ctx.log(f"Imported {kind} from {src} (review: {review})")
```

- [ ] **Step 5: Branch the two authoring stages** — in `src/adversarial_ai_coding/workflow.py`, inside `run_workflow`, replace the non-dual spec stage body:

```python
    else:
        set_spec_roles_from_slot(ctx, "A")
        if begin_stage(ctx, "write-spec", spec_file):
            if ctx.settings.import_spec:
                from .imports import stage_import

                stage_import(ctx, "spec", ctx.settings.import_spec, spec_file)
            else:
                work(
                    ctx,
                    ctx.spec_roles.owner_agent,
                    render_prompt(
                        ctx.prompts_dir,
                        "write-spec",
                        {"SPEC_FILE": str(spec_file), "TASK": task},
                    ),
                )
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
            human_gate_spec(ctx)
            end_stage(ctx)
```

and the plan stage body (`if begin_stage(ctx, "write-implementation-plan", plan_file):`) with:

```python
    if begin_stage(ctx, "write-implementation-plan", plan_file):
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
        if ctx.settings.import_plan:
            from .imports import stage_import

            stage_import(ctx, "plan", ctx.settings.import_plan, plan_file)
        else:
            work(
                ctx,
                ctx.spec_roles.owner_agent,
                render_prompt(
                    ctx.prompts_dir,
                    plan_template,
                    {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)},
                ),
            )
        if not ctx.settings.import_plan or ctx.settings.import_review:
            scope = render_prompt(
                ctx.prompts_dir,
                plan_scope_template,
                {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)},
            )
            review_loop_ref(
                ctx,
                ctx.spec_roles.reviewer_agent,
                ctx.spec_roles.owner_agent,
                scope,
            )
        human_gate_plan(ctx)
        if ctx.settings.phases:
            from .phaseflow import phased_plan_structure_check

            phased_plan_structure_check(ctx, plan_file)
        commit_work(ctx, ctx.spec_roles.owner_agent, "Implementation plan")
        end_stage(ctx)
```

- [ ] **Step 6: Wire the CLI** — in `src/adversarial_ai_coding/cli.py`: add `from .imports import import_preflight` to the import block (after the `.gitops` imports). After the `plan_gate_preflight(settings, stdin_isatty)` line add:

```python
        import_preflight(settings, env, fresh_run=not resume_run)
```

After the `print(f"Task:{task}")` line add:

```python
        if settings.import_spec:
            print(
                f"Importing spec:{settings.import_spec}"
                + (
                    f"  plan:{settings.import_plan}"
                    if settings.import_plan
                    else ""
                )
                + f"  IMPORT_REVIEW={'1' if settings.import_review else '0'}"
            )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_imports.py tests/test_import_integration.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (basic flow untouched when `IMPORT_SPEC` is unset; the stage-order smoke test in `test_finish_pipeline.py` must still pass unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/adversarial_ai_coding/imports.py src/adversarial_ai_coding/workflow.py src/adversarial_ai_coding/cli.py tests/test_imports.py tests/test_import_integration.py
git commit -m "feat(workflow): import external spec and plan into the pipeline

IMPORT_SPEC/IMPORT_PLAN replace only the 'A writes' half of the two
authoring stages: the workflow copies the file into the spec dir,
archives the original as imported-spec.md/imported-plan.md, and the
reviewer loop (unless IMPORT_REVIEW=0), human gates, commits, and the
phased structure check run unchanged. The CLI runs import_preflight
before workspace setup so bad input costs zero AI calls, and prints the
import settings at startup. Stage-time re-validation keeps resumed runs
honest when the source file changed or disappeared."
```

---

### Task 5: Import provenance in pr-body.md

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py` (`finish()` and a new `_artifact_provenance` helper)
- Test: `tests/test_finish_pipeline.py` (append)

**Interfaces:**
- Consumes: `ctx.settings.import_spec/import_plan/import_review` (Task 1).
- Produces: pr-body artifact lines suffixed with `" (imported; cross-reviewed in-run)"` or `" (imported; AI review skipped)"`; the sentence `"Each stage passed deterministic quality gates and cross-review."` only appears when no AI review was skipped.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_finish_pipeline.py`:

```python
def test_pr_body_records_import_provenance_review_off(make_ctx):
    ctx = make_ctx(
        {"IMPORT_SPEC": "ext.md", "IMPORT_REVIEW": "0", "RETRY_ON_LIMIT": "0"}
    )
    finish(ctx, "task", which=lambda name: None, run_gh=None, run_git=None)
    body = (ctx.wf / "pr-body.md").read_text(encoding="utf-8")
    assert "(imported; AI review skipped)" in body
    assert "and cross-review" not in body


def test_pr_body_keeps_cross_review_claim_when_reviewed(make_ctx):
    ctx = make_ctx({"IMPORT_SPEC": "ext.md", "RETRY_ON_LIMIT": "0"})
    finish(ctx, "task", which=lambda name: None, run_gh=None, run_git=None)
    body = (ctx.wf / "pr-body.md").read_text(encoding="utf-8")
    assert "(imported; cross-reviewed in-run)" in body
    assert "Each stage passed deterministic quality gates and cross-review." in body


def test_pr_body_unchanged_without_import(make_ctx):
    ctx = make_ctx({"RETRY_ON_LIMIT": "0"})
    finish(ctx, "task", which=lambda name: None, run_gh=None, run_git=None)
    body = (ctx.wf / "pr-body.md").read_text(encoding="utf-8")
    assert "imported" not in body
    assert "Each stage passed deterministic quality gates and cross-review." in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_finish_pipeline.py -q`
Expected: the two new import tests FAIL (no provenance text); the no-import test PASSES already.

- [ ] **Step 3: Implement** — in `src/adversarial_ai_coding/workflow.py`, add above `finish()`:

```python
def _artifact_provenance(imported: bool, reviewed: bool) -> str:
    if not imported:
        return ""
    if reviewed:
        return " (imported; cross-reviewed in-run)"
    return " (imported; AI review skipped)"
```

In `finish()`, replace the `pr-body.md` write with:

```python
    spec_note = _artifact_provenance(
        bool(ctx.settings.import_spec), ctx.settings.import_review
    )
    plan_note = _artifact_provenance(
        bool(ctx.settings.import_plan), ctx.settings.import_review
    )
    if ctx.settings.import_spec and not ctx.settings.import_review:
        review_note = (
            "Each stage passed deterministic quality gates; imported "
            "artifacts skipped AI review, and later stages were "
            "cross-reviewed. "
        )
    else:
        review_note = (
            "Each stage passed deterministic quality gates and cross-review. "
        )
    (ctx.wf / "pr-body.md").write_text(
        f"## Task\n\n{task}\n\n## Artifacts\n\n"
        f"- Spec with assumptions and open questions:"
        f"`{ctx.spec_dir}/spec.md`{spec_note}\n"
        f"- Implementation plan:`{ctx.spec_dir}/plan.md`{plan_note}\n\n"
        "Generated by adversarial-ai-coding, with original slots "
        f"A={ctx.settings.agent_a} and B={ctx.settings.agent_b}.\n"
        f"Final spec owner/worker: {roles.owner_slot}={roles.owner_agent.name}. "
        f"Reviewer: {roles.reviewer_slot}={roles.reviewer_agent.name}.\n"
        f"{review_note}"
        "Acceptance tests were written by the reviewer and protected against "
        "worker edits.\n",
        encoding="utf-8",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_finish_pipeline.py tests/test_import_integration.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py tests/test_finish_pipeline.py
git commit -m "feat(workflow): record import provenance honestly in pr-body

pr-body.md now marks the spec and plan lines as imported and says
whether the in-run AI review ran. The blanket 'passed cross-review'
sentence is only used when every artifact was actually cross-reviewed;
IMPORT_REVIEW=0 runs state that imported artifacts skipped AI review."
```

---

### Task 6: Format contract, authoring prompt, and docs

**Files:**
- Create: `docs/import-format.md`
- Create: `resources/import-authoring-prompt.md`
- Modify: `README.md`, `README.zh-TW.md`, `docs/how-it-works.md`, `docs/how-it-works.zh-TW.md`
- Test: `tests/test_documentation.py` (append)

**Interfaces:**
- Consumes: validator behavior fixed in Task 2 (the contract documents exactly what the code enforces).
- Produces: `docs/import-format.md` referenced by error messages (`CONTRACT_HINT`).

- [ ] **Step 1: Write the failing test** — append to `tests/test_documentation.py`:

```python
def test_import_mode_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "IMPORT_SPEC" in readme
        assert "IMPORT_PLAN" in readme
        assert "IMPORT_REVIEW" in readme
        assert "import-format" in readme
    contract = _read("docs/import-format.md")
    assert "Assumptions" in contract and "Open Questions" in contract
    assert "- [ ] " in contract
    assert "## Phase" in contract
    assert "IMPORT_REVIEW" in contract
    prompt = _read("resources/import-authoring-prompt.md")
    assert "Assumptions and Open Questions" in prompt
    assert "- [ ] " in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_documentation.py -q`
Expected: FAIL with `FileNotFoundError` on `docs/import-format.md`.

- [ ] **Step 3: Create `docs/import-format.md`**:

```markdown
# Import Format Contract

`IMPORT_SPEC` and `IMPORT_PLAN` let the workflow start from spec/plan
files written outside the tool (for example, from a brainstorming session
in your own AI CLI). The workflow copies the file into the run's spec
directory; the original is never modified. Everything below is enforced
deterministically at startup, before any AI call.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `IMPORT_SPEC=path` | Use this file as `spec.md`; skip the "worker writes the spec" step. |
| `IMPORT_PLAN=path` | Use this file as `plan.md`; skip the "worker writes the plan" step. Requires `IMPORT_SPEC`. |
| `IMPORT_REVIEW=0/1` | Default `1`: imported artifacts still go through the reviewer's review loop. `0` skips the AI review of imported artifacts only. Requires `IMPORT_SPEC`. |

Rules the workflow enforces:

- `IMPORT_PLAN` without `IMPORT_SPEC` is an error.
- `IMPORT_REVIEW` without `IMPORT_SPEC` is an error.
- `IMPORT_SPEC` with `DUAL_SPEC=1` is an error.
- Import settings are frozen into the run's resume snapshot; they cannot
  be changed when resuming.
- `IMPORT_REVIEW=0` never skips human gates, format checks, or commits.

## Spec file (`IMPORT_SPEC`)

- Markdown, UTF-8, non-empty.
- Must contain a heading line (starting with `#`) whose text includes
  both "Assumptions" and "Open Questions" (case-insensitive), for
  example `## Assumptions and Open Questions`. Headless stages cannot
  ask a human questions, so unresolved decisions must be written down.
- Recommended sections (mirroring the in-run spec prompt): feature
  description, testable acceptance criteria, edge cases, and
  out-of-scope items.

## Plan file (`IMPORT_PLAN`), basic mode

- Must contain at least one task line starting exactly with `- [ ] `.
- One task becomes one commit; keep tasks small and independently
  buildable (the per-task gate only compiles).

## Plan file (`IMPORT_PLAN`), phased mode (`PHASES=1`)

- Tasks are grouped into `## Phase N: <title>` sections.
- Each phase needs an observable `Acceptance:` line and at least one
  `- [ ] ` task.
- A trailing `(regression-guard)` on the title marks a phase whose tests
  must pass immediately instead of starting red.

## What still happens in-run

Imported artifacts are reviewed by the reviewer agent (unless
`IMPORT_REVIEW=0`), pass the human gates (`HUMAN_GATE`,
`HUMAN_GATE_PLAN`), are committed by the owner agent, and are archived
(originals as `imported-spec.md` / `imported-plan.md` in the run
archive). `pr-body.md` records what was imported and whether the AI
review ran. See `resources/import-authoring-prompt.md` for a prompt you
can paste into your own tool to produce compliant files.
```

- [ ] **Step 4: Create `resources/import-authoring-prompt.md`**:

```markdown
# Authoring Prompt for External Tools

Paste the prompt below into your own AI tool (Claude Code, Codex, a chat
session) after you have finished clarifying requirements. It produces
files that pass the adversarial-ai-coding import checks in
docs/import-format.md.

---

Write two files from our discussion above.

1. `spec.md` — a specification with these sections:
   - Feature description.
   - Testable acceptance criteria.
   - Edge cases.
   - Out of scope.
   - `## Assumptions and Open Questions` — list every assumption we did
     not settle explicitly. This exact topic must appear as a Markdown
     heading; automation rejects the file without it. If everything is
     settled, write `- none`.

2. `plan.md` — an implementation plan as a Markdown checkbox list. Every
   task line must start exactly with `- [ ] `. One task becomes one git
   commit, so keep tasks small and independently buildable.

   If I tell you the run uses phased mode (PHASES=1), group the tasks
   into `## Phase N: <title>` sections instead; give every phase an
   observable `Acceptance:` line and at least one `- [ ] ` task, and
   mark pure regression phases with `(regression-guard)` at the end of
   the title.

Do not include anything else in the two files.
```

- [ ] **Step 5: Update the READMEs.** In `README.md`, next to the Dual Spec Mode documentation, add a section (and add the three variables to the settings/env table if one lists `DUAL_SPEC`, with the same one-line meanings as the table in `docs/import-format.md`):

```markdown
## Importing an External Spec or Plan

Clarify requirements in whatever interactive tool you prefer, then hand
the finished files to the workflow: `IMPORT_SPEC=path` uses your file as
`spec.md` and skips only the "worker writes the spec" step, and
`IMPORT_PLAN=path` (requires `IMPORT_SPEC`) does the same for `plan.md`.
Imported artifacts still get the reviewer's adversarial review by
default; set `IMPORT_REVIEW=0` to skip that AI review (human gates,
format checks, and commits always run). File requirements and the exact
rules are in [docs/import-format.md](docs/import-format.md), and
[resources/import-authoring-prompt.md](resources/import-authoring-prompt.md)
is a paste-ready prompt for your own tool.
```

In `README.zh-TW.md`, add the equivalent section in Traditional Chinese:

```markdown
## 匯入外部 Spec 或 Plan

先在你慣用的互動工具裡釐清需求,再把成品交給 workflow:
`IMPORT_SPEC=path` 會把你的檔案當作 `spec.md`,只跳過「worker 撰寫
spec」那一步;`IMPORT_PLAN=path`(需同時設定 `IMPORT_SPEC`)對
`plan.md` 做同樣的事。匯入的產物預設仍會經過 reviewer 的對抗式
review;設 `IMPORT_REVIEW=0` 可跳過該 AI review(human gate、格式檢查
與 commit 一律照跑)。檔案格式要求見
[docs/import-format.md](docs/import-format.md),
[resources/import-authoring-prompt.md](resources/import-authoring-prompt.md)
是可直接貼進你自己工具的 prompt。
```

- [ ] **Step 6: Update `docs/how-it-works.md`** — append to stage note 1 ("Write spec"):

```markdown
   With `IMPORT_SPEC=path`, the workflow copies your file in instead of
   asking A to write it; see the import contract in
   [import-format.md](import-format.md).
```

and to stage note 3 ("Write plan"):

```markdown
   With `IMPORT_PLAN=path` the plan is imported the same way; the
   review, gates, and structure checks still run (`IMPORT_REVIEW=0`
   skips only the AI review of imported files).
```

Mirror both sentences in `docs/how-it-works.zh-TW.md` in Traditional Chinese at the same stage notes.

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_documentation.py -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add docs/import-format.md resources/import-authoring-prompt.md README.md README.zh-TW.md docs/how-it-works.md docs/how-it-works.zh-TW.md tests/test_documentation.py
git commit -m "docs: document spec/plan import contract and authoring prompt

Adds docs/import-format.md (the deterministic rules the validators
enforce), resources/import-authoring-prompt.md (paste-ready prompt for
external brainstorming tools), bilingual README sections, and
how-it-works stage notes. test_documentation locks the bilingual
coverage in place."
```

---

### Task 7: Live E2E import scenario

**Files:**
- Modify: `tests/e2e/test_e2e.py` (append one test)

**Interfaces:**
- Consumes: `e2e_base`, `make_fixture_repo`, `verify_gates`, `E2E_DEFAULTS`, `needs_go` from the same file; log lines `Imported spec from` / `Imported plan from` (Task 4); archive names `imported-spec.md` / `imported-plan.md` (Task 4).
- Produces: `test_full_workflow_import_e2e`, gated by `@pytest.mark.e2e` (excluded from default runs; consumes real agent quota).

- [ ] **Step 1: Append the test** — add to `tests/e2e/test_e2e.py`:

```python
IMPORT_SPEC_TEXT = """# Spec: IsPalindrome for strutil

Add IsPalindrome(s string) bool to the strutil package. It reports
whether the string is a palindrome.

## Acceptance criteria

- Compare rune-by-rune, verbatim: no case folding, no Unicode
  normalization; every character (including spaces) is significant.
- The empty string returns true.
- Examples: "" true; "a" true; "abc" false; "Abba" false (case matters);
  "a b a" true; the rune sequence U+4E0A U+6D77 U+6D77 U+4E0A true
  (write that test string with Go Unicode escape sequences, not literal
  CJK characters).
- Unit tests cover the example set.

## Out of scope

- Do not modify the existing Reverse function. No new APIs, no CLI.

## Assumptions and Open Questions

- Assumes the strutil package layout stays unchanged; no open questions.
"""

IMPORT_PLAN_TEXT = """# Plan

- [ ] Add IsPalindrome to strutil with unit tests for the ASCII examples
- [ ] Add the CJK palindrome test using Go Unicode escape sequences
"""


@pytest.mark.e2e
@needs_go
def test_full_workflow_import_e2e():
    base = e2e_base("wf-e2e-imp-")
    print(f"== Import E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    external = base / "external"
    external.mkdir()
    spec = external / "spec.md"
    spec.write_text(IMPORT_SPEC_TEXT, encoding="utf-8")
    plan = external / "plan.md"
    plan.write_text(IMPORT_PLAN_TEXT, encoding="utf-8")

    env = {key: os.environ.get(key, value) for key, value in E2E_DEFAULTS.items()}
    env["IMPORT_SPEC"] = str(spec)
    env["IMPORT_PLAN"] = str(plan)
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
    assert "Imported spec from" in log
    assert "Imported plan from" in log
    assert spec.read_text(encoding="utf-8") == IMPORT_SPEC_TEXT

    spec_copy = next((repo / "specs").glob("*/spec.md")).read_text(
        encoding="utf-8"
    )
    assert "assumptions and open questions" in spec_copy.lower()
    plan_text = next((repo / "specs").glob("*/plan.md")).read_text(
        encoding="utf-8"
    )
    assert "- [x]" in plan_text and "- [ ] " not in plan_text

    strutil = "".join(
        path.read_text(encoding="utf-8")
        for path in (repo / "strutil").glob("*.go")
    )
    assert "func IsPalindrome" in strutil

    run_dir = Path(
        (repo / ".workflow" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-spec.md"))
    assert list(run_dir.glob("*imported-plan.md"))
    verify_gates(repo)
    print(f"Import E2E passed; workspace kept at {base} (delete after inspection)")
```

Note: the repo's `spec.md` copy is checked loosely (the reviewer round may legitimately edit it); only the external original is asserted byte-identical.

- [ ] **Step 2: Verify the default suite still excludes it**

Run: `uv run pytest -q`
Expected: PASS with the new test deselected (it only runs with `-m e2e`).

- [ ] **Step 3: Run the live scenario once** (consumes quota; needs `claude`/`codex` CLIs logged in; workspace goes under `C:/tmp` or `E2E_DIR`)

Run: `uv run pytest tests/e2e/test_e2e.py -m e2e -k import -q -s`
Expected: PASS, with the printed workspace kept for inspection. If reviewer-agent sandbox issues occur on this machine, set `AGENT_B=agy` (or another working reviewer) via env, matching how the other live tests are run here.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_e2e.py
git commit -m "test(e2e): add live import scenario for external spec and plan

One marker-gated live run: imports an externally written IsPalindrome
spec and two-task plan, keeps IMPORT_REVIEW=1, and asserts the write
stages were replaced by imports (log lines and archived originals), the
original files stayed untouched, the plan finished checked off, and the
fixture gates stay green."
```

---

## Self-Review Notes (already applied)

- Spec coverage: interface rules (Task 2), stage flow (Task 4), validation and preflight (Task 2), resume semantics (Task 3 + resume integration test in Task 4), provenance (Task 5), companion deliverables (Task 6), testing pyramid (Tasks 2-4 unit/integration, Task 7 E2E). The reviewer-count assertions (4 vs 2) encode "review runs by default / skipped only for imported artifacts".
- The `IMPORT_REVIEW` echo, `CONTRACT_HINT` doc path, archive names, and log lines are each defined once in Task 4 and consumed verbatim by Tasks 6-7.
