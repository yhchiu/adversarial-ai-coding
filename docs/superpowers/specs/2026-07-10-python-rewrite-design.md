# Python Rewrite Design

Date: 2026-07-10
Status: Approved design, pending implementation plan

## Context

`adversarial-ai-coding.sh` has grown to 1,836 lines of Bash with about 120
functions, plus 1,400 lines of Bash tests. The pain points driving this
rewrite, in the owner's priority order:

1. Changing the workflow is getting risky: one large file with global state,
   so a change in one stage can break another stage.
2. Bash language limits: string handling, structured data (resume snapshot,
   CSV metrics), and error handling get worse as the logic grows.
3. Bash tests have no assertion framework or mocking tools, so the test
   suites are expensive to maintain.

Windows compatibility is not a pain point today (CI covers Git Bash on
Windows), but the rewrite removes the Git Bash dependency as a side benefit.

## Decisions Already Made

- Target language: **Python**. Decided; no further language comparison.
- Compatibility: **behavior-compatible**. Same environment variables, same
  stage flow, same artifact directory layout, same prompt files. Internal
  interfaces may be redesigned. The Bash version was never released, so no
  cross-version compatibility is required (a run started by Bash does not
  need to resume under Python).
- Project shape: **standard package managed by uv**, `src/` layout,
  Python 3.12+, pytest for tests.
- Migration strategy: **full port in one project** (option A). The Bash
  script and its tests stay frozen in the repo as the working tool and the
  behavior reference. Bash is deleted only after the Python version passes
  the parity gates below.

## Goals

- Reach feature parity with the Bash version, then replace it.
- Split the single file into modules with one clear purpose each.
- Replace global mutable state with explicit parameter passing.
- Port the Bash test assertions to pytest so behavior carries over.

## Non-Goals

- No new workflow features during the port.
- No CLI redesign: keep `adversarial-ai-coding "task" | task.md |
  print-agents` and the same environment variables (including legacy
  aliases and their conflict detection).
- No third-party runtime dependencies (stdlib only; pytest is dev-only).
- No changes to `resources/` (prompt templates and AGENTS template are
  reused as-is).

## Architecture

Same repository, rewritten in place. New files:

```
pyproject.toml                      # uv-managed, Python 3.12+, pytest dev dep
src/adversarial_ai_coding/
    cli.py         # entry point: args, print-agents, usage, top-level error handler
    config.py      # frozen Settings dataclass: env vars, legacy aliases, validation
    engines.py     # engine adapters: claude / codex / agy / generic command
    prompts.py     # prompt template loading and placeholder rendering
    runstate.py    # run lock, settings snapshot, stage ledger, cross-stage restore
    archive.py     # run directory, artifacts, meta files, git snapshots, CSV metrics
    ratelimit.py   # quota detection, reset-time parsing, wait-and-retry policy
    gates.py       # quality/build gate detection and gate repair loop
    review.py      # review prompt assembly, verdict parsing, review loop, suggestions
    gitops.py      # branch/worktree setup, commits, protected-file checks, checkpoints
    workflow.py    # stage orchestration: spec -> plan -> tests -> implement -> finish
    dual_spec.py   # DUAL_SPEC mode: dual candidates, cross-review, human decision
```

The console script name is `adversarial-ai-coding`. During development the
tool runs with `uv run adversarial-ai-coding`.

## Components

**Settings (`config.py`).** A frozen dataclass loaded once from the
environment, with the same preferred/legacy alias resolution and conflict
errors as `alias_env_or_default`. All modules receive `Settings` (and
`RunState`) as explicit parameters. No module-level mutable globals.

**Engines (`engines.py`).** A small protocol replaces the paired `w_*` /
`r_*` Bash functions:

```python
class Engine(Protocol):
    name: str
    def work_argv(self, prompt_file: Path, *, fresh_session: bool) -> list[str]: ...
    def review_argv(self, prompt_file: Path) -> list[str]: ...
```

One implementation per built-in CLI (claude, codex, agy) plus
`GenericEngine` for custom commands. A single `run_engine()` function owns
subprocess execution: it streams output to the console and the artifact
file at the same time (the `tee` behavior) and returns the exit code.
Session semantics are preserved: the same stage resumes the worker session,
a new stage resets it, and reviewer rounds always start fresh.

**Run state (`runstate.py`).** Same responsibilities and invariants as the
Bash version (C2-C6): atomic run lock via `mkdir`, task snapshot as the
single source of truth on resume, stage ledger with checkpoint HEADs,
restore of acceptance-test base and dual-spec decision from files. Format
changes (allowed, no released users):

- `resume.conf` (hand-parsed key=value) becomes `settings.json`.
- The ledger becomes JSON as well.
- All state writes stay atomic: temp file + `os.replace`.
- Unchanged: metrics stay CSV (human spreadsheet use), verdicts stay
  `verdict.json`, and the artifact directory layout and names stay
  identical.

**Workflow (`workflow.py`).** Each stage is a plain function receiving a
`WorkflowContext` (settings + run state + engines + archive). No class
hierarchy. `begin_stage()` / `end_stage()` keep the resume semantics:
completed stages are skipped, completion records the checkpoint HEAD.
`dual_spec.py` is invoked from the spec stage only when `DUAL_SPEC=1`.

## Error Handling

Exceptions replace `set -Eeuo pipefail` plus traps:

- A small domain hierarchy: `WorkflowError` (user-facing message),
  `RateLimitExceeded` (carries reset info for the retry policy).
- `cli.py` owns the single top-level handler, mirroring
  `on_workflow_exit`: print the paste-ready resume hint exactly once for
  any abort of an unfinished run, release the run lock in `finally`, and
  keep the exit-code semantics (non-zero on abort).
- `KeyboardInterrupt` takes the same path, so Ctrl-C stays resumable.
- Engine subprocess failures keep the current archive-attempt-then-retry
  behavior of `engine_call`.

## Testing

Three layers, mirroring the Bash suites:

- **Unit (ports `tests/helpers.test.sh`, 972 lines).** Pure helpers (slug,
  CSV, reset-wait parsing, prompt rendering, alias resolution, ...) are
  imported directly and tested with parametrized pytest cases. Every Bash
  assertion is ported; the Bash tests are the executable spec.
- **Integration (ports `tests/resume.test.sh`).** Offline interrupt-resume
  scenarios with fake engine scripts and temporary git repos: ledger
  skipping, snapshot restore, lock conflicts.
- **E2E.** The Go fixture is reused unchanged. The driver moves from
  `tests/e2e/run.sh` to pytest with a marker, keeping the setup-only mode
  for CI. Real-engine E2E stays manual and quota-gated.

CI runs both matrices (frozen Bash suites and pytest) on ubuntu and windows
during the migration. After cutover the Bash steps are removed; the Windows
job then no longer needs Git Bash or the LF checkout guard.

## Parity Gates

1. All Bash test assertions are ported to pytest and pass on both OSes.
2. The implementation plan maintains a Bash-function-to-Python mapping
   table, checked off one by one, so nothing is silently dropped.
3. Final acceptance: one real small-task workflow run end to end under the
   Python version, including one deliberate interrupt and resume.

Scheduling note: the real interrupt-resume E2E planned for 2026-07-14
(quota reset) still runs on the Bash version first. It validates the resume
design itself; the port then inherits a validated design.

## Bash Retirement

When all parity gates pass: delete `adversarial-ai-coding.sh`,
`tests/helpers.test.sh`, `tests/resume.test.sh`, and `tests/e2e/run.sh`;
update `README.md` and `README.zh-TW.md` (install via uv, usage otherwise
unchanged); remove the Bash CI steps.

## Migration Phases

Rough commit grouping; the detailed task breakdown belongs to the
implementation plan:

1. Scaffold: pyproject, package skeleton, CI pytest job alongside Bash.
2. `config.py` plus pure helpers, with their unit tests.
3. `engines.py` + `ratelimit.py`.
4. `runstate.py` + `archive.py`.
5. `gates.py` + `review.py` + `gitops.py`.
6. `workflow.py` + `dual_spec.py` + `cli.py`.
7. CI dual-track green on both OSes.
8. Real-run final acceptance.
9. Bash removal and documentation updates.

## Risks

- **Silent behavior drift** in rarely exercised paths (rate-limit parsing,
  protected-file recovery). Mitigated by porting every Bash test assertion
  and the function mapping table.
- **Windows path differences** once Git Bash is out of the loop (native
  paths instead of POSIX-style). Mitigated by `pathlib` throughout and the
  existing Windows CI job.
- **Long dual-maintenance window** if the port stalls: the freeze on the
  Bash version is only sustainable if no urgent workflow features are
  needed mid-port. Accepted; the tool works today and there is no release
  pressure.
