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
(Antigravity CLI), `opencode` (OpenCode, any model the user has already
authenticated), or a custom wrapper command. 

The implementation step can optionally use a third slot `I` (see
[Strong Model Plans, Cheap Model Implements](#strong-model-plans-cheap-model-implements)).

Roles at each stage:

| Stage | Writes | Reviews |
| --- | --- | --- |
| Spec | A | B |
| Plan | A | B |
| Acceptance tests | **B** | A |
| Implementation (per-task loop) | A (`IMPL_AGENT` can swap in I) | build gate, plus the later branch review |
| Branch review / final acceptance | — | B (one A self-review in between) |

Two separations are deliberate: **no slot writes both the spec and the
acceptance tests** — turning a spec into tests is what forces a second model to
walk every corner of it, which is where ambiguities and gaps surface — and **no
slot implements against tests it wrote itself**, which would let one agent set
its own exam and then sit it.

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
  - `opencode` is optional; use it when you want one runtime and many models
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

The repository includes `scripts/aac` for macOS/Linux and `scripts/aac.cmd` for
Windows. Add the checkout's `scripts` directory to `PATH` for your current
shell:

macOS, Linux, or Git Bash:

```bash
export PATH="/path/to/adversarial-ai-coding/scripts:$PATH"
```

Windows PowerShell:

```powershell
$env:Path = "C:\path\to\adversarial-ai-coding\scripts;$env:Path"
```

Windows Command Prompt:

```bat
set "PATH=C:\path\to\adversarial-ai-coding\scripts;%PATH%"
```

Put the equivalent setting in your shell profile or user `PATH` to keep it
across terminal sessions. The `aac` launchers find the workflow checkout from
their own location, run its environment with `--locked`, and leave the current
working directory unchanged. They unset `PYTHONHOME` and `PYTHONPATH` so a
machine-wide Python install cannot crash the locked interpreter.

Then run `aac` from the root of the target project:

```bash
cd /path/to/your-project
```

Run a request with the default agents, where Claude is the worker and Codex is
the reviewer:

```bash
aac "Add --json output to the CLI"
```

You can also write the request in a file:

```bash
aac request.md
```

Swap the worker and reviewer agents:

```bash
AGENT_A=codex AGENT_B=claude aac request.md
```

Use the same built-in CLI in both slots with different slot-specific models:

```bash
AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex \
  aac request.md
```

Use OpenCode as a multi-model runtime. AAC does not keep a model catalog;
`MODEL_*` is passed through as `provider/model`. Authenticate with
`opencode auth` or a custom provider in your OpenCode config. Pair OpenCode
with a different CLI when you want different runtimes, not only different
weights:

```bash
AGENT_A=opencode MODEL_A=google/gemini-2.5-pro \
AGENT_B=claude   MODEL_B=opus \
  aac request.md
```

Use custom agent or wrapper commands:

```bash
AGENT_A=gemini AGENT_A_ARGS='--model gemini-2.5-pro --yolo' \
AGENT_B=my-review-wrapper AGENT_B_ARGS='--strict' \
  aac request.md
```

Enable dual spec mode:

```bash
DUAL_SPEC=1 aac request.md
```

Print the agent rules template for manual merging into an existing `AGENTS.md`:

```bash
aac print-agents
```

Show the CLI help or version:

```bash
aac --help
aac --version
```

An existing `AGENTS.md` is never overwritten. Every run compares the block
between the `adversarial-ai-coding:begin` and `adversarial-ai-coding:end`
markers against the current template and prints a note when it is missing or
out of date, so rules added by a newer version do not go unnoticed. Your own
text around the block is left alone and is not treated as drift.

## Writing a Good Request

The result depends heavily on how clear the request is. Prefer a request file
with a goal, testable acceptance criteria, and explicit non-goals.

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

## Strong Model Plans, Cheap Model Implements

Spec writing, planning, acceptance tests, and adversarial review benefit most
from a strong model. The repetitive stage-5 checkbox-task loop can use a cheaper
model or a different CLI without weakening the complete gate and reviews that
follow.

Keep the owner's command and change only the implementation model:

```bash
AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet \
  aac request.md
```

Or plan with Claude and implement the checkbox tasks with Codex:

```bash
AGENT_A=claude MODEL_A=opus \
AGENT_B=codex MODEL_B=gpt-5.5 \
IMPL_AGENT=codex IMPL_MODEL=gpt-5-codex \
IMPL_ARGS='-c model_reasoning_effort="low"' \
  aac request.md
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
- `ma`: use Candidate A as base, edit `aac/.run/spec-merge-request.md`, and
  require A to adopt selected items from Candidate B
- `mb`: use Candidate B as base, edit `aac/.run/spec-merge-request.md`, and
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
imported, the spec reviewer also judges whether the request suits phased
mode — two or more vertical features that can each be accepted
independently — and writes its judgment to
`aac/.run/phased-suggestion.json`. If it recommends phased, the spec
human gate shows the reason and asks `Enable Phased ATDD for this run? [y/N]:`.
Answering `y` enables phased mode and rewrites the run snapshot atomically,
so every later resume still sees one consistent value. With `HUMAN_GATE=0`
the recommendation is only logged; nothing is ever enabled automatically.
An explicit `PHASES=0` in the environment disables the suggestion entirely.

## Custom Agent Commands

If `AGENT_A`, `AGENT_B`, or `IMPL_AGENT` is not `claude`, `codex`, `agy`, or
`opencode`,
the workflow treats it as a custom agent command. The command is run with the
slot-specific args followed by a short prompt-file instruction as the final
argument:

```bash
$AGENT_A $AGENT_A_ARGS "Read the full workflow prompt from this repository file and follow it exactly: aac/.run/archive/<RUN_ID>/NNN-*-prompt.md"
$AGENT_B $AGENT_B_ARGS "Read the full workflow prompt from this repository file and follow it exactly: aac/.run/archive/<RUN_ID>/NNN-*-prompt.md"
$IMPL_AGENT $IMPL_ARGS "Read the full workflow prompt from this repository file and follow it exactly: aac/.run/archive/<RUN_ID>/NNN-*-prompt.md"
```

Custom commands must be agentic: they need to read the referenced prompt file,
inspect and edit the repository as needed, and exit non-zero on execution
failure. A custom reviewer must write `aac/.run/review.md` and
`aac/.run/verdict.json`; stdout JSON verdicts are not parsed. Custom agents
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

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `AGENT_A` | `claude` | Worker agent command: `claude`, `codex`, `agy`, `opencode`, or a custom command. |
| `AGENT_B` | `codex` | Reviewer agent command. In the acceptance-test stage, the roles are swapped. |
| `IMPL_AGENT` | selected owner command | Command for the stage-5 per-task implementation loop. Built-ins may match A or B; a custom implementation wrapper must differ from both. |
| `MODEL_A` | CLI default | Model override for built-in slot A, even when both slots use the same command. Custom agents should pass model flags through `AGENT_A_ARGS`. |
| `MODEL_B` | CLI default | Model override for built-in slot B, even when both slots use the same command. Custom agents should pass model flags through `AGENT_B_ARGS`. |
| `IMPL_MODEL` | inherited or CLI default | Model override for a built-in implementation slot. When omitted, inherits the owner's model only if the implementation and owner commands match; custom implementation agents ignore it and use `IMPL_ARGS`. |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` / `OPENCODE_ARGS` | empty | Extra CLI arguments for built-in commands, shared by command name and parsed with POSIX shell quoting. Session-control flags documented below are reserved. For OpenCode, put `--variant` or `--agent` here; models stay on `MODEL_*`. |
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
| `AUTO_BRANCH` | `1` | Create an `aac/<timestamp>` branch before running. |
| `USE_WORKTREE` | `0` | Run in a separate Git worktree. The worktree is created next to the repository, as a sibling directory named `<repo>-aac-<timestamp>`, and is not removed for you: `git worktree remove` it when you are done with the run. |
| `OPEN_PR` | `0` | Push and create a GitHub PR at the end. By default, commands are only printed. |
| `NOTIFY_CMD` | empty | Notification command. The message is passed as the first argument. |
| `AAC_LANG` | unset (English) | Language of the workflow's own terminal messages: `en`, `zh-TW`, `zh-CN`, `ja-JP`, `ko-KR`, or `pt-BR`. Aliases: `zh_TW` / `zh-Hant*` / `zh-HK` → Traditional Chinese; `zh_CN` / `zh-Hans*` / `zh-SG` → Simplified Chinese; `ja` / `ja_*` → Japanese; `ko` / `ko_*` → Korean; `pt` / `pt_*` → Brazilian Portuguese. Unset or unknown values stay English. The package does not read `LANG`, `LC_*`, or the Windows UI culture. `scripts/aac` and `scripts/aac.cmd` set `AAC_LANG` from the OS locale when it is unset; `uv run adversarial-ai-coding` stays English unless you set this. Run logs, prompts, and artifacts stay English. |
| `COLOR` | `auto` | Colorize the workflow's own status messages. `auto` normally keeps redirected or non-terminal output plain; `NO_COLOR` disables color, `FORCE_COLOR` can force ANSI color in `auto` mode, including redirects, and `TERM=dumb` disables unforced color. `always` can emit ANSI color to redirected output; `never` disables color; the archived run log never contains color codes, even when color is forced. |
| `COLOR_THEME` | `dark` | Status message color theme: `dark` or `light`. |
| `COLOR_<CATEGORY>` | theme default | Per-category color override for `STAGE`, `PROGRESS`, `ERROR`, `WARNING`, `CHECKPOINT`, `SUCCESS`, `AGENT`. Accepts a color name (`red`, `bright-cyan`, `bold-bright-red`) or raw SGR parameters (`1;91`), e.g. `COLOR_ERROR=bold-bright-red`. |
| `RETRY_ON_LIMIT` | `1` | Wait and retry on rate-limit or quota errors. A reset time the agent reports directly is preferred over one parsed from its message. |
| `RETRY_MAX` | `6` | Maximum rate-limit retries per agent call. |
| `RETRY_BASE_WAIT` | `300` | Initial exponential backoff wait, in seconds. |
| `RETRY_MAX_WAIT` | `3600` | Maximum exponential backoff wait, in seconds. |
| `RETRY_MAX_RESET_WAIT` | `21600` | When the message states a reset time farther away than this, abort instead of waiting. |
| `RESUME_RUN` | empty | Resume an interrupted run: a run id from `aac/.run/state/`, or `last` for the newest unfinished run. Completed stages are skipped. See "Resuming an Interrupted Run". |
| `AGENTS_TEMPLATE` | workflow checkout's `resources/AGENTS.template.md` | Path to the `AGENTS.md` template. |
| `PROMPTS_DIR` | workflow checkout's `resources/prompts` | Directory for workflow prompt templates. |
| `SPEC_DIR` | `aac/docs/<timestamp>` | Directory for `spec.md` and `plan.md`. |
| `TOOLS` | git/go build/test/vet allowlist | Claude Code `--allowedTools` value. |

On Windows, if you want Go race tests in the gate, use:

```bash
GATE_CMD='go build ./... && go vet ./... && go test -race -ldflags "-extldflags=-Wl,--default-image-base-low" ./...' \
  aac request.md
```

## Resuming an Interrupted Run

Every run records its progress under `aac/.run/state/<run-id>/`: the resolved
request, the effective settings, a stage completion ledger, and the remaining
implementation tasks. When a run aborts, it prints a paste-ready command:

```bash
RESUME_RUN=20260710-153012 aac
```

The resumed run skips every completed stage (no AI cost is paid again),
restores cross-stage state (the dual-spec decision, the acceptance-test base,
the write-code task queue), and continues from the interruption point.
`RESUME_RUN=last` picks the newest unfinished run. Do not pass the request
argument again: the run's request snapshot is used, and a conflicting argument
is refused.

Engines, models, and most settings may be overridden per attempt. The main
use case is swapping an agent whose quota ran out:

```bash
AGENT_B=agy RESUME_RUN=last aac
```

The persisted `IMPL_AGENT`, `IMPL_MODEL`, and `IMPL_ARGS` values follow the
same non-empty override rule. A non-empty value on the resume command replaces
the snapshot for that attempt, but an empty environment value cannot clear a
saved value. To clear one, edit the corresponding lowercase key in
`aac/.run/state/<run-id>/settings.json`, keep it as valid schema-1 JSON, and
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

- `USE_WORKTREE=1` runs keep their state inside the worktree's `aac/.run/`.
  Resume from inside the worktree; the printed hint includes the `cd` command.
- A crashed attempt can leave a stale lock. The error message shows the exact
  `rm` command to clear `aac/.run/state/<run-id>/lock` once you confirmed the
  previous attempt is dead.
- A completed run refuses to resume, and `RESUME_RUN=last` skips completed
  runs.

## Artifacts

Everything the workflow writes goes under one top-level directory, `aac/`.
It has exactly two halves, and the split is what decides version control:

- `aac/docs/<RUN_ID>/` is **committed**. It holds the spec and plan, which a
  human reads at the human gates and a reviewer reads on the pull request.
  Nothing ignores this path.
- `aac/.run/` is **never committed**. The workflow writes a `.gitignore`
  containing a single `*` into it, so the whole subtree is invisible to git
  without touching your repository's own `.gitignore`. It is hidden because
  only the workflow reads it.

Commits are made with `git add -A` semantics, so this ignore file is the only
thing keeping machine artifacts out of your branch. See
[`docs/adr/0001-single-aac-root-for-run-artifacts.md`](docs/adr/0001-single-aac-root-for-run-artifacts.md)
for why the layout is shaped this way.

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
`-- aac/
    |-- docs/<RUN_ID>/                   # committed
    |   |-- spec.md
    |   |-- plan.md
    |   |-- spec-a.md                    # DUAL_SPEC=1 candidate from slot A
    |   |-- spec-b.md                    # DUAL_SPEC=1 candidate from slot B
    |   |-- spec-a.review-by-b.md        # DUAL_SPEC=1 one-shot candidate review
    |   |-- spec-b.review-by-a.md        # DUAL_SPEC=1 one-shot candidate review
    |   |-- spec-a.verdict-by-b.json     # DUAL_SPEC=1 candidate verdict
    |   |-- spec-b.verdict-by-a.json     # DUAL_SPEC=1 candidate verdict
    |   |-- spec-comparison-a.md         # DUAL_SPEC=1 comparison from slot A
    |   |-- spec-comparison-b.md         # DUAL_SPEC=1 comparison from slot B
    |   |-- spec-comparison.md           # DUAL_SPEC=1 human decision index
    |   `-- spec-decision.md             # DUAL_SPEC=1 selected owner/reviewer
    `-- .run/                            # never committed
        |-- .gitignore                   # "*", written on every run
        |-- review.md                    # current round's review and replies
        |-- verdict.json                 # current round's verdict
        |-- suggestions.md               # non-blocking findings, handled at the end
        |-- pr-body.md
        |-- spec-merge-request.md        # DUAL_SPEC=1 merge-adoption instructions
        |-- protected-tests.txt          # protected acceptance test paths
        |-- protected-base.sha           # commit the protected list is measured from
        |-- phased-suggestion.json       # spec reviewer's phased-fitness judgment
        |-- last-agent-output.txt        # most recent agent output
        |-- last-agent-cli.raw           # most recent raw CLI transcript
        |-- latest-run.txt               # path of the newest archive directory
        |-- state/<RUN_ID>/              # resume state
        |   |-- settings.json            # settings snapshot (schema 1)
        |   |-- ledger.json              # append-only stage ledger
        |   |-- task.txt                 # resolved request snapshot
        |   |-- phases.json              # PHASES=1 phase graph
        |   |-- tasks-remaining          # write-code task queue
        |   |-- last-head                # cross-stage HEAD record
        |   |-- lock/                    # mkdir-based mutex for one attempt
        |   `-- completed                # written when the run finishes
        `-- archive/<RUN_ID>/            # permanent record of one run
            |-- 001-run-metadata.json
            |-- 002-task-source.md
            |-- 003-task.txt
            |-- NNN-*-prompt.md
            |-- NNN-*-output.txt
            |-- NNN-*-attempt-*-rc*.raw
            |-- NNN-*-attempt-*-rc*.cli.raw
            |-- NNN-review-*.md
            |-- NNN-verdict-*.json
            |-- NNN-*-git-status.txt
            |-- NNN-*-git-diff.patch
            |-- metrics.csv
            `-- logs/001-run.log
```

The files directly under `aac/.run/` describe the current round, not the whole
run: `init_live_state` clears the transient ones at startup, and a resume keeps
the durable controls that later stages depend on.

Each archived artifact has a `.meta.json` sidecar with generator, `engine`,
model, stage, round, run id, and timestamp data. The archive schema keeps the
stable `engine` field name for backward compatibility; it records the resolved
agent command/runtime used for the call.

## Agent CLI Session Behavior

For the exact fresh-versus-resumed session behavior of every default, Phased
ATDD, and Dual Spec stage, including `RESUME_RUN`, see
[`docs/agent-session-lifecycle.md`](docs/agent-session-lifecycle.md).

| | Claude | Codex | Agy | OpenCode |
|---|---|---|---|---|
| Non-interactive call | `claude -p --output-format stream-json` | `codex exec --json` | `agy --print` | `opencode run --format json --auto` |
| Worker resume | `--resume <id>` | `exec resume ... <thread-id>` | `--conversation <conversation-id>` | `--session <id>` |
| ID source | Structured response | `thread.started` JSONL event | Per-attempt `--log-file` record | JSONL `sessionID` |
| Permission mode | `acceptEdits` + `TOOLS` | `--sandbox workspace-write` | `--dangerously-skip-permissions` | `--auto` (user deny rules still apply) |
| Reasoning level | `CLAUDE_ARGS='--effort=low'` | `CODEX_ARGS='-c model_reasoning_effort=low'` | `AGY_ARGS='--effort=low'` | `OPENCODE_ARGS='--variant low'` |
| Live output | Messages and a one-line summary per tool call | Messages and a one-line summary per tool call | Raw merged output | Messages and a one-line summary per tool call (at completion) |

Claude, Codex, Agy, and OpenCode may each be used in A, B, and I. OpenCode
is the BYO-model runtime: `MODEL_A=google/gemini-2.5-pro` and
`MODEL_B=ollama/qwen3.6` do not need new AAC adapters. Two OpenCode slots
still share one runtime; pair OpenCode with Claude or Codex when you want
different tool stacks. Worker calls resume only
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

The workflow never falls back to Codex `--last`, Agy `--continue`, or OpenCode
`-c`: if a fresh call does not yield an ID, it warns and starts fresh again
next time; if an established session later omits the ID, the known ID is
retained. Claude, Codex, and OpenCode JSONL and Agy logs are archived as
per-attempt `.cli.raw` artifacts for diagnosis.

All built-in agents stream their output while they work, so a long step is rarely a
silent wait. Every streamed line is prefixed with its slot and command, as in
`[A claude] `, and printed in the `AGENT` color category. The prefix is what
keeps an agent's own `### heading` from being read as a workflow checkpoint, and
it is added at print time only: archived artifacts and the run log never contain
it. Claude, Codex, and OpenCode also report each tool call as one line naming the
tool and the file, command, or pattern it acts on; the rest of the tool input is
dropped, so a large write still costs one short line. Claude and Codex report a
tool call when it starts, so a ten-minute command is visible while it runs.
OpenCode reports one only once the call has finished, marking it `(failed)` when
it did, so a slow tool call is silent until it returns. Codex reports a shell call
as the full interpreter invocation, so the `powershell -Command` or `bash -c` wrapper is stripped and only the command you care about is shown.

Session, output, sandbox, and log flags belong to the workflow. Reserved
flags per built-in command:

| Argument variable | Reserved flags |
|---|---|
| `CLAUDE_ARGS`, Claude-targeted `IMPL_ARGS` | `-c` / `--continue`, `-r` / `--resume`, `--session-id`, `--fork-session`, `--no-session-persistence`, `--from-pr`; no override of the structured output contract through `--output-format`, `--verbose`, or `--json-schema` |
| `CODEX_ARGS`, Codex-targeted `IMPL_ARGS` | `--json`, `resume`, `--sandbox` / `-s`, `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--ephemeral`; no `sandbox_mode` override through `-c` / `--config` |
| `AGY_ARGS`, Agy-targeted `IMPL_ARGS` | `--log-file`, `--continue`, `--conversation` |
| `OPENCODE_ARGS`, OpenCode-targeted `IMPL_ARGS` | `--format`, `--session` / `-s`, `--continue` / `-c`, `--fork`, `--attach`, `--auto`, `--share`, `--command`, `--dir` |

For every built-in argument variable:

- A model may not be set through `--model`, `-m`, or Codex
  `-c model=` / `--config model=`. Use `MODEL_A`, `MODEL_B`, or `IMPL_MODEL`
  so actual calls and archived metadata agree.
- Attached short forms such as `-mMODEL`, `-sVALUE`, and `-cVALUE` are parsed
  by the same reserved-option rules.
- Custom argument variables are passed through instead, so custom model and
  session flags may be supplied there.

Agy conversation IDs depend on its current log wording; an incompatible Agy
upgrade degrades safely to a warning and fresh sessions rather than resuming an
unrelated conversation.

All built-in and custom agent argument variables use POSIX shell quoting on
every platform. Quote values to preserve embedded spaces. On Windows, quote
paths containing backslashes or write them with `/`; unquoted backslashes have
their POSIX escape meaning.

### Reasoning level

AAC has no single `REASONING` variable. Each built-in CLI has its own flag;
put it in that command's `*_ARGS` (or `IMPL_ARGS` when slot I uses that
command). `MODEL_*` still selects the model. Accepted values are owned by
the CLI and can change; `claude --help`, `codex exec --help`, `agy --help`,
and `opencode run --help` are authoritative.

| Agent | Variable | Flag | Values verified on this project's CLIs |
|---|---|---|---|
| Claude | `CLAUDE_ARGS` | `--effort=low` | `low`, `medium`, `high`, `xhigh`, `max` |
| Codex | `CODEX_ARGS` | `-c model_reasoning_effort=low` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh` (model-dependent). `-c model=` is reserved; this key is not. |
| Agy | `AGY_ARGS` | `--effort=low` | `low`, `medium`, `high` |
| OpenCode | `OPENCODE_ARGS` | `--variant low` | Provider-specific. OpenCode's help cites `high`, `max`, `minimal`. |

The same flag on slot I uses `IMPL_ARGS` after the command-wide `*_ARGS`:

```bash
CLAUDE_ARGS='--effort=high' \
CODEX_ARGS='-c model_reasoning_effort=medium' \
AGENT_A=claude AGENT_B=codex \
IMPL_AGENT=codex IMPL_ARGS='-c model_reasoning_effort="low"' \
  aac request.md
```

```bash
AGENT_A=opencode MODEL_A=xai/grok-4.6 \
OPENCODE_ARGS='--variant low' \
AGENT_B=agy AGY_ARGS='--effort=low' \
  aac request.md
```

## Protected Acceptance Tests

Acceptance tests are written by the reviewer and then protected. During
implementation, the worker must not edit, delete, or skip files listed in
`aac/.run/protected-tests.txt`.

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
commit SHA to `aac/.run/protected-base.sha`. If the test should no longer be
protected, a human may instead remove its path from
`aac/.run/protected-tests.txt`. Resume only after the manual controls describe
the intended trusted state.

## Safety Notes

- Deterministic gates are run by the workflow, not trusted from AI output.
- `agy` currently uses `--dangerously-skip-permissions`; prefer a worktree or
  container when using it.
- `opencode` runs with `--auto`, which approves every permission you have not
  explicitly denied in your OpenCode config. Deny rules are the only filter, so
  write them first or prefer a worktree or container, as with `agy`.
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
  custom CLI. Identical built-in Claude, Codex, Agy, and OpenCode slots are supported.

## Testing This Repository

Run the fast suite while you work. This is the default selection and finishes
in under a minute:

```bash
uv run pytest -q
```

Run the whole offline suite before pushing. This adds the tests marked `slow`,
each of which drives a complete workflow run against fake agents, and it is
exactly what CI runs:

```bash
uv run pytest -q -m "not e2e"
```

Neither of those calls any AI agent. Both run across all cores, because
`-n auto` is on by default.

Run the full E2E only when changing core workflow behavior. It calls real AI
agents and consumes quota:

```bash
uv run pytest -m e2e -s
```

## Troubleshooting

### Reviewer did not write `verdict.json`

The reviewer failed or did not follow the rules. For custom reviewers, verify
that the command can write `aac/.run/review.md` and `aac/.run/verdict.json`.
Check the run archive under `aac/.run/archive/<RUN_ID>/`, whose
`logs/001-run.log` holds the whole run.

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
agent. Detection reads only the agent's own error channel, never the output of a
command the agent ran, so a test suite that happens to print the words "rate
limit" cannot send the run to sleep. For Claude that channel is the structured
response, and its reported status decides on its own; for Codex it is the `error`
and `turn.failed` events plus anything the CLI writes outside JSON. OpenCode uses
`error` events from `--format json`, plus anything it writes outside JSON, and
keeps the HTTP status the provider reported next to its message, because OpenCode
passes provider wording through and only some providers say "rate limit" on a
429. Agy has no structured channel, so its whole output is still scanned.

When the agent states when the quota returns, the workflow waits exactly that long
instead of guessing. Claude reports an exact reset time in its stream, which is
used directly; otherwise four message shapes are understood:

| Message | Agent | Wait |
| --- | --- | --- |
| Reported reset time | Claude | Until that moment, plus 2 minutes |
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
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [GitHub Spec Kit](https://github.com/github/spec-kit)
