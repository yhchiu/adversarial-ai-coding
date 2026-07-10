# Python Port — Plan 1 of 6: Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the uv-managed Python package and port the pure-function layer (settings, prompt templates, slug/CSV/metrics helpers, rate-limit parsers) with their unit tests, plus a dual-track CI job.

**Architecture:** This is plan 1 of a 6-plan series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. Later plans:
(2) engines + ratelimit retry policy, (3) runstate + archive I/O,
(4) gates + review + gitops, (5) workflow + dual_spec + cli,
(6) CI cutover + real-run acceptance + bash removal. Each later plan is
written after the previous one executes. This plan creates the package
skeleton and the modules that are pure functions of their inputs, ported
1:1 from the frozen bash reference `adversarial-ai-coding.sh`.

**Tech Stack:** Python 3.12+, uv (package manager + build backend), pytest (dev-only dependency). No runtime dependencies.

## Global Constraints

- Python floor: `requires-python = ">=3.12"` (spec: "Python 3.12+").
- Runtime dependencies: none — stdlib only. pytest is dev-group only.
- The bash files are FROZEN: never edit `adversarial-ai-coding.sh`,
  `tests/helpers.test.sh`, `tests/resume.test.sh`, `tests/e2e/run.sh`.
  They are the behavior reference; each task cites exact line numbers.
- Behavior parity: when this plan says "port", the Python function must
  produce the same observable output as the cited bash lines for the same
  inputs. The ported test assertions encode that.
- All commands run from the repo root `C:\Project\adversarial-ai-coding`
  (Windows; use Git Bash or PowerShell — commands below are shell-neutral).
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By
  trailer.
- Run tests with `uv run pytest -q`. Every task must leave the whole suite
  green on the developer's Windows machine.
- Existing bash test suites keep passing untouched (they don't overlap
  with pytest; do not modify them).

## File Structure

Created in this plan:

```
pyproject.toml                          # Task 1
uv.lock                                 # Task 1 (generated, committed)
src/adversarial_ai_coding/__init__.py   # Task 1
src/adversarial_ai_coding/config.py     # Task 1
src/adversarial_ai_coding/prompts.py    # Task 2
src/adversarial_ai_coding/archive.py    # Task 3 (pure helpers only; I/O parts arrive in plan 3)
src/adversarial_ai_coding/ratelimit.py  # Task 4 (parsers only; retry loop arrives in plan 2)
tests/test_config.py                    # Task 1
tests/test_prompts.py                   # Task 2
tests/test_archive_helpers.py           # Task 3
tests/test_ratelimit_parsing.py         # Task 4
.github/workflows/ci.yml                # Task 5 (modified: add python job)
```

---

### Task 1: Package scaffold + `config.py` (Settings)

Bash reference: `adversarial-ai-coding.sh:49-64` (`alias_env_or_default`),
`adversarial-ai-coding.sh:285-330` (settings block).
Bash tests ported: `tests/helpers.test.sh:63-71` (agent aliases).

**Files:**
- Create: `pyproject.toml`
- Create: `src/adversarial_ai_coding/__init__.py`
- Create: `src/adversarial_ai_coding/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `config.SettingsError(Exception)` — raised for any user-fixable
    configuration problem. Later plans catch it in `cli.py` only.
  - `config.alias_env_or_default(env: Mapping[str, str], preferred: str, legacy: str, default: str) -> str`
  - `config.Settings` — frozen dataclass; field names and types exactly as
    in the code below. All later plans receive a `Settings` instance as a
    parameter; nothing reads `os.environ` outside `Settings.from_env`.
  - `config.Settings.from_env(env: Mapping[str, str], run_id: str, snapshot: Mapping[str, str] | None = None) -> Settings`
    — `env` wins, then `snapshot` (the resume snapshot, plan 3), then the
    default. `snapshot` keys use the bash variable names (`ENGINE_A`,
    `MAX_ROUNDS`, ...). `NOTIFY_CMD` and `RETRY_*` are never read from
    `snapshot` (bash line 307: deliberately not persisted).

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "adversarial-ai-coding"
version = "0.1.0"
description = "Adversarial two-AI coding workflow"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["uv_build>=0.7,<1"]
build-backend = "uv_build"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

No `[project.scripts]` yet — the console script arrives with `cli.py` in
plan 5 (YAGNI: declaring it now would point at a module that doesn't
exist).

- [ ] **Step 2: Write the package init**

`src/adversarial_ai_coding/__init__.py`:

```python
"""Adversarial two-AI coding workflow (Python port of adversarial-ai-coding.sh)."""

__version__ = "0.1.0"
```

- [ ] **Step 3: Sync the environment and verify pytest runs**

Run: `uv sync`
Expected: creates `.venv` and `uv.lock`, installs the package editable plus pytest.

Run: `uv run pytest -q`
Expected: `no tests ran` (exit code 5 is fine at this step).

- [ ] **Step 4: Write the failing tests**

`tests/test_config.py`:

```python
"""Ports tests/helpers.test.sh:63-71 (agent aliases) and pins the bash
settings defaults from adversarial-ai-coding.sh:285-330."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError, alias_env_or_default


def make(env=None, run_id="20260710-120000", snapshot=None):
    return Settings.from_env(env or {}, run_id=run_id, snapshot=snapshot)


def test_defaults_match_bash():
    s = make()
    assert s.engine_a == "claude"
    assert s.engine_b == "codex"
    assert s.model_a == ""
    assert s.model_b == ""
    assert s.max_rounds == 3
    assert s.auto_branch is True
    assert s.use_worktree is False
    assert s.human_gate is True
    assert s.dual_spec is False
    assert s.open_pr is False
    assert s.notify_cmd == ""
    assert s.retry_on_limit is True
    assert s.retry_max == 6
    assert s.retry_base_wait == 300
    assert s.retry_max_wait == 3600
    assert s.retry_max_reset_wait == 21600
    assert s.tools == "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"
    assert s.spec_dir == "specs/20260710-120000"
    assert s.runs_dir == ".workflow/runs"


def test_agent_a_alias_configures_slot_a():
    # helpers.test.sh: "agent aliases:AGENT_A configures slot A"
    assert make({"AGENT_A": "codex", "AGENT_B": "claude"}).engine_a == "codex"


def test_legacy_engine_vars_still_work():
    s = make({"ENGINE_A": "agy", "ENGINE_B": "claude"})
    assert (s.engine_a, s.engine_b) == ("agy", "claude")


def test_conflicting_alias_fails_fast():
    # helpers.test.sh: "agent aliases:conflicting AGENT_A and ENGINE_A fail fast"
    with pytest.raises(SettingsError, match="Conflicting AGENT_A and ENGINE_A"):
        make({"AGENT_A": "claude", "ENGINE_A": "codex"})


def test_matching_alias_values_are_not_a_conflict():
    assert make({"AGENT_A": "codex", "ENGINE_A": "codex"}).engine_a == "codex"


def test_custom_agent_args_alias():
    # helpers.test.sh: "agent aliases:custom agent uses AGENT_A_ARGS"
    s = make({"AGENT_A": "custom-agent", "AGENT_A_ARGS": "--model custom --flag"})
    assert s.engine_a_args == "--model custom --flag"


def test_conflicting_args_alias_fails_fast():
    with pytest.raises(SettingsError, match="Conflicting AGENT_B_ARGS and ENGINE_B_ARGS"):
        make({"AGENT_B_ARGS": "--x", "ENGINE_B_ARGS": "--y"})


def test_snapshot_supplies_resumed_defaults_and_env_wins():
    snap = {"ENGINE_A": "agy", "MAX_ROUNDS": "5", "TOOLS": "Bash(ls *)"}
    s = make({}, snapshot=snap)
    assert s.engine_a == "agy"
    assert s.max_rounds == 5
    assert s.tools == "Bash(ls *)"
    s = make({"AGENT_A": "codex", "MAX_ROUNDS": "2"}, snapshot=snap)
    assert s.engine_a == "codex"
    assert s.max_rounds == 2


def test_notify_cmd_and_retry_never_come_from_snapshot():
    # Bash line 307: NOTIFY_CMD deliberately not persisted; RETRY_* likewise.
    s = make({}, snapshot={"NOTIFY_CMD": "notify-send", "RETRY_MAX": "99"})
    assert s.notify_cmd == ""
    assert s.retry_max == 6


def test_spec_dir_uses_run_id_by_default():
    assert make(run_id="abc-123").spec_dir == "specs/abc-123"
    assert make({"SPEC_DIR": "myspecs"}).spec_dir == "myspecs"


def test_non_integer_max_rounds_raises():
    with pytest.raises(SettingsError, match="MAX_ROUNDS"):
        make({"MAX_ROUNDS": "three"})


def test_alias_env_or_default_direct():
    assert alias_env_or_default({}, "A", "B", "d") == "d"
    assert alias_env_or_default({"B": "legacy"}, "A", "B", "d") == "legacy"
    assert alias_env_or_default({"A": "new", "B": "new"}, "A", "B", "d") == "new"
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.config'`

- [ ] **Step 6: Write `src/adversarial_ai_coding/config.py`**

```python
"""Environment-variable settings.

Port of the bash Settings section: adversarial-ai-coding.sh:49-64
(alias_env_or_default) and 285-330 (defaults). Resolution order for
persisted keys: environment, then the resume snapshot, then the default —
matching bash "${VAR:-${RESUMED_VAR:-default}}".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_TOOLS = "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"


class SettingsError(Exception):
    """A configuration problem the user must fix before the run starts."""


def alias_env_or_default(
    env: Mapping[str, str], preferred: str, legacy: str, default: str
) -> str:
    preferred_value = env.get(preferred, "")
    legacy_value = env.get(legacy, "")
    if preferred_value and legacy_value and preferred_value != legacy_value:
        raise SettingsError(
            f"Conflicting {preferred} and {legacy}; set only one or use the same value."
        )
    return preferred_value or legacy_value or default


def _to_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer, got: {raw}") from None


@dataclass(frozen=True)
class Settings:
    engine_a: str
    engine_b: str
    model_a: str
    model_b: str
    claude_args: str
    codex_args: str
    agy_args: str
    engine_a_args: str
    engine_b_args: str
    max_rounds: int
    auto_branch: bool
    use_worktree: bool
    human_gate: bool
    dual_spec: bool
    open_pr: bool
    notify_cmd: str
    retry_on_limit: bool
    retry_max: int
    retry_base_wait: int
    retry_max_wait: int
    retry_max_reset_wait: int
    tools: str
    spec_dir: str
    runs_dir: str

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        run_id: str,
        snapshot: Mapping[str, str] | None = None,
    ) -> "Settings":
        snap = snapshot or {}

        def persisted(key: str, default: str) -> str:
            return env.get(key) or snap.get(key) or default

        return cls(
            engine_a=alias_env_or_default(
                env, "AGENT_A", "ENGINE_A", snap.get("ENGINE_A") or "claude"
            ),
            engine_b=alias_env_or_default(
                env, "AGENT_B", "ENGINE_B", snap.get("ENGINE_B") or "codex"
            ),
            model_a=persisted("MODEL_A", ""),
            model_b=persisted("MODEL_B", ""),
            claude_args=persisted("CLAUDE_ARGS", ""),
            codex_args=persisted("CODEX_ARGS", ""),
            agy_args=persisted("AGY_ARGS", ""),
            engine_a_args=alias_env_or_default(
                env, "AGENT_A_ARGS", "ENGINE_A_ARGS", snap.get("ENGINE_A_ARGS") or ""
            ),
            engine_b_args=alias_env_or_default(
                env, "AGENT_B_ARGS", "ENGINE_B_ARGS", snap.get("ENGINE_B_ARGS") or ""
            ),
            max_rounds=_to_int("MAX_ROUNDS", persisted("MAX_ROUNDS", "3")),
            auto_branch=persisted("AUTO_BRANCH", "1") == "1",
            use_worktree=persisted("USE_WORKTREE", "0") == "1",
            human_gate=persisted("HUMAN_GATE", "1") == "1",
            dual_spec=persisted("DUAL_SPEC", "0") == "1",
            open_pr=persisted("OPEN_PR", "0") == "1",
            # Deliberately never from the snapshot (bash line 307): provide per attempt.
            notify_cmd=env.get("NOTIFY_CMD", ""),
            retry_on_limit=env.get("RETRY_ON_LIMIT", "1") == "1",
            retry_max=_to_int("RETRY_MAX", env.get("RETRY_MAX", "6")),
            retry_base_wait=_to_int("RETRY_BASE_WAIT", env.get("RETRY_BASE_WAIT", "300")),
            retry_max_wait=_to_int("RETRY_MAX_WAIT", env.get("RETRY_MAX_WAIT", "3600")),
            retry_max_reset_wait=_to_int(
                "RETRY_MAX_RESET_WAIT", env.get("RETRY_MAX_RESET_WAIT", "21600")
            ),
            tools=persisted("TOOLS", DEFAULT_TOOLS),
            spec_dir=persisted("SPEC_DIR", f"specs/{run_id}"),
            runs_dir=env.get("RUNS_DIR") or ".workflow/runs",
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock src/adversarial_ai_coding/__init__.py src/adversarial_ai_coding/config.py tests/test_config.py
git commit -m "feat: scaffold python package with Settings port

Add the uv-managed package skeleton (pyproject, src layout, pytest dev
dependency) and port the bash Settings section to a frozen dataclass:
env wins, then the resume snapshot, then defaults, with the same
AGENT_*/ENGINE_* alias conflict detection. NOTIFY_CMD and RETRY_* are
never read from the snapshot, matching the bash behavior. Tests port
the agent-alias assertions from helpers.test.sh and pin every default."
```

---

### Task 2: `prompts.py` (template loading and rendering)

Bash reference: `adversarial-ai-coding.sh:507-535` (prompt helpers),
`adversarial-ai-coding.sh:1008-1012` (SCRIPT_DIR / RESOURCES_DIR / PROMPTS_DIR defaults).
Bash tests ported: `tests/helpers.test.sh:125-171` (prompt file handoff, templates).

**Files:**
- Create: `src/adversarial_ai_coding/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `prompts.REPO_ROOT: Path` — repo root derived from the package location.
  - `prompts.PromptTemplateError(Exception)`
  - `prompts.default_prompts_dir(env: Mapping[str, str]) -> Path` — honors
    `PROMPTS_DIR`, then `RESOURCES_DIR`, then `REPO_ROOT / "resources" / "prompts"`.
  - `prompts.prompt_template_path(prompts_dir: Path, name: str) -> Path`
  - `prompts.read_prompt_template(prompts_dir: Path, name: str) -> str`
  - `prompts.render_prompt(prompts_dir: Path, name: str, replacements: Mapping[str, str]) -> str`
    — replaces each `{{KEY}}`; result always ends with exactly one newline
    (bash: `printf '%s\n'` after `$(...)` stripping).
  - `prompts.prompt_file_instruction(artifact_path: str) -> str`

- [ ] **Step 1: Write the failing tests**

`tests/test_prompts.py`:

```python
"""Ports tests/helpers.test.sh:125-171 (prompt handoff and templates)."""

import pytest

from adversarial_ai_coding.prompts import (
    REPO_ROOT,
    PromptTemplateError,
    default_prompts_dir,
    prompt_file_instruction,
    render_prompt,
)


def test_default_prompts_dir_lives_under_resources():
    # helpers.test.sh: "prompts:default directory lives under resources"
    assert default_prompts_dir({}) == REPO_ROOT / "resources" / "prompts"
    assert default_prompts_dir({}).is_dir()


def test_default_prompts_dir_env_overrides(tmp_path):
    assert default_prompts_dir({"PROMPTS_DIR": str(tmp_path)}) == tmp_path
    assert default_prompts_dir({"RESOURCES_DIR": str(tmp_path)}) == tmp_path / "prompts"


def test_render_prompt_replaces_placeholders(tmp_path):
    # helpers.test.sh: "prompts:render_prompt replaces placeholders"
    (tmp_path / "sample.md").write_text(
        "Hello {{NAME}}.\nPath: {{PATH}}\nMessage:\n{{MESSAGE}}\n", encoding="utf-8"
    )
    out = render_prompt(
        tmp_path,
        "sample",
        {"NAME": "worker", "PATH": "specs/run/spec.md", "MESSAGE": "line one\nline two"},
    )
    assert out == "Hello worker.\nPath: specs/run/spec.md\nMessage:\nline one\nline two\n"


def test_missing_template_fails_and_names_the_file(tmp_path):
    # helpers.test.sh: "prompts:missing template fails" + "names the file"
    with pytest.raises(PromptTemplateError, match=r"prompt template not found:.*missing\.md"):
        render_prompt(tmp_path, "missing", {})


def test_prompt_file_instruction_points_at_the_file():
    # helpers.test.sh: "prompt_file_instruction:points engine at prompt file"
    out = prompt_file_instruction(".workflow/runs/test/001-worker-prompt.md")
    assert "Read the full workflow prompt" in out
    assert ".workflow/runs/test/001-worker-prompt.md" in out


def test_real_repo_templates_render():
    # Every template shipped in resources/prompts must load through this module.
    prompts_dir = default_prompts_dir({})
    names = sorted(p.stem for p in prompts_dir.glob("*.md"))
    assert "review" in names
    for name in names:
        assert render_prompt(prompts_dir, name, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.prompts'`

- [ ] **Step 3: Write `src/adversarial_ai_coding/prompts.py`**

```python
"""Workflow prompt templates.

Port of adversarial-ai-coding.sh:507-535 and the resource-path defaults at
1008-1012. Templates stay in resources/prompts as plain markdown; this
module only locates, reads, and renders them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

PACKAGE_DIR = Path(__file__).resolve().parent
# src/adversarial_ai_coding -> src -> repo root. The tool runs from a repo
# checkout (never released as a wheel), so this is the SCRIPT_DIR equivalent.
REPO_ROOT = PACKAGE_DIR.parents[1]


class PromptTemplateError(Exception):
    """A workflow prompt template is missing or unreadable."""


def default_prompts_dir(env: Mapping[str, str]) -> Path:
    resources = Path(env.get("RESOURCES_DIR") or REPO_ROOT / "resources")
    return Path(env.get("PROMPTS_DIR") or resources / "prompts")


def prompt_template_path(prompts_dir: Path, name: str) -> Path:
    return prompts_dir / f"{name}.md"


def read_prompt_template(prompts_dir: Path, name: str) -> str:
    path = prompt_template_path(prompts_dir, name)
    if not path.is_file():
        raise PromptTemplateError(
            f"(workflow prompt template not found:{path}; "
            "keep resources/prompts with the script or set PROMPTS_DIR)"
        )
    return path.read_text(encoding="utf-8")


def render_prompt(
    prompts_dir: Path, name: str, replacements: Mapping[str, str]
) -> str:
    text = read_prompt_template(prompts_dir, name)
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    # bash: command substitution strips trailing newlines, printf '%s\n' adds one.
    return text.rstrip("\n") + "\n"


def prompt_file_instruction(artifact_path: str) -> str:
    return (
        "Read the full workflow prompt from this repository file "
        f"and follow it exactly: {artifact_path}\n"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_prompts.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/prompts.py tests/test_prompts.py
git commit -m "feat: port prompt template loading and rendering

Locate resources/prompts relative to the repo checkout with PROMPTS_DIR
and RESOURCES_DIR overrides, read templates with an actionable error
when one is missing, and render {{KEY}} placeholders with the same
trailing-newline behavior as the bash version. A repo-wide test renders
every shipped template through the new module."
```

---

### Task 3: `archive.py` pure helpers (slug, timestamps, CSV, metrics summary)

Bash reference: `adversarial-ai-coding.sh:377-398` (generated_at, safe_slug),
`424-452` (csv_row, write_csv_row, metrics_summary).
Bash tests ported: `tests/helpers.test.sh:301-322` (metric CSV shape,
metrics_summary quoted-row regression).

Scope note: only the pure text/CSV helpers land here. `art_path`,
`write_meta`, `archive_snapshot`, and the run-directory functions need run
state and arrive in plan 3, in this same module.

**Files:**
- Create: `src/adversarial_ai_coding/archive.py`
- Test: `tests/test_archive_helpers.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `archive.generated_at(now: datetime | None = None) -> str` — local time
    formatted `%Y-%m-%dT%H:%M:%S%z`. (The bash WF_NOW env hook is replaced
    by the explicit parameter; tests pass a fixed datetime.)
  - `archive.safe_slug(s: str) -> str`
  - `archive.csv_row(fields: Sequence[object]) -> str` — every field quoted,
    embedded quotes doubled, "\n"-terminated.
  - `archive.write_csv_row(path: Path, fields: Sequence[object]) -> None` — appends.
  - `archive.METRICS_HEADER: list[str]` — exactly
    `["run_id", "stage", "role", "engine", "round", "duration_s", "cost_usd", "model", "model_args", "generated_at"]`.
  - `archive.metrics_summary(path: Path) -> str` — per-stage lines formatted
    `"  %-14s AI calls %d, review rounds %d, %d seconds, $%.4f"`; empty
    string when the file is missing.

- [ ] **Step 1: Write the failing tests**

`tests/test_archive_helpers.py`:

```python
"""Ports the pure-helper assertions from tests/helpers.test.sh:301-328."""

from datetime import datetime, timedelta, timezone

from adversarial_ai_coding.archive import (
    METRICS_HEADER,
    csv_row,
    generated_at,
    metrics_summary,
    safe_slug,
    write_csv_row,
)


def test_generated_at_format_matches_bash():
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    assert generated_at(fixed) == "2026-01-02T03:04:05+0800"


def test_safe_slug_replaces_separators():
    assert safe_slug("a/b\\c d:e;f|g<h>i\"j'k") == "a-b-c-d-e-f-g-h-i-j-k"
    assert safe_slug("plain-name_1.txt") == "plain-name_1.txt"


def test_csv_row_quotes_every_field_and_escapes_quotes():
    # helpers.test.sh: "metric:CSV escaping preserves model_args with comma and quotes"
    row = csv_row(["a", 'x"y', "1,2"])
    assert row == '"a","x""y","1,2"\n'


def test_metrics_header_matches_bash():
    # helpers.test.sh: "metric:CSV header is correct"
    assert (
        ",".join(METRICS_HEADER)
        == "run_id,stage,role,engine,round,duration_s,cost_usd,model,model_args,generated_at"
    )


def _write_metrics(path, rows):
    path.write_text(",".join(METRICS_HEADER) + "\n", encoding="utf-8")
    for row in rows:
        write_csv_row(path, row)


def test_metrics_summary_sums_despite_quoted_fields(tmp_path):
    # helpers.test.sh: "metrics_summary:seconds/cost/max round" regression.
    csv = tmp_path / "metrics.csv"
    ts = "2026-01-02T03:04:05+0800"
    _write_metrics(
        csv,
        [
            ["run1", "stageX", "worker", "claude", 1, 12, 0.05, "", '-c model="x,y"', ts],
            ["run1", "stageX", "worker", "claude", 3, 8, 0.10, "", "", ts],
        ],
    )
    out = metrics_summary(csv)
    assert "stageX" in out
    assert "AI calls 2" in out
    assert "review rounds 3" in out
    assert "20 seconds" in out
    assert "$0.1500" in out


def test_metrics_summary_empty_cost_counts_as_zero(tmp_path):
    csv = tmp_path / "metrics.csv"
    _write_metrics(csv, [["run1", "s1", "reviewer", "codex", 2, 30, "", "", "", "t"]])
    assert "$0.0000" in metrics_summary(csv)


def test_metrics_summary_missing_file_is_empty(tmp_path):
    assert metrics_summary(tmp_path / "absent.csv") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_archive_helpers.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.archive'`

- [ ] **Step 3: Write `src/adversarial_ai_coding/archive.py`**

```python
"""Run archive helpers — pure text/CSV parts.

Port of adversarial-ai-coding.sh:377-398 (generated_at, safe_slug) and
424-452 (csv_row, write_csv_row, metrics_summary). The artifact and
run-directory I/O functions join this module in plan 3.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

METRICS_HEADER = [
    "run_id", "stage", "role", "engine", "round",
    "duration_s", "cost_usd", "model", "model_args", "generated_at",
]

_SLUG_UNSAFE = set("/\\ :;|<>\"'")


def generated_at(now: datetime | None = None) -> str:
    dt = now if now is not None else datetime.now().astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def safe_slug(s: str) -> str:
    return "".join("-" if c in _SLUG_UNSAFE else c for c in s)


def csv_row(fields: Sequence[object]) -> str:
    quoted = ('"' + str(f).replace('"', '""') + '"' for f in fields)
    return ",".join(quoted) + "\n"


def write_csv_row(path: Path, fields: Sequence[object]) -> None:
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write(csv_row(fields))


def _num(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return 0.0  # awk treats non-numeric fields as 0


def metrics_summary(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    stats: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if len(row) < 7:
            continue
        st = stats.setdefault(row[1], {"calls": 0, "round": 0, "secs": 0.0, "cost": 0.0})
        st["calls"] += 1
        st["round"] = max(st["round"], _num(row[4]))
        st["secs"] += _num(row[5])
        st["cost"] += _num(row[6])
    lines = [
        "  %-14s AI calls %d, review rounds %d, %d seconds, $%.4f"
        % (stage, st["calls"], int(st["round"]), int(st["secs"]), st["cost"])
        for stage, st in stats.items()
    ]
    return "\n".join(lines) + ("\n" if lines else "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_archive_helpers.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/archive.py tests/test_archive_helpers.py
git commit -m "feat: port slug, timestamp, and CSV metrics helpers

Port generated_at, safe_slug, csv_row, write_csv_row, and
metrics_summary from the bash script. The CSV writer quotes every field
and doubles embedded quotes so model_args can contain commas; the
summary parses with the csv module and reproduces the bash awk output
format, including the quoted-row summing regression case."
```

---

### Task 4: `ratelimit.py` parsers (quota detection, reset-time parsing)

Bash reference: `adversarial-ai-coding.sh:702-769` (is_rate_limited,
RESET_SANITY_MAX, parse_reset_wait, human_duration).
Bash tests ported: `tests/helpers.test.sh:576-649`.

**Files:**
- Create: `src/adversarial_ai_coding/ratelimit.py`
- Test: `tests/test_ratelimit_parsing.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces:
  - `ratelimit.RESET_SANITY_MAX = 2_592_000`
  - `ratelimit.is_rate_limited(path: Path) -> bool`
  - `ratelimit.parse_reset_wait(path: Path, now: int | None = None) -> int | None`
    — wait seconds, or `None` when nothing parseable (bash: empty output).
    `now` is a Unix epoch; defaults to current time. Local-time semantics
    match bash `date -d`.
  - `ratelimit.human_duration(seconds: int) -> str`
  - Plan 2 adds the retry policy loop around these parsers in this module.

- [ ] **Step 1: Write the failing tests**

`tests/test_ratelimit_parsing.py`:

```python
"""Ports tests/helpers.test.sh:576-649 (rate-limit detection, reset parser).

The bash suite derives clock strings from the real current time; here we
fix `now` instead, which makes the same cases deterministic.
"""

from datetime import datetime

import pytest

from adversarial_ai_coding.ratelimit import (
    human_duration,
    is_rate_limited,
    parse_reset_wait,
)

CLAUDE_429 = (
    '{"type":"result","subtype":"success","is_error":true,'
    '"api_error_status":429,"result":"You\'ve hit your session limit - '
    'resets 10:50am (Asia/Taipei)"}\n'
)
CODEX_429 = (
    'ERROR: {"type":"error","status":429,"error":{"type":"rate_limit_exceeded",'
    '"message":"Rate limit reached for gpt-5.5. Please try again in 90s."}}\n'
)
# Real codex CLI quota message, wrapped across lines exactly as the CLI prints it.
CODEX_QUOTA = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit\n"
    "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Jul\n"
    "14th, 2026 7:23 PM.\n"
)


def out_file(tmp_path, text):
    p = tmp_path / "engine-out.txt"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "sample",
    [
        CLAUDE_429,
        "HTTP 429 Too Many Requests\n",
        CODEX_429,
        "You've reached your usage limit.\n",
        CODEX_QUOTA,
    ],
    ids=["claude-429-json", "too-many-requests", "codex-429-json", "reached-usage", "codex-quota-wrapped"],
)
def test_rate_limit_samples_detected(tmp_path, sample):
    assert is_rate_limited(out_file(tmp_path, sample))


def test_ordinary_error_is_not_misclassified(tmp_path):
    p = out_file(tmp_path, "strutil_test.go:47:14: undefined: IsPalindrome\n")
    assert not is_rate_limited(p)


def test_missing_file_is_not_rate_limited(tmp_path):
    assert not is_rate_limited(tmp_path / "nothere.txt")


NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())  # local 09:00


def test_clock_two_hours_ahead_waits_2h_plus_buffer(tmp_path):
    p = out_file(tmp_path, "You have hit your session limit - resets 11:00am (Asia/Taipei)\n")
    assert parse_reset_wait(p, NOW) == 7200 + 120


def test_past_clock_time_rolls_to_tomorrow(tmp_path):
    p = out_file(tmp_path, "resets 8:00am\n")
    assert parse_reset_wait(p, NOW) == 86400 - 3600 + 120  # 82920


def test_pm_clock_parses(tmp_path):
    p = out_file(tmp_path, "resets 12:30pm\n")
    assert parse_reset_wait(p, NOW) == int(3.5 * 3600) + 120


def test_no_reset_info_returns_none_for_backoff(tmp_path):
    assert parse_reset_wait(out_file(tmp_path, "no reset info here\n"), NOW) is None


def test_missing_file_returns_none(tmp_path):
    assert parse_reset_wait(tmp_path / "nothere.txt", NOW) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CODEX_429, 90 + 30),
        ("Rate limit reached. Please try again in 2 minutes.\n", 120 + 30),
        ("usage cap, try again in 3 hours\n", 3 * 3600 + 30),
        ("try again in 12 hours\n", 12 * 3600 + 30),  # parsed as-is; caller applies policy
        ("please try again in 250 ms\n", 1 + 30),
    ],
    ids=["90s", "2min", "3h", "12h-as-is", "ms"],
)
def test_relative_durations(tmp_path, text, expected):
    assert parse_reset_wait(out_file(tmp_path, text), NOW) == expected


def test_beyond_30_days_hits_sanity_guard(tmp_path):
    assert parse_reset_wait(out_file(tmp_path, "try again in 900 hours\n"), NOW) is None


def test_absolute_date_across_line_break(tmp_path):
    # helpers.test.sh: "reset parser:real codex 'try again at <date>' across a line break"
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = out_file(tmp_path, CODEX_QUOTA)
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_absolute_date_already_elapsed_short_buffer(tmp_path):
    p = out_file(tmp_path, "try again at Jan 2nd, 2020 7:23 PM.\n")
    assert parse_reset_wait(p, NOW) == 30


def test_human_duration():
    assert human_duration(11520) == "3h 12m"
    assert human_duration(2700) == "45m"
    assert human_duration(59) == "0m"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ratelimit_parsing.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.ratelimit'`

- [ ] **Step 3: Write `src/adversarial_ai_coding/ratelimit.py`**

```python
"""Quota/rate-limit detection and reset-time parsing.

Port of adversarial-ai-coding.sh:702-769. Only parsing lives here; the
caller decides whether a wait is worth sitting through. Plan 2 adds the
retry loop.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

RESET_SANITY_MAX = 2_592_000  # 30 days; beyond this is a parsing artefact.

_RATE_LIMIT = re.compile(
    r'"api_error_status": *429'
    r"|(?:hit|reached) your (?:session|usage|weekly|rate) limit"
    r"|rate.?limit"
    r"|too many requests"
    r"|status.?429",
    re.IGNORECASE,
)

_CLOCK = re.compile(r"resets +(\d{1,2}):(\d{2}) ?([ap])m", re.IGNORECASE)
_RELATIVE = re.compile(
    r"try again in (\d+)(?:\.\d+)? ?"
    r"(ms|milliseconds?|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(
    r"(?:try again at|resets at|resets on) +([A-Za-z]{3,9}) +(\d{1,2})"
    r"(?:st|nd|rd|th)?,? +(\d{4}),? +(\d{1,2}):(\d{2}) *([ap])\.?m",
    re.IGNORECASE,
)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_rate_limited(path: Path) -> bool:
    text = _read(path)
    return text is not None and _RATE_LIMIT.search(text) is not None


def _hour24(hour12: int, ampm: str) -> int:
    h = hour12 % 12
    return h + 12 if ampm.lower() == "p" else h


def parse_reset_wait(path: Path, now: int | None = None) -> int | None:
    text = _read(path)
    if text is None:
        return None
    if now is None:
        now = int(datetime.now().timestamp())
    # Agents wrap their output, so a timestamp can straddle a newline.
    norm = re.sub(r"[ \t\r\n]+", " ", text)

    # Format 1, Claude: "resets 10:50am" -> next occurrence, plus 120s buffer.
    m = _CLOCK.search(norm)
    if m:
        base = datetime.fromtimestamp(now)
        target = base.replace(
            hour=_hour24(int(m.group(1)), m.group(3)),
            minute=int(m.group(2)),
            second=0,
            microsecond=0,
        )
        if int(target.timestamp()) <= now:
            target += timedelta(days=1)
        wait = int(target.timestamp()) - now + 120
        return wait if wait <= RESET_SANITY_MAX else None

    # Format 2, OpenAI/Codex: "try again in 20s / 2 minutes / 3 hours" + 30s buffer.
    m = _RELATIVE.search(norm)
    if m:
        num = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "ms" or unit.startswith("millisecond"):
            wait = 1
        elif unit == "s" or unit.startswith(("sec", "second")):
            wait = num
        elif unit == "m" or unit.startswith(("min", "minute")):
            wait = num * 60
        else:  # h, hr(s), hour(s)
            wait = num * 3600
        wait += 30
        return wait if wait <= RESET_SANITY_MAX else None

    # Format 3, Codex quota: "try again at Jul 14th, 2026 7:23 PM" + 30s buffer.
    m = _ABSOLUTE.search(norm)
    if m:
        month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
        try:
            parsed_month = datetime.strptime(month[:3].title(), "%b").month
        except ValueError:
            return None
        target = datetime(
            year, parsed_month, day,
            _hour24(int(m.group(4)), m.group(6)), int(m.group(5)),
        )
        target_epoch = int(target.timestamp())
        if target_epoch <= now:
            return 30  # Already elapsed; retry after a short buffer.
        wait = target_epoch - now + 30
        return wait if wait <= RESET_SANITY_MAX else None

    return None


def human_duration(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 60}m"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ratelimit_parsing.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all tests from Tasks 1-4 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/ratelimit.py tests/test_ratelimit_parsing.py
git commit -m "feat: port rate-limit detection and reset-time parsing

Port is_rate_limited, parse_reset_wait, and human_duration. The parser
handles the three known formats: Claude clock times with day rollover
and a 120s buffer, relative durations with a 30s buffer, and absolute
codex quota dates with ordinal suffixes wrapped across lines. The
30-day sanity guard and the caller-decides policy stay unchanged.
Tests fix the epoch instead of deriving clock strings from the real
time, making the ported bash cases deterministic."
```

---

### Task 5: CI dual track (pytest job alongside the bash jobs)

Bash reference: none (infrastructure). Spec section: "Testing" — "CI runs
both matrices (frozen Bash suites and pytest) on ubuntu and windows during
the migration."

**Files:**
- Modify: `.github/workflows/ci.yml` (append a job; do not touch the `test` and `test-windows` jobs)

**Interfaces:**
- Consumes: `pyproject.toml` + `uv.lock` from Task 1.
- Produces: a `test-python` CI job later plans extend. Note: the repo has
  no git remote yet, so this job runs for real only once the repo is
  pushed; local validation is the yaml parse plus the same commands run
  locally.

- [ ] **Step 1: Append the job to `.github/workflows/ci.yml`**

Add at the end of the file (same indentation level as the existing `test:` and `test-windows:` jobs under `jobs:`):

```yaml

  test-python:
    name: Tests (Python, ${{ matrix.os }})
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}

    steps:
      - name: Check out repository
        uses: actions/checkout@v7

      - name: Install uv
        uses: astral-sh/setup-uv@v6

      - name: Sync dependencies
        run: uv sync --frozen

      - name: Run pytest
        run: uv run pytest -q
```

- [ ] **Step 2: Validate the commands CI will run, locally**

Run: `uv sync --frozen`
Expected: succeeds without changing `uv.lock` (proves `--frozen` will work in CI).

Run: `uv run pytest -q`
Expected: all PASS.

Run: `git diff .github/workflows/ci.yml`
Expected: only the appended `test-python` job; the two bash jobs untouched.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run pytest suite alongside frozen bash suites

Add a test-python job on ubuntu and windows using astral-sh/setup-uv
with a frozen sync, keeping the existing bash jobs untouched. The two
tracks run together for the whole migration; the bash jobs are removed
only at cutover (plan 6). The repo has no remote yet, so this job is
exercised on the first push."
```

---

## Verification at the End of This Plan

Run: `uv run pytest -q`
Expected: all tests pass (4 test files).

Run: `bash tests/helpers.test.sh` (Git Bash)
Expected: unchanged — same pass count as before this plan (the bash side is frozen and untouched).

## Not in This Plan (deliberately)

- `engines.py`, `validate_engines`, `engine_model`, `resolve_model_args`:
  plan 2 (they need the Engine abstraction).
- `detect_gate` / `detect_build_gate`: plan 4 with `gates.py`.
- `art_path` / `write_meta` / `archive_snapshot` / run directories: plan 3.
- Retry loop (`engine_call` semantics): plan 2, on top of Task 4's parsers.
- Console script / `cli.py`: plan 5.
