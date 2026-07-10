# Python Port — Plan 6 of 6: Cutover and Bash Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks 4 and 5 are gated on Task 3's HUMAN-RUN acceptance — do not execute them until the human has recorded a passing acceptance.

**Goal:** Finish the port: pytest E2E driver, a consolidated parity audit, the real-engine acceptance run (including a deliberate interrupt + resume), CI cutover, and deletion of the bash implementation.

**Architecture:** Plan 6 of the series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. This plan
closes the three parity gates: (1) ported test assertions green on both
OSes — reached at the end of plan 5; (2) the function mapping audit —
Task 2; (3) one real small-task run under the Python version including one
deliberate interrupt and resume — Task 3 (human-run, quota-gated).

**Tech Stack:** Python 3.12+, pytest markers for E2E, GitHub Actions.

## Global Constraints

- Until Task 5 executes, the bash files stay FROZEN. Task 5 deletes them.
- Tasks 4 (CI cutover) and 5 (bash removal) are BLOCKED until Task 3's
  acceptance record shows all checks passed. If Task 3 fails, fix forward
  through the normal review loop and re-run Task 3 before proceeding.
- The real E2E consumes real quota (roughly $2-5 and 20-40 minutes with
  the default models) — never wire it into CI or the default pytest run.
- Scheduling note from the project ledger: the codex weekly quota resets
  2026-07-14 19:23. The bash-version real interrupt-resume E2E planned for
  that date validates the resume design first; run Task 3 after it.
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By.
- `uv run pytest -q` green after every task (E2E excluded by marker).
- Machine note: clear `PYTHONHOME`/`PYTHONPATH` if `uv run` misbehaves.

## File Structure

```
tests/e2e/test_e2e.py       # Task 1 (driver port; fixture/ unchanged)
pyproject.toml              # Task 1 (marker + addopts)
docs/python-port-parity.md  # Task 2 (audit), Task 3 (acceptance record)
.github/workflows/ci.yml    # Task 4 (bash jobs removed)
README.md, README.zh-TW.md  # Task 5 (uv usage)
DELETED: adversarial-ai-coding.sh, tests/helpers.test.sh,
         tests/resume.test.sh, tests/e2e/run.sh     # Task 5
```

---

### Task 1: Port the E2E driver to pytest

Bash reference: `tests/e2e/run.sh` (all of it — setup, baseline gates,
workflow invocation, acceptance checklist).

**Files:**
- Create: `tests/e2e/test_e2e.py`
- Modify: `pyproject.toml` (marker registration, default exclusion)
- Test: the file itself; `run.sh` stays frozen until Task 5.

**Interfaces:**
- `pytest -m e2e` runs the real-engine E2E (manual). Default runs exclude
  it. The setup-only baseline check runs whenever a Go toolchain exists
  (CI keeps it on ubuntu, where setup-go provides one).
- Environment defaults mirror run.sh:18-34; `E2E_DIR` keeps the workspace.

- [ ] **Step 1: Register the marker and default exclusion in `pyproject.toml`**

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-m 'not e2e'"
markers = [
    "e2e: real-engine end-to-end; consumes quota; run manually with -m e2e",
]
```

- [ ] **Step 2: Write `tests/e2e/test_e2e.py`**

```python
"""E2E driver, port of tests/e2e/run.sh.

Two layers:
- test_fixture_baseline: copies the Go fixture, verifies the baseline gates
  locally. No AI. Runs wherever a Go toolchain exists (CI: ubuntu).
- test_full_workflow_e2e: the real-engine run plus the acceptance
  checklist. Marked e2e; excluded by default; costs real quota.

E2E defaults from real-run lessons (run.sh:27-34): worker at least
sonnet-class; codex reviewer effort lowered to save quota.
"""

import csv
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
FIXTURE = HERE / "fixture"

E2E_DEFAULTS = {
    "HUMAN_GATE": "0",
    "AGENT_A": "claude", "MODEL_A": "sonnet", "CLAUDE_ARGS": "--effort=low",
    "AGENT_B": "codex", "MODEL_B": "gpt-5.5",
    "CODEX_ARGS": "-c model_reasoning_effort=low",
}

needs_go = pytest.mark.skipif(shutil.which("go") is None,
                              reason="fixture is a Go project")


def run(cmd, cwd, env=None, check=True):
    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(cmd, cwd=cwd, env=merged, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise AssertionError(f"{cmd} failed rc={proc.returncode}:\n"
                             f"{proc.stdout}\n{proc.stderr}")
    return proc


def make_fixture_repo(base: Path) -> Path:
    repo = base / "repo"
    shutil.copytree(FIXTURE, repo)
    run(["git", "init", "-q", "-b", "main"], repo)
    run(["git", "config", "user.email", "e2e@local"], repo)
    run(["git", "config", "user.name", "e2e"], repo)
    run(["git", "add", "-A"], repo)
    run(["git", "commit", "-qm",
         "chore: baseline fixture for adversarial-ai-coding E2E"], repo)
    return repo


def verify_gates(repo: Path):
    # Verified locally instead of trusting AI output (run.sh:60-65).
    run(["go", "build", "./..."], repo)
    run(["go", "vet", "./..."], repo)
    run(["go", "test", "./..."], repo)


@needs_go
def test_fixture_baseline(tmp_path):
    repo = make_fixture_repo(tmp_path)
    verify_gates(repo)


@pytest.mark.e2e
@needs_go
def test_full_workflow_e2e():
    base = Path(os.environ.get("E2E_DIR") or tempfile.mkdtemp(prefix="wf-e2e-"))
    base.mkdir(parents=True, exist_ok=True)
    print(f"== E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    env = {k: os.environ.get(k, v) for k, v in E2E_DEFAULTS.items()}
    tool = shutil.which("adversarial-ai-coding")
    assert tool, "console script not installed; run `uv sync` first"
    proc = subprocess.run(
        [tool, "task.md"],
        cwd=repo, env={**os.environ, **env}, capture_output=True,
        text=True, encoding="utf-8", errors="replace")
    (base / "run.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    assert proc.returncode == 0, f"workflow rc={proc.returncode}; see {base}/run.log"
    log = (base / "run.log").read_text(encoding="utf-8")

    # ---- acceptance checklist (run.sh:81-173) ------------------------------
    assert "All stages complete" in log
    assert "Quality gate passed" in log
    branch = run(["git", "branch", "--show-current"], repo).stdout.strip()
    assert branch.startswith("auto/")
    assert (repo / "AGENTS.md").is_file() and (repo / "CLAUDE.md").is_file()

    spec_dirs = sorted((repo / "specs").glob("*/"))
    assert spec_dirs, "specs/<run>/ missing"
    spec = spec_dirs[0] / "spec.md"
    plan = spec_dirs[0] / "plan.md"
    assert "assumptions and open questions" in spec.read_text(encoding="utf-8").lower()
    plan_text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in plan_text and "- [x]" in plan_text

    strutil = "".join(p.read_text(encoding="utf-8")
                      for p in (repo / "strutil").glob("*.go"))
    assert "func IsPalindrome" in strutil

    protected = repo / ".workflow" / "protected-tests.txt"
    base_sha = repo / ".workflow" / "protected-base.sha"
    assert protected.is_file() and protected.stat().st_size > 0
    assert base_sha.is_file()
    from adversarial_ai_coding.gitops import protected_violations
    assert protected_violations(
        protected, base_sha.read_text(encoding="utf-8").strip(), repo) == []

    run_dir = Path((repo / ".workflow" / "latest-run.txt")
                   .read_text(encoding="utf-8").strip())
    assert run_dir.is_dir()
    for pattern in ("*-task-source.md", "*-task.txt", "*-prompt.md",
                    "*-output.txt", "*-attempt-*-rc*.raw", "*-git-status.txt",
                    "*-git-diff.patch", "*.meta.json"):
        assert list(run_dir.glob(pattern)), f"missing artifact {pattern}"
    log_meta = json.loads((run_dir / "logs" / "001-run.log.meta.json")
                          .read_text(encoding="utf-8"))
    assert log_meta["run_id"] and log_meta["generator_role"] == "workflow"

    commits = int(run(["git", "rev-list", "--count", "main..HEAD"], repo)
                  .stdout.strip())
    assert commits >= 5, f"small-batch commits: main..HEAD = {commits}"

    verify_gates(repo)  # final gate, verified locally

    metrics = run_dir / "metrics.csv"
    assert metrics.is_file()
    rows = list(csv.reader(metrics.open(newline="", encoding="utf-8")))
    assert rows[0] == ["run_id", "stage", "role", "engine", "round",
                       "duration_s", "cost_usd", "model", "model_args",
                       "generated_at"]
    assert len(rows) > 1 and all(len(r) == 10 for r in rows)
    print(f"Acceptance passed; workspace kept at {base} (delete after inspection)")
```

- [ ] **Step 3: Verify**

Run: `uv run pytest -q`
Expected: suite green; `test_full_workflow_e2e` deselected by the marker;
`test_fixture_baseline` runs when go is installed, otherwise skips.

Run: `uv run pytest tests/e2e/test_e2e.py -q -m e2e --collect-only`
Expected: exactly one test collected (proves the marker wiring).

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_e2e.py pyproject.toml
git commit -m "test: port the E2E driver to pytest

Split the bash driver into a no-AI fixture baseline test that runs
wherever a Go toolchain exists and a marker-gated real-engine E2E with
the full acceptance checklist (stages, gates, branch, bootstrap files,
spec/plan shape, protected tests untouched, artifact inventory, commit
granularity, metrics schema). The e2e marker is excluded by default so
neither CI nor a plain pytest run can burn quota. run.sh stays frozen
until the bash retirement task."
```

---

### Task 2: Consolidated parity audit

**Files:**
- Create: `docs/python-port-parity.md`

**Interfaces:** none (documentation gate). This is parity gate 2 from the
spec: "a Bash-function-to-Python mapping table, checked off one by one, so
nothing is silently dropped."

- [ ] **Step 1: Build the audit document**

Concatenate the per-plan mapping tables (plans 2-5 each carry a
"Bash-Function Mapping" section; plan 1's modules are config/prompts/
archive-pure/ratelimit-parsers) into `docs/python-port-parity.md` with
three sections:

1. **Function map** — every function in `adversarial-ai-coding.sh` (walk
   the file top to bottom; `grep -E '^[a-zA-Z_]+\(\) \{' adversarial-ai-coding.sh`
   is the checklist source) with its Python location or the token
   `intentionally-dropped(<reason>)`. Every row must be one or the other —
   an unmapped row FAILS this task.
2. **Known deliberate divergences** — consolidate the per-plan lists:
   resets-at/on branches resurrected; DST-aware day rollover; hour-range
   guard; metrics_summary short-row guard and deterministic ordering;
   settings.json/ledger.json formats; newline-capable snapshot values;
   platform-shell gate execution; shutil.which argv[0] resolution; lenient
   non-JSON claude output; interactive-stdin human gates (no /dev/tty);
   jq not required; setup_workspace returns instead of chdir; task_arg
   first-line persistence. Each with a one-line rationale and the test
   that pins it.
3. **Acceptance record** — an empty section Task 3 fills in.

- [ ] **Step 2: Verify the walk found no unmapped functions**

Run: `grep -E '^[a-zA-Z_]+\(\) \{' adversarial-ai-coding.sh | wc -l`
and compare with the row count in the function map (plus dropped rows).
Expected: equal.

- [ ] **Step 3: Commit**

```bash
git add docs/python-port-parity.md
git commit -m "docs: add the bash-to-python parity audit

Walk every function in the frozen bash script and record its Python
location or an intentional-drop rationale, consolidate the deliberate
divergences with their pinning tests, and open the acceptance-record
section the real-run gate fills in."
```

---

### Task 3: Real-run final acceptance (HUMAN-RUN, quota-gated)

This is parity gate 3. It cannot be executed by an agent — it consumes
real quota and requires a deliberate manual interrupt. The implementing
agent prepares nothing here; the human runs the procedure and records the
result. Do not proceed to Tasks 4-5 until the record shows PASS.

- [ ] **Step 1 (human): schedule after quota**

Run after the bash-version real interrupt-resume E2E planned for the
2026-07-14 quota reset has validated the resume design.

- [ ] **Step 2 (human): full real E2E under Python**

Run: `uv run pytest tests/e2e/test_e2e.py -m e2e -s`
Expected: acceptance checklist passes; workspace path printed and kept.

- [ ] **Step 3 (human): deliberate interrupt + resume (real SIGINT path)**

In a scratch copy of the E2E fixture repo:

1. `uv run adversarial-ai-coding task.md` with the E2E default env.
2. During the write-code stage, press Ctrl-C once.
   Expected: exit code 130, "Workflow interrupted", a paste-ready
   `RESUME_RUN=<id> adversarial-ai-coding` hint printed exactly once, and
   the run lock released (`.workflow/state/<id>/lock` absent).
3. Re-run with the printed `RESUME_RUN=<id>`.
   Expected: completed stages skipped (`== skip [...]` lines), no repeated
   spec/plan agent calls (compare `.workflow/runs/*/metrics.csv` rows),
   run completes, `completed` marker present.
4. `RESUME_RUN=<id>` again. Expected: "already completed" refusal.

- [ ] **Step 4 (human): record the result**

Fill the acceptance-record section of `docs/python-port-parity.md` with
date, models used, run ids, interrupt point, and PASS/FAIL per check.

```bash
git add docs/python-port-parity.md
git commit -m "docs: record python port real-run acceptance"
```

---

### Task 4: CI cutover (BLOCKED until Task 3 PASS)

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Replace the workflow with the Python-only matrix**

```yaml
name: CI

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  test-python:
    name: Tests (Python, ${{ matrix.os }})
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}

    steps:
      - name: Check out repository
        uses: actions/checkout@v7

      - name: Set up Go (fixture baseline test)
        uses: actions/setup-go@v6
        with:
          go-version-file: tests/e2e/fixture/go.mod
          cache: false

      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true

      - name: Sync dependencies
        run: uv sync --frozen

      - name: Run pytest
        run: uv run pytest -q
```

The bash `test` and `test-windows` jobs, the jq install, the shell syntax
check, and the LF-checkout guard are all removed. The Windows job now runs
the same steps as ubuntu (the Go setup lets `test_fixture_baseline` run on
both platforms).

- [ ] **Step 2: Verify locally and commit**

Run: `uv sync --frozen && uv run pytest -q` — green.
Run: `git diff .github/workflows/ci.yml` — only the described change.

```bash
git add .github/workflows/ci.yml
git commit -m "ci: cut over to the python test matrix

Remove the frozen bash jobs, jq install, shell syntax check, and LF
checkout guard now that the real-run acceptance passed. Both OS jobs
run the same uv sync + pytest steps, with Go available for the E2E
fixture baseline test."
```

---

### Task 5: Bash retirement and documentation (BLOCKED until Task 3 PASS)

**Files:**
- Delete: `adversarial-ai-coding.sh`, `tests/helpers.test.sh`,
  `tests/resume.test.sh`, `tests/e2e/run.sh`
- Modify: `README.md`, `README.zh-TW.md`, `.gitattributes` (review)

- [ ] **Step 1: Delete the bash implementation**

```bash
git rm adversarial-ai-coding.sh tests/helpers.test.sh tests/resume.test.sh tests/e2e/run.sh
```

Then verify nothing else references them:
Run: `grep -rn "adversarial-ai-coding.sh\|helpers.test.sh\|resume.test.sh\|e2e/run.sh" --include="*.md" --include="*.yml" --include="*.py" .`
Expected: hits only inside `docs/` history (plans/specs — leave those; they
are records) and `docs/python-port-parity.md`. README hits get fixed in
Step 2. Any hit in CI or source is a bug in this task.

- [ ] **Step 2: Update both READMEs**

Keep the workflow description, stage diagram, and environment variable
reference (unchanged behavior). Replace the invocation sections:

- Install: `uv sync` (requires Python 3.12+ and uv).
- Run: `uv run adversarial-ai-coding "task description"` /
  `uv run adversarial-ai-coding task.md` /
  `uv run adversarial-ai-coding print-agents`.
- Resume: `RESUME_RUN=<id|last> uv run adversarial-ai-coding`.
- Tests: `uv run pytest -q`; real E2E: `uv run pytest -m e2e -s`.
- Note that jq and bash are no longer required; git remains required.
- Apply the same changes to `README.zh-TW.md` in Traditional Chinese,
  keeping both files structurally in sync.

- [ ] **Step 3: Review `.gitattributes`**

Run: `git show HEAD:.gitattributes` (or read the file) and remove rules
that exist only for shell scripts (e.g. forced LF on `*.sh`) if any;
keep everything else.

- [ ] **Step 4: Full verification**

Run: `uv run pytest -q` — green.
Run: `uv run adversarial-ai-coding print-agents | head -3` — template text.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat!: retire the bash implementation

Delete adversarial-ai-coding.sh and the bash test suites now that the
Python port passed all three parity gates: ported assertions green on
both OSes, the function-map audit complete, and the real-run acceptance
including a deliberate interrupt and resume recorded in
docs/python-port-parity.md. Update both READMEs to the uv-based
invocation; behavior, environment variables, stage flow, and artifact
layout are unchanged.

BREAKING CHANGE: the entry point is now the adversarial-ai-coding
console script (uv run adversarial-ai-coding); the .sh script no longer
exists."
```

---

## Verification at the End of This Plan

- `uv run pytest -q` green on the dev machine; CI green on both OS jobs
  once the repo has a remote.
- `docs/python-port-parity.md` complete: full function map, divergences,
  and a PASS acceptance record.
- The repository contains no bash implementation and both READMEs describe
  the uv workflow.

## After This Plan

The port is complete. Deferred ideas that were explicitly out of scope
(new features, CLI redesign beyond the console script, publishing to PyPI)
go through fresh brainstorming if wanted.
