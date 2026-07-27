# adversarial-ai-coding

English | [繁體中文](README.zh-TW.md)

`adversarial-ai-coding` is a workflow orchestrator for agentic software development.

## Multi-Agent Adversarial Coding Workflow

One AI agent is the worker, and a second AI agent is the reviewer that reviews
the work and writes adversarial acceptance tests.

The workflow is designed around spec-driven development (SDD), adversarial
test-driven development with protected acceptance tests, deterministic quality
gates, small commits, and human review before costly implementation starts.

## How It Works

Every run drives two agent slots: 

  - `A` is the worker
  - `B` is the adversarial reviewer

Use different AI brands for the two slots — their blind spots differ. Each slot can be `claude` (Claude Code), `codex` (Codex CLI), `agy`
(Antigravity CLI), or a custom wrapper command. 

The implementation step can optionally use a third slot `I` (see
[Strong Model Plans, Cheap Model Implements](#strong-model-plans-cheap-model-implements)).

The default pipeline:

```text
Spec (A writes, B reviews)
human gate (if PHASES is unset and no plan is imported, may offer Phased ATDD; see below)
commit
  ↓
Plan into a checkbox task list (- [ ], A writes, B reviews)
(HUMAN_GATE_PLAN=1) human gate
commit
  ↓
B writes all acceptance tests at once (TDD red; the workflow does not verify red)
A reviews
commit acceptance tests
record + arm the protected-test guard (whole list at once; re-checked after every later worker call)
  ↓
For each task (- [ ] in plan.md):
    A implements (IMPL_AGENT swaps the implementer; default A)
    build gate
    commit
  ↓
Full gate (acceptance tests must all be green)
  ↓
B reviews the whole branch diff → commit if dirty
  ↓
A self-review → full gate
  ↓
B final acceptance → commit if dirty
  ↓
finish: write pr-body.md, (OPEN_PR=1) push + gh pr create
```

Four rules make the pipeline adversarial instead of cooperative:

- **The reviewer writes the acceptance tests.** Roles swap for that stage: B
  writes the tests, A only reviews them, and the test files become protected —
  the workflow re-checks them with `git diff` after every later worker action.
- **Quality gates are deterministic.** The workflow runs the build and test
  commands itself and feeds failures back to the worker; an agent's own "tests
  pass" claim is never trusted.
- **The workflow decides when a review ends.** Every review step loops
  review → fix → gate until B's `verdict.json` is approved, and aborts after
  `MAX_ROUNDS`. Only blockers repeat the loop; suggestions accumulate and are
  handled in the final stage.
- **Humans sit at the highest-leverage checkpoints.** A human approves the
  spec before implementation starts (`HUMAN_GATE`), optionally the plan too
  (`HUMAN_GATE_PLAN=1`), and the run ends in a PR for a human to merge.

Two optional modes reshape parts of the pipeline:

- **[Phased ATDD](#phased-atdd-mode)** (`PHASES=1`) splits the plan into
  vertical phases and replaces the single test stage with a per-phase loop:
  B writes one phase's tests, the workflow verifies they start red, the phase
  is implemented, and the phase gate keeps every finished phase green.
- **[Dual spec](#dual-spec-mode)** (`DUAL_SPEC=1`) replaces the spec stage:
  A and B write independent candidate specs, cross-review them, and a human
  picks the base (or a merge). The chosen slot owns the rest of the run.

For the stage-by-stage walkthrough — the full pipeline diagram, review-loop
mechanics, gate commands, and per-stage notes — see
[`docs/how-it-works.md`](docs/how-it-works.md).

## Requirements

- Python 3.12 or newer
- [Astral uv](https://docs.astral.sh/uv/)
- `git`
- The AI CLIs you plan to use, already installed and logged in:
  - `claude`
  - `codex`
  - `agy` is optional
- Any custom agent or wrapper commands you configure through `AGENT_A`,
  `AGENT_B`, or `IMPL_AGENT`, available on `PATH`.
- Run the workflow from the root of the target Git repository. Bash and `jq`
  are not required.

## Quick Start

Install the locked environment once in the `adversarial-ai-coding` checkout:

```bash
cd /path/to/adversarial-ai-coding
uv sync --frozen
```

Then run it from the root of the target project. `--project` selects the
workflow's environment without changing the target working directory:

```bash
cd /path/to/your-project
AAC_PROJECT=/path/to/adversarial-ai-coding
```

When the target is this checkout itself, the shorter form is
`uv run adversarial-ai-coding task.md`.

Run a task with the default agents, where Claude is the worker and Codex is the
reviewer:

```bash
uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding "Add --json output to the CLI"
```

You can also write the task in a file:

```bash
uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Swap the worker and reviewer agents:

```bash
AGENT_A=codex AGENT_B=claude uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Use the same built-in CLI in both slots with different slot-specific models:

```bash
AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex \
  uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Use custom agent or wrapper commands:

```bash
AGENT_A=gemini AGENT_A_ARGS='--model gemini-2.5-pro --yolo' \
AGENT_B=my-review-wrapper AGENT_B_ARGS='--strict' \
  uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Enable dual spec mode:

```bash
DUAL_SPEC=1 uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Print the agent rules template for manual merging into an existing `AGENTS.md`:

```bash
uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding print-agents
```

An existing `AGENTS.md` is never overwritten. Every run compares the block
between the `adversarial-ai-coding:begin` and `adversarial-ai-coding:end`
markers against the current template and prints a note when it is missing or
out of date, so rules added by a newer version do not go unnoticed. Your own
text around the block is left alone and is not treated as drift.

## Strong Model Plans, Cheap Model Implements

Spec writing, planning, acceptance tests, and adversarial review benefit most
from a strong model. The repetitive stage-5 task loop can use a cheaper model
or a different CLI without weakening the complete gate and reviews that follow.

Keep the owner's command and change only the implementation model:

```bash
AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet \
  uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

Or plan with Claude and implement the checkbox tasks with Codex:

```bash
AGENT_A=claude MODEL_A=opus \
AGENT_B=codex MODEL_B=gpt-5.5 \
IMPL_AGENT=codex IMPL_MODEL=gpt-5-codex \
IMPL_ARGS='-c model_reasoning_effort="low"' \
  uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

If all three `IMPL_*` variables are empty, the implementation slot resolves to
the selected owner with exactly the previous behavior. If `IMPL_MODEL` is
empty, the implementation slot inherits `MODEL_A` or `MODEL_B` only when its
command is the same as the owner's command. Changing the command without also
setting `IMPL_MODEL` uses that CLI's default model; a model name is never
carried across different CLIs.

## Dual Spec Mode

Set `DUAL_SPEC=1` to make both slots write independent candidate specs before
implementation planning starts. The workflow becomes:

```text
DUAL_SPEC=1: replaces the Spec stage of the default/phased flow; the rest is unchanged
(preflight: requires HUMAN_GATE=1 and an interactive terminal)

A writes candidate spec-a.md, B writes candidate spec-b.md (independently; reading the other is forbidden)
  ↓
Cross review: B reviews spec-a, A reviews spec-b (report + verdict inform the human only; never block, no repair loop)
  ↓
A and B each write a comparison table (spec-comparison-a/b.md); workflow writes the spec-comparison.md index
  ↓
Human chooses a / b / ma / mb:
    a, b:   adopt that candidate as the base
    ma, mb: that candidate is the base; the human edits spec-merge-request.md listing items to adopt from the other (workflow verifies it has real content)
    the chosen slot becomes owner and takes over the "A" role; the other becomes reviewer "B"
  ↓
base is copied to spec.md; (merge) the owner merges per the merge request
reviewer reviews spec.md (merge: adopted items must arrive intact and undistorted) + human gate → commit
  ↓
continues at the Plan stage of the default or phased flow (A = owner, B = reviewer)
```

Decision commands:

- `a`: copy Candidate A to final `spec.md`
- `b`: copy Candidate B to final `spec.md`
- `ma`: use Candidate A as base, edit `.workflow/spec-merge-request.md`, and
  require A to adopt selected items from Candidate B
- `mb`: use Candidate B as base, edit `.workflow/spec-merge-request.md`, and
  require B to adopt selected items from Candidate A

After selection, the chosen owner remains responsible for planning, complete
gate and review repairs, and self-review. The optional implementation slot runs
only the per-task loop described above. The other A/B slot becomes the reviewer
and writes the protected acceptance tests. Dual spec mode requires an
interactive terminal and `HUMAN_GATE=1`; unattended runs should leave it
disabled.

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

When a spec is imported with review disabled (`IMPORT_SPEC` + `IMPORT_REVIEW=0`), no spec reviewer runs, so no phased suggestion is produced or offered.

## Phased ATDD Mode

Set `PHASES=1` to replace the single up-front acceptance-test stage with a
per-phase loop. The plan must use `## Phase N: <title>` headings; every
phase needs an `Acceptance:` line with observable behavior at a stable
boundary and at least one `- [ ]` task. Phases must be vertical functional
slices (a working behavior increment), never horizontal technical layers.
The workflow parses the plan deterministically after the plan review and
sends structure problems back to the owner before anything is implemented.

```text
Spec (A writes, B reviews)
human gate
commit
  ↓
Plan into vertical phases (A writes, B reviews) 
(HUMAN_GATE_PLAN=1) human gate
workflow validates plan structure
commit
  ↓
For each phase:
    B writes this phase's acceptance/component/contract tests
    A reviews
    workflow verifies the tests are correctly red (regression-guard phase must be green instead)
    commit phase tests
    record + arm the protected-test guard (append; re-checked after every later worker call)
    For each task:
        A implements (IMPL_AGENT swaps the implementer; default A)
        build gate
        commit
    phase gate: earlier phases + current phase all green
    (PHASE_REVIEW=1) B reviews the phase diff → commit if dirty
  ↓
Full gate
  ↓
B reviews the whole branch diff → commit if dirty
  ↓
A self-review → full gate
  ↓
B final acceptance → commit if dirty
  ↓
finish: write pr-body.md, (OPEN_PR=1) push + gh pr create
```

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
unchanged. `PHASES` cannot change across resume: the value is snapshotted at run
start and conflicting resume environments are rejected. There is one
sanctioned in-run switch. When `PHASES` is unset and no plan is
imported, the spec reviewer also judges whether the task suits phased
mode — two or more vertical features that can each be accepted
independently — and writes its judgment to
`.workflow/phased-suggestion.json`. If it recommends phased, the spec
human gate shows the reason and asks `Enable Phased ATDD for this run? [y/N]:`.
Answering `y` enables phased mode and rewrites the run snapshot atomically,
so every later resume still sees one consistent value. With `HUMAN_GATE=0`
the recommendation is only logged; nothing is ever enabled automatically.
An explicit `PHASES=0` in the environment disables the suggestion entirely.

## Custom Agent Commands

If `AGENT_A`, `AGENT_B`, or `IMPL_AGENT` is not `claude`, `codex`, or `agy`,
the workflow treats it as a custom agent command. The command is run with the
slot-specific args followed by a short prompt-file instruction as the final
argument:

```bash
$AGENT_A $AGENT_A_ARGS "Read the full workflow prompt from this repository file and follow it exactly: .workflow/runs/<RUN_ID>/NNN-*-prompt.md"
$AGENT_B $AGENT_B_ARGS "Read the full workflow prompt from this repository file and follow it exactly: .workflow/runs/<RUN_ID>/NNN-*-prompt.md"
$IMPL_AGENT $IMPL_ARGS "Read the full workflow prompt from this repository file and follow it exactly: .workflow/runs/<RUN_ID>/NNN-*-prompt.md"
```

Custom commands must be agentic: they need to read the referenced prompt file,
inspect and edit the repository as needed, and exit non-zero on execution
failure. A custom reviewer must write `.workflow/review.md` and
`.workflow/verdict.json`; stdout JSON verdicts are not parsed. Custom agents
do not get automatic session resume, and `MODEL_A`, `MODEL_B`, and `IMPL_MODEL`
are not translated into model flags for them. Put model flags in
`AGENT_A_ARGS`, `AGENT_B_ARGS`, or `IMPL_ARGS`. Built-in command names may be
shared across A, B, and I because the workflow resumes only exact captured
session IDs. Distinct custom slots may not share a command name because the
workflow cannot determine a wrapper's hidden session behavior. A custom
implementation wrapper must therefore differ from both A and B. If the selected
owner is custom, setting any `IMPL_*` customization requires an explicit,
different `IMPL_AGENT` wrapper; leaving all `IMPL_*` values empty keeps the
owner itself and does not create a distinct slot.

If a custom CLI needs session continuity, handle it in a wrapper script. For
example, give the worker and reviewer separate profiles, session ids, or cache
directories:

```bash
# my-agent-worker
exec my-agent --session aac-worker "$@"

# my-agent-reviewer
exec my-agent --session aac-reviewer "$@"
```

Wrappers are also the right place for CLIs that need stdin, prompt files,
quoting-sensitive arguments, or other stateful setup.

## Writing Good Tasks

The result depends heavily on how clear the task is. Prefer a task file with a
goal, testable acceptance criteria, and explicit non-goals.

```markdown
## Goal

Add `--json` output to the CLI.

## Acceptance Criteria

- `mytool list --json` prints a valid JSON array.
- Behavior without `--json` is unchanged.

## Out of Scope

- Do not change the existing text output format.
- Do not add `--yaml`.
```

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `AGENT_A` | `claude` | Worker agent command: `claude`, `codex`, `agy`, or a custom command. |
| `AGENT_B` | `codex` | Reviewer agent command. In the acceptance-test stage, the roles are swapped. |
| `IMPL_AGENT` | selected owner command | Command for the stage-5 per-task implementation loop. Built-ins may match A or B; a custom implementation wrapper must differ from both. |
| `MODEL_A` | CLI default | Model override for built-in slot A, even when both slots use the same command. Custom agents should pass model flags through `AGENT_A_ARGS`. |
| `MODEL_B` | CLI default | Model override for built-in slot B, even when both slots use the same command. Custom agents should pass model flags through `AGENT_B_ARGS`. |
| `IMPL_MODEL` | inherited or CLI default | Model override for a built-in implementation slot. When omitted, inherits the owner's model only if the implementation and owner commands match; custom implementation agents ignore it and use `IMPL_ARGS`. |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` | empty | Extra CLI arguments for built-in commands, shared by command name and parsed with POSIX shell quoting. Session-control flags documented below are reserved. |
| `AGENT_A_ARGS` / `AGENT_B_ARGS` | empty | Extra CLI arguments for custom agent commands, parsed with POSIX shell quoting and appended before the prompt-file instruction argument. |
| `IMPL_ARGS` | empty | Extra implementation-slot arguments, parsed with POSIX shell quoting. For a built-in, these follow its command-wide args; for a custom implementation wrapper, include its model flag here. |
| `MAX_ROUNDS` | `3` | Maximum review or quality-gate repair rounds per stage. |
| `HUMAN_GATE` | `1` | Pause for human approval after the spec review. Set `0` for unattended runs. |
| `HUMAN_GATE_PLAN` | `0` | `1` also pauses for human approval after the plan review, before `plan.md` is committed. Independent of `HUMAN_GATE`, and requires an interactive terminal. |
| `DUAL_SPEC` | `0` | `1` enables the dual spec flow: A/B write independent candidates, cross-review once, produce comparison tables, and wait for human owner selection. Requires `HUMAN_GATE=1` and an interactive terminal. |
| `IMPORT_SPEC` | empty | Use this file as `spec.md`; skip the "worker writes the spec" step. |
| `IMPORT_PLAN` | empty | Use this file as `plan.md`; skip the "worker writes the plan" step. Requires `IMPORT_SPEC`. |
| `IMPORT_REVIEW` | `1` | Imported artifacts still go through the reviewer's review loop. `0` skips the AI review of imported artifacts only. Requires `IMPORT_SPEC`. |
| `PHASES` | `0` | `1` enables the phased ATDD flow: the plan is split into vertical phases, and each phase writes its own protected acceptance tests before its tasks are implemented. Decides the stage graph, so it cannot change across resume. When `PHASES` and `IMPORT_PLAN` are both unset, the spec reviewer also judges fitness and the spec human gate may offer to enable it (see [Phased ATDD Mode](#phased-atdd-mode)). |
| `PHASE_GATE_CMD` | empty | Gate command for the per-phase red check and phase gate. Empty falls back to `GATE_CMD`. |
| `PHASE_REVIEW` | `0` | `1` adds a reviewer pass over each phase diff, with blocker loops. Off by default because the phase gate already enforces the reviewer's protected tests. |
| `GATE_CMD` | auto-detected | Full quality gate. Go projects use `go build ./... && go vet ./... && go test ./...`, npm projects with a `test` script use `npm test`, Cargo projects use `cargo test`, and projects without a detected gate skip deterministic gates unless you set it. |
| `BUILD_GATE_CMD` | auto-detected | Lightweight per-task build gate. Go projects use `go build ./...`, Cargo projects use `cargo build`, and projects without a detected build gate skip this per-task gate unless you set it. |
| `AUTO_BRANCH` | `1` | Create an `auto/<timestamp>` branch before running. |
| `USE_WORKTREE` | `0` | Run in a separate Git worktree. |
| `OPEN_PR` | `0` | Push and create a GitHub PR at the end. By default, commands are only printed. |
| `NOTIFY_CMD` | empty | Notification command. The message is passed as the first argument. |
| `COLOR` | `auto` | Colorize the workflow's own status messages. `auto` normally keeps redirected or non-terminal output plain; `NO_COLOR` disables color, `FORCE_COLOR` can force ANSI color in `auto` mode, including redirects, and `TERM=dumb` disables unforced color. `always` can emit ANSI color to redirected output; `never` disables color; the archived run log never contains color codes, even when color is forced. |
| `COLOR_THEME` | `dark` | Status message color theme: `dark` or `light`. |
| `COLOR_<CATEGORY>` | theme default | Per-category color override for `STAGE`, `PROGRESS`, `ERROR`, `WARNING`, `CHECKPOINT`, `SUCCESS`. Accepts a color name (`red`, `bright-cyan`, `bold-bright-red`) or raw SGR parameters (`1;91`), e.g. `COLOR_ERROR=bold-bright-red`. |
| `RETRY_ON_LIMIT` | `1` | Wait and retry on rate-limit or quota errors. |
| `RETRY_MAX` | `6` | Maximum rate-limit retries per agent call. |
| `RETRY_BASE_WAIT` | `300` | Initial exponential backoff wait, in seconds. |
| `RETRY_MAX_WAIT` | `3600` | Maximum exponential backoff wait, in seconds. |
| `RETRY_MAX_RESET_WAIT` | `21600` | When the message states a reset time farther away than this, abort instead of waiting. |
| `RESUME_RUN` | empty | Resume an interrupted run: a run id from `.workflow/state/`, or `last` for the newest unfinished run. Completed stages are skipped. See "Resuming an Interrupted Run". |
| `AGENTS_TEMPLATE` | workflow checkout's `resources/AGENTS.template.md` | Path to the `AGENTS.md` template. |
| `PROMPTS_DIR` | workflow checkout's `resources/prompts` | Directory for workflow prompt templates. |
| `SPEC_DIR` | `specs/<timestamp>` | Directory for `spec.md` and `plan.md`. |
| `RUNS_DIR` | `.workflow/runs` | Directory for archived workflow run artifacts. |
| `TOOLS` | git/go build/test/vet allowlist | Claude Code `--allowedTools` value. |

On Windows, if you want Go race tests in the gate, use:

```bash
GATE_CMD='go build ./... && go vet ./... && go test -race -ldflags "-extldflags=-Wl,--default-image-base-low" ./...' \
  uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding task.md
```

## Resuming an Interrupted Run

Every run records its progress under `.workflow/state/<run-id>/`: the resolved
task, the effective settings, a stage completion ledger, and the remaining
implementation tasks. When a run aborts, it prints a paste-ready command:

```bash
RESUME_RUN=20260710-153012 uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding
```

The resumed run skips every completed stage (no AI cost is paid again),
restores cross-stage state (the dual-spec decision, the acceptance-test base,
the write-code task queue), and continues from the interruption point.
`RESUME_RUN=last` picks the newest unfinished run. Do not pass the task
argument again: the run's task snapshot is used, and a conflicting argument
is refused.

Engines, models, and most settings may be overridden per attempt. The main
use case is swapping an agent whose quota ran out:

```bash
AGENT_B=agy RESUME_RUN=last uv run --project "$AAC_PROJECT" --locked adversarial-ai-coding
```

The persisted `IMPL_AGENT`, `IMPL_MODEL`, and `IMPL_ARGS` values follow the
same non-empty override rule. A non-empty value on the resume command replaces
the snapshot for that attempt, but an empty environment value cannot clear a
saved value. To clear one, edit the corresponding lowercase key in
`.workflow/state/<run-id>/settings.json`, keep it as valid schema-1 JSON, and
then resume. For example, set `"impl_model": ""` to return to the inheritance
rule.

`SPEC_DIR`, `DUAL_SPEC`, `AUTO_BRANCH`, and `USE_WORKTREE` are immutable
across resume: they decide the stage graph and artifact locations, so a
conflicting override is refused. `NOTIFY_CMD` is deliberately not persisted;
provide it again on each attempt.

What is guaranteed:

| Interruption | Behavior |
|---|---|
| Catchable aborts: agent failure, quota exhaustion (exit code 75), exhausted review or gate rounds, human abort, protected-test stop, Ctrl-C / SIGTERM / SIGHUP | The resume command is printed once and the original exit code is preserved. Resuming continues after the last completed stage. |
| SIGKILL, power loss, OS crash | Best effort. State is append-only and fails safe: at worst one or two stages run again (a little extra AI cost); finished work is never skipped incorrectly. |
| Deleted state directory or worktree, rewritten branch history | Fails closed with an explicit message. There is no transparent recovery. |

Notes:

- `USE_WORKTREE=1` runs keep their state inside the worktree's `.workflow/`.
  Resume from inside the worktree; the printed hint includes the `cd` command.
- A crashed attempt can leave a stale lock. The error message shows the exact
  `rm` command to clear `.workflow/state/<run-id>/lock` once you confirmed the
  previous attempt is dead.
- A completed run refuses to resume, and `RESUME_RUN=last` skips completed
  runs.

## Artifacts

The workflow writes live state under `.workflow/` and archives each run
under `.workflow/runs/<RUN_ID>/` by default.

```text
adversarial-ai-coding/
|-- pyproject.toml
|-- src/adversarial_ai_coding/
`-- resources/
    |-- AGENTS.template.md
    `-- prompts/
        `-- *.md

your-project/
|-- AGENTS.md
|-- CLAUDE.md
|-- specs/<RUN_ID>/
|   |-- spec-a.md                    # DUAL_SPEC=1 candidate from slot A
|   |-- spec-b.md                    # DUAL_SPEC=1 candidate from slot B
|   |-- spec-a.review-by-b.md        # DUAL_SPEC=1 one-shot candidate review
|   |-- spec-b.review-by-a.md        # DUAL_SPEC=1 one-shot candidate review
|   |-- spec-comparison-a.md         # DUAL_SPEC=1 comparison from slot A
|   |-- spec-comparison-b.md         # DUAL_SPEC=1 comparison from slot B
|   |-- spec-comparison.md           # DUAL_SPEC=1 human decision index
|   |-- spec-decision.md             # DUAL_SPEC=1 selected owner/reviewer
|   |-- spec.md
|   `-- plan.md
|-- .workflow/
|   |-- review.md
|   |-- verdict.json
|   |-- suggestions.md
|   |-- spec-merge-request.md        # DUAL_SPEC=1 merge-adoption instructions
|   |-- protected-tests.txt
|   |-- protected-base.sha
|   |-- pr-body.md
|   |-- latest-run.txt
|   |-- state/<RUN_ID>/               # resume state: settings snapshot, stage ledger, task queue
|   `-- runs/<RUN_ID>/
|       |-- 001-run-metadata.json
|       |-- 002-task-source.md
|       |-- 003-task.txt
|       |-- NNN-*-prompt.md
|       |-- NNN-*-output.txt
|       |-- NNN-*-attempt-*-rc*.raw
|       |-- NNN-*-attempt-*-rc*.cli.raw
|       |-- NNN-review-*.md
|       |-- NNN-verdict-*.json
|       |-- NNN-*-git-status.txt
|       |-- NNN-*-git-diff.patch
|       |-- metrics.csv
|       `-- logs/001-run.log
```

Each archived artifact has a `.meta.json` sidecar with generator, `engine`,
model, stage, round, run id, and timestamp data. The archive schema keeps the
stable `engine` field name for backward compatibility; it records the resolved
agent command/runtime used for the call.

## Agent CLI Session Behavior

| | Claude | Codex | Agy |
|---|---|---|---|
| Non-interactive call | `claude -p` | `codex exec --json` | `agy --print` |
| Worker resume | `--resume <id>` | `exec resume ... <thread-id>` | `--conversation <conversation-id>` |
| ID source | Structured response | `thread.started` JSONL event | Per-attempt `--log-file` record |
| Permission mode | `acceptEdits` + `TOOLS` | `--sandbox workspace-write` | `--dangerously-skip-permissions` |

Claude, Codex, and Agy may each be used in A, B, and I. Worker calls resume only
by their captured ID, while every reviewer call starts fresh. There is one
active worker session, not one saved session per slot. Calls with the same full
agent ref can resume within a loop; any handoff to a different agent ref, such
as changing the slot or command, discards the captured ID and starts fresh.
Changing only the model does not itself discard the active session because
model values are not part of `AgentRef`; discard happens only when slot/command
ref identity changes (or when a stage boundary resets the session).
Thus I starts fresh on entry, accumulates context during the per-task loop, and
is discarded when the workflow returns to the owner for the complete gate. The
old owner session is not restored either. Stage boundaries also clear the
active session. Workflow prompts point to complete archived prompt files, so
these handoffs do not depend on retained chat context.

The workflow never falls back to Codex `--last` or Agy `--continue`: if a fresh
call does not yield an ID, it warns and starts fresh again next time; if an
established session later omits the ID, the known ID is retained. Codex JSONL
and Agy logs are archived as per-attempt `.cli.raw` artifacts for diagnosis.

Built-in session, output, sandbox, and log flags belong to the workflow.
`CLAUDE_ARGS` (and `IMPL_ARGS` when I resolves to Claude) must not contain
`-c` / `--continue`, `-r` / `--resume`, `--session-id`, `--fork-session`,
`--no-session-persistence`, or `--from-pr`, and must not override the structured
output contract through `--output-format` or `--json-schema`.
`CODEX_ARGS` and Codex-targeted `IMPL_ARGS` must not contain `--json`, `resume`,
`--sandbox` / `-s`, `--dangerously-bypass-approvals-and-sandbox`, `--yolo`,
`--ephemeral`, or a `sandbox_mode` override through `-c` / `--config`.
`AGY_ARGS` and Agy-targeted `IMPL_ARGS` must not contain `--log-file`,
`--continue`, or `--conversation`. Built-in argument variables also cannot set
a model with `--model`, `-m`, or Codex `-c model=` / `--config model=`; use
`MODEL_A`, `MODEL_B`, or `IMPL_MODEL` so actual calls and archived metadata
agree. Attached short forms such as `-mMODEL`, `-sVALUE`, and `-cVALUE` are
parsed by the same reserved-option rules. Custom argument variables are passed
through instead, so custom model and session flags may be supplied there.

Agy conversation IDs depend on its current log wording; an incompatible Agy
upgrade degrades safely to a warning and fresh sessions rather than resuming an
unrelated conversation.

All built-in and custom agent argument variables use POSIX shell quoting on
every platform. Quote values to preserve embedded spaces. On Windows, quote
paths containing backslashes or write them with `/`; unquoted backslashes have
their POSIX escape meaning.

## Protected Acceptance Tests

Acceptance tests are written by the reviewer and then protected. During
implementation, the worker must not edit, delete, or skip files listed in
`.workflow/protected-tests.txt`.

After the acceptance stage, the active workflow process keeps the exact bytes
of the protected path list and base control file, together with their parsed
paths and base commit, in memory. It verifies those exact bytes before and
after every active worker boundary. An empty path list still protects both
control files. The snapshot is process-local: a resumed process trusts the
current on-disk controls as its new starting state. It is not an OS-level lock
or a guarantee against concurrent pathname replacement between filesystem
calls.

If a protected test is wrong, stop the workflow and handle it manually. Edit
the corrected test, commit the corrected test, and only then write that new
commit SHA to `.workflow/protected-base.sha`. If the test should no longer be
protected, a human may instead remove its path from
`.workflow/protected-tests.txt`. Resume only after the manual controls describe
the intended trusted state.

## Safety Notes

- Deterministic gates are run by the workflow, not trusted from AI output.
- `agy` currently uses `--dangerously-skip-permissions`; prefer a worktree or
  container when using it.
- Branches and worktrees isolate Git state, but they do not isolate the full
  filesystem or network. Use a container for stronger isolation.
- Two AI agents can consume a lot of quota. `MAX_ROUNDS`, graded verdicts, and
  `commit_if_dirty` are designed to limit waste.
- Dual spec mode adds extra AI calls for the second candidate, candidate
  reviews, and comparison tables. Keep it off unless the spec decision is
  worth the extra cost.
- `DUAL_SPEC=1` intentionally refuses `HUMAN_GATE=0` and non-interactive
  terminals because the workflow requires a human owner decision.
- Identical custom agent commands cannot be used as both worker and reviewer.
  Use distinct wrapper command names when both slots share the same underlying
  custom CLI. Identical built-in Claude, Codex, and Agy slots are supported.

## Testing This Repository

Run the unit and integration suite. It does not call any AI agent:

```bash
uv run pytest -q
```

Run the full E2E only when changing core workflow behavior. It calls real AI
agents and consumes quota:

```bash
uv run pytest -m e2e -s
```

## Troubleshooting

### Reviewer did not write `verdict.json`

The reviewer failed or did not follow the rules. For custom reviewers, verify
that the command can write `.workflow/review.md` and `.workflow/verdict.json`.
Check `.workflow/logs/` or the run archive under `.workflow/runs/<RUN_ID>/`.

### The run is stuck on a permission prompt

Headless mode cannot answer permission prompts. For Claude Code, add required
commands to `TOOLS`. For Codex, check sandbox settings. For Antigravity, check
its permission flags and isolation.

### No interactive terminal is available for approval

`HUMAN_GATE=1` requires a TTY, and so does `HUMAN_GATE_PLAN=1` (which is
checked at startup, before any AI call). In unattended environments, set
`HUMAN_GATE=0` (and leave `HUMAN_GATE_PLAN` at `0`) and use `NOTIFY_CMD` or PR
review for human control.

### The per-task quality gate keeps failing

During task implementation, full acceptance tests may still be red. When
configured, the workflow uses `BUILD_GATE_CMD` for per-task checks and `GATE_CMD`
after all tasks finish.

### Reviewer reports corrupted files on Windows

Some AI tools may misdecode non-ASCII UTF-8 content on Windows. Keep generated
specs, plans, and test data ASCII when possible. Represent non-ASCII source
test data with Unicode escapes, as described in `resources/AGENTS.template.md`.

### Rate limit or quota errors

By default, the workflow waits and retries on rate-limit or quota errors, for every
agent. When the message states how long to wait, the workflow waits exactly that
long instead of guessing. It understands four shapes:

| Message | Agent | Wait |
| --- | --- | --- |
| `resets 10:50am` | Claude | Until that clock time, plus 2 minutes |
| `try again in 90s` | Codex | That duration, plus 30 seconds |
| `try again at Jul 14th, 2026 7:23 PM` | Codex | Until that timestamp, plus 30 seconds |
| `try again at 12:50 AM` | Codex | Until that clock time, plus 30 seconds |

Anything else falls back to exponential backoff (`RETRY_BASE_WAIT` doubling up to
`RETRY_MAX_WAIT`), for at most `RETRY_MAX` retries.

A weekly quota can reset days away. Sleeping through it would waste hours and
still fail, so when a parsed reset time exceeds `RETRY_MAX_RESET_WAIT` the run
aborts immediately, reports the reset timestamp, and fires `NOTIFY_CMD`. Rerun
after the quota returns. Set `RETRY_ON_LIMIT=0` to fail immediately on any limit.

## Related Reading

- [Claude Code headless mode](https://code.claude.com/docs/en/headless)
- [Codex CLI non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)
- [GitHub Spec Kit](https://github.com/github/spec-kit)
