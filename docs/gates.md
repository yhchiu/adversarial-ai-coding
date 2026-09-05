# Gates

English | [繁體中文](gates.zh-TW.md)

A gate is a decision the workflow makes for itself instead of taking an
agent's word for it. An agent that says "all tests pass" is reporting, not
proving; a gate is what turns that claim into a fact, or stops the run.

There are three kinds:

| Kind | Decides | Bounded by |
|---|---|---|
| **Command gates** | Does the code build and pass its tests? | `MAX_ROUNDS` |
| **Human gates** | Should this run keep spending money? | a person |
| **Integrity gates** | Did anyone rewrite the exam or the plan? | `MAX_ROUNDS`, 2 recoveries |

This document is the reference for all of them. For the pipeline they sit
in, see [`how-it-works.md`](how-it-works.md); for permission errors while a
gate runs, see [`troubleshooting.md`](troubleshooting.md).

## Command gates

Three commands, run by the workflow through the platform shell with the
workspace as the working directory. Exit status 0 is a pass; anything else
is a failure.

| Variable | Default | What it is |
|---|---|---|
| `GATE_CMD` | auto-detected | The full gate: build, vet, and every test, including the protected acceptance tests. |
| `BUILD_GATE_CMD` | auto-detected | The per-task gate. Compile only: it runs after every implementation task, while the acceptance tests are still expected to be red. |
| `PHASE_GATE_CMD` | empty, falls back to `GATE_CMD` | The gate for Phased ATDD: the red check before a phase, and the phase gate after it. |

### Where each one runs

Default flow:

```text
For each task in plan.md:
    implement → BUILD_GATE_CMD → commit
All tasks done → GATE_CMD          (acceptance tests must now be green)
Branch review → after each fix round → GATE_CMD
Self review → GATE_CMD
Final acceptance review
```

Phased ATDD (`PHASES=1`) adds two positions, both using `PHASE_GATE_CMD` or,
when it is empty, `GATE_CMD`:

```text
For each phase:
    reviewer writes this phase's tests → red check   (must FAIL)
    For each task: implement → BUILD_GATE_CMD → commit
    phase gate                                       (must PASS)
    (PHASE_REVIEW=1) phase review → after each fix round → phase gate
```

The red check is the one gate that expects failure. New tests that pass
before the code exists prove nothing, so a phase whose tests come back green
is sent back to the test author. A phase whose title ends in
`(regression-guard)` inverts this: those tests lock in behavior that already
works, so they must pass immediately.

### What happens when a gate fails

`gate_loop` is the same for every command gate:

1. The command runs. Exit 0 ends the loop.
2. On failure the last 150 lines of combined stdout and stderr go to the
   agent responsible for that stage, with instructions to repair.
3. The command runs again. This repeats until it passes or the attempt count
   reaches `MAX_ROUNDS` (default 3).
4. On the last attempt the workflow sends the `NOTIFY_CMD` notification and
   aborts the run with the last 50 lines of output. The branch and every
   commit so far are left in place; fix the problem and resume with
   `RESUME_RUN`.

Who repairs depends on where the gate is: the implementation slot for the
per-task and phase gates, the owner for the full gate and the self-review
gate, and whichever slot is fixing review findings inside a review loop.

## Detection

A gate command is resolved in this order, and the first non-empty value
wins:

1. the environment variable for this run
2. the value recorded in the resume snapshot
3. auto-detection from the workspace

An empty string counts as unset, so `GATE_CMD= aac request.md` does not
disable the gate — it falls through to the snapshot and then to detection.

| Detected from | `GATE_CMD` | `BUILD_GATE_CMD` |
|---|---|---|
| `go.mod` | `go build ./... && go vet ./... && go test ./...` | `go build ./...` |
| `package.json` with a `test` script | `npm test` | (none) |
| `package.json` without one | (none) | (none) |
| `Cargo.toml` | `cargo test` | `cargo build` |
| a Python project (see below) | pytest through the tool that owns the environment | (none) |
| anything else | (none) | (none) |

Detection stops at the first match, in that order, so in a repository that
is both a Go service and an npm front end the Go gate wins. Set `GATE_CMD`
explicitly to gate both.

`PHASE_GATE_CMD` is never detected. Left empty it uses `GATE_CMD`, which is
usually what you want: the phase gate is the full suite, just run earlier.

### Python detection

A Python gate is claimed only when three things hold together:

1. a marker file: `pyproject.toml`, `setup.py`, or `setup.cfg`
2. a statement that pytest is the runner: a `[tool.pytest.ini_options]`
   section, a `pytest.ini`, `[pytest]` in `tox.ini`, `[tool:pytest]` in
   `setup.cfg`, or pytest named in `pyproject.toml`
3. at least one test file: `test_*.py` or `*_test.py`, in the root or under
   `tests/`

The third condition is not caution. pytest exits 5 when it collects no
tests, which a gate reads as a failure, so a project with no tests yet would
send an agent to repair code that is not broken, once per round, until
`MAX_ROUNDS`.

The command names the tool that owns the environment, because which
interpreter runs the tests matters more than the spelling of `python`:

| Found in the workspace | Command |
|---|---|
| `uv.lock` | `uv run pytest` |
| `poetry.lock` | `poetry run pytest` |
| `.venv/Scripts/python.exe` or `.venv/bin/python` | that interpreter, `-m pytest` |
| neither, but `python` is on PATH | `python -m pytest` |
| neither, but only `python3` is | `python3 -m pytest` |

Python projects get no `BUILD_GATE_CMD`: there is no compile step worth
running between tasks.

### When nothing is detected

Startup prints the resolved full gate, or this warning when there is none:

```text
(warning: no quality gate command detected; deterministic gates are
disabled. Set GATE_CMD to enable one.)
```

A run in that state still works, and that is the danger: every deterministic
check is skipped and the only remaining evidence that the code works is an
agent saying so. What each empty value costs:

| Empty | Consequence |
|---|---|
| `GATE_CMD` | No full gate, no gate inside review loops, and in phased mode no red check and no phase gate unless `PHASE_GATE_CMD` is set. The acceptance tests are written, committed, and never run. |
| `BUILD_GATE_CMD` | No per-task compile check. Breakage is found later, by the full gate, with more tasks to search through. Silent: no warning is printed. |
| `PHASE_GATE_CMD` | Falls back to `GATE_CMD`. Only when both are empty is the red check skipped, with its own warning. |

### Setting a gate yourself

Anything the shell can run works, as long as it is non-interactive, exits
non-zero on failure, and runs from the repository root:

```bash
# Make-based project
GATE_CMD='make build && make test' BUILD_GATE_CMD='make build' aac request.md

# Gradle
GATE_CMD='./gradlew build' BUILD_GATE_CMD='./gradlew compileJava' aac request.md

# .NET
GATE_CMD='dotnet test' BUILD_GATE_CMD='dotnet build' aac request.md

# Python without a lock file or a detected venv
GATE_CMD='python -m pytest -q' aac request.md

# Monorepo: gate one package
GATE_CMD='npm --prefix services/api test' aac request.md

# Faster phase gate than the full suite
PHASES=1 GATE_CMD='make test-all' PHASE_GATE_CMD='make test-fast' aac request.md
```

Notes that save a wasted run:

- The command goes to the platform shell — `cmd.exe` on Windows, `sh`
  elsewhere. `&&` chaining is portable; `;`, subshells, and single-quote
  nesting are not.
- Go race tests on Windows need a linker flag; the README settings table
  carries the full value under `GATE_CMD`.
- Keep `BUILD_GATE_CMD` fast. It runs after every task, and it must tolerate
  red acceptance tests — a build gate that runs the tests will fail every
  task until the last one.
- A gate that needs credentials, a database, or a network service will fail
  in the same way a broken build does, and the agent will try to repair the
  code. Gate what the agent can actually fix.

## Human gates

| Variable | Default | Gate |
|---|---|---|
| `HUMAN_GATE` | `1` | Approve `spec.md` before implementation starts. |
| `HUMAN_GATE_PLAN` | `0` | Approve `plan.md` after the plan review. |

Both pause with the file path and ask for `y`; anything else aborts the run.
You may edit the file first — your edits are committed with the stage. Two
more human gates appear in Dual Spec mode: the `[a/b/ma/mb]` decision that
picks the base spec, and the approval after you edit a merge request.

The spec gate is also where Phased ATDD may be offered: with `PHASES` unset
and no imported plan, the spec reviewer judges fitness, and a recommendation
turns into `Enable Phased ATDD for this run? [y/N]:`. With `HUMAN_GATE=0`
the recommendation is only logged, never applied.

Human gates fail closed. Without an interactive terminal the ask refuses
rather than assuming approval, and `HUMAN_GATE_PLAN=1` and `DUAL_SPEC=1` are
both rejected during preflight — before any paid call — when stdin is not a
terminal.

## Integrity gates

**Protected acceptance tests.** Once the reviewer's tests are committed,
their paths are recorded in `aac/.run/protected-tests.txt` with a base
commit in `aac/.run/protected-base.sha`. After every producing call the
workflow runs `git diff` against that base and fails closed if git itself
errors. A violation is handed back to the agent to restore; after two
failed recoveries the run aborts. See
[Protected Acceptance Tests](../README.md#protected-acceptance-tests) for
the escape hatch when a test really is wrong.

**Plan structure.** In phased mode the plan is parsed deterministically
after the plan review: phases numbered from 1, each with an `Acceptance:`
line and at least one `- [ ]` task, and no task outside a phase. Structure
problems go back to the owner before anything is implemented.

**Verdicts.** Every review loop ends on the reviewer's `verdict.json`, not
on prose. Only blockers repeat the loop; it too is bounded by `MAX_ROUNDS`,
and suggestions accumulate for the final stage.

## `MAX_ROUNDS`

`MAX_ROUNDS` (default `3`) bounds every loop above: command-gate repairs,
red-check repairs, review rounds, and the phase gate. Raising it lets an
agent keep trying on a genuinely hard failure; lowering it stops a
thrashing run sooner. It does not bound the protected-test guard, which
allows two recoveries.

## `TOOLS` and gates

The workflow runs gate commands itself, so `TOOLS` never affects whether a
gate can run. It affects the agents: a Claude slot that cannot run the
project's tests cannot check its own work, and a permission prompt in a
non-interactive run stalls it.

The default allowlist is detected from the workspace and is the union of
what it holds, since one repository can be several:

| Detected from | Rules added |
|---|---|
| always | `Bash(git *)` |
| `go.mod` | `Bash(go test *),Bash(go build *),Bash(go vet *)` |
| `package.json` | `Bash(npm test)` |
| `Cargo.toml` | `Bash(cargo build),Bash(cargo test)` |
| a Python project naming pytest | `Bash(pytest *),Bash(uv run pytest *),Bash(poetry run pytest *),Bash(python -m pytest *),Bash(python3 -m pytest *)` |

A workspace matching none of them keeps every rule above. Unlike the gate,
this detection is a union and does not require test files to exist yet — the
run is what writes them. A custom `GATE_CMD` usually needs a matching rule:
gating on `make` while `TOOLS` omits `Bash(make *)` leaves the agent unable
to reproduce the failure it is being asked to fix.

## Resume

Gate commands are recorded in the run snapshot, so a resumed run keeps the
gates it started with even if detection would now answer differently.
Passing the variable again overrides the snapshot for that attempt. `PHASES`
is the exception: it decides the stage graph, so it is fixed at run start
and a conflicting value is rejected on resume.

## Related

- [`how-it-works.md`](how-it-works.md) — the stage-by-stage pipeline
- [`troubleshooting.md`](troubleshooting.md) — permission errors, `TOOLS`
  syntax, quota waits
- [README: Configuration](../README.md#configuration) — every environment
  variable, gates included
