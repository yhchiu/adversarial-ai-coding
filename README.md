# adversarial-ai-coding

`adversarial-ai-coding` is a Python workflow for agentic software development.

## Multi-Agent Adversarial Coding Workflow

One AI agent is the worker, and a second AI agent is the reviewer that reviews
the work and writes adversarial acceptance tests.

The workflow is designed around spec-first development, deterministic quality
gates, protected acceptance tests, small commits, and human review before costly
implementation starts.

The Traditional Chinese (中文) README is available at [`README.zh-TW.md`].

## How It Works

The workflow drives two agent slots through a staged pipeline: `A` is the worker
agent and `B` is the reviewer agent. They can be different agents:

- `claude` for Claude Code CLI
- `codex` for Codex CLI
- `agy` for Antigravity CLI
- A custom agent CLI or wrapper command

Using different agent commands for worker and reviewer is recommended because their
failure modes are different.

Every step marked ⟳ in the pipeline runs the same review loop, shown in the
second diagram.

```mermaid
flowchart TD
    spec["<b>1 · Write spec</b><br/>A writes · B reviews ⟳"]
    gate{"2 · Human approves<br/>the spec?"}
    plan["<b>3 · Write plan</b><br/>A writes · B reviews ⟳"]
    tests["<b>4 · Acceptance tests</b> (roles swapped)<br/>B writes · A reviews ⟳"]
    task["<b>5 · Implement next task</b><br/>A codes · build gate · protected-test check · commit"]
    more{"Tasks left?"}
    branch["<b>6 · Full gate + branch review</b><br/>workflow runs GATE_CMD · B reviews diff ⟳"]
    final["<b>7 · Final review and fixes</b><br/>A self-review · B final acceptance ⟳"]
    fin(["<b>8 · Finish</b><br/>print push / PR commands"])
    abort(["Abort"])

    spec --> gate
    gate -- "y" --> plan
    gate -- "anything else" --> abort
    plan --> tests --> task --> more
    more -- "yes" --> task
    more -- "no" --> branch --> final --> fin
```

The ⟳ review loop is one reusable building block. The workflow, not the AI,
decides when the loop ends:

```mermaid
flowchart LR
    review["B reviews the scope"] --> verdict{"verdict.json<br/>approved?"}
    verdict -- "yes" --> done(["stage continues"])
    verdict -- "no (blockers)" --> fix["A replies to review.md<br/>and fixes"]
    fix --> dgate["deterministic gate<br/>(if configured)"] --> review
    verdict -. "MAX_ROUNDS exhausted" .-> halt(["abort + notify human"])
```

Stage notes:

1. **Write spec**: `spec.md` must include an Assumptions and Open Questions
   section, because headless AI cannot ask humans and silent guessing is
   forbidden. With `DUAL_SPEC=1`, A and B write independent candidate specs
   first; see [Dual Spec Mode](#dual-spec-mode).
2. **Human approval**: the highest-leverage checkpoint. A bad spec amplifies
   into many bad changes, so a human approves the spec (and may edit it first)
   before costly implementation starts. `HUMAN_GATE=0` skips this gate.
3. **Write plan**: `plan.md` must be a `- [ ]` checkbox task list. Each task
   maps to one commit.
4. **Acceptance tests**: adversarial TDD separates the test author from the
   implementer, so the roles swap: B writes the tests and A only reviews them.
   The test files become protected; the workflow hard-checks them with
   `git diff` after every later worker action. Red tests are expected here
   (TDD red phase).
5. **Implement tasks**: one checkbox task per commit keeps review and rollback
   small. The per-task gate is the lightweight `BUILD_GATE_CMD` (compile
   only); acceptance tests may stay red until all tasks are done.
6. **Full gate + branch review**: the workflow itself runs `GATE_CMD` — the AI's
   own "tests pass" claim is never trusted — and acceptance tests must pass
   now. B then reviews the complete branch diff.
7. **Final review and fixes**: A works through the accumulated
   `.workflow/suggestions.md` items and its own self-review findings, then B
   gives final acceptance.
8. **Finish**: the workflow prints `git push` / `gh pr create` commands and run
   metrics. `OPEN_PR=1` runs them automatically.

Review verdicts are graded. `verdict.json` is
`{approved, blockers[], suggestions[]}`: only blockers make the loop repeat,
while suggestions accumulate in `.workflow/suggestions.md` and are handled in
stage 7. This keeps a reviewer from blocking on nitpicks or approving just to
be polite.

## Requirements

- Python 3.12 or newer
- [Astral uv](https://docs.astral.sh/uv/)
- `git`
- The AI CLIs you plan to use, already installed and logged in:
  - `claude`
  - `codex`
  - `agy` is optional
- Any custom agent or wrapper commands you configure through `AGENT_A` or
  `AGENT_B`, available on `PATH`.
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

## Dual Spec Mode

Set `DUAL_SPEC=1` to make both slots write independent candidate specs before
implementation planning starts. The workflow becomes:

```text
A writes spec-a.md independently
B writes spec-b.md independently
B reviews A once, A reviews B once
A writes spec-comparison-a.md, B writes spec-comparison-b.md
Human chooses a, b, ma, or mb
Selected owner produces final spec.md
Other slot reviews final spec.md to approval
Human approves final spec.md
```

Decision commands:

- `a`: copy Candidate A to final `spec.md`
- `b`: copy Candidate B to final `spec.md`
- `ma`: use Candidate A as base, edit `.workflow/spec-merge-request.md`, and
  require A to adopt selected items from Candidate B
- `mb`: use Candidate B as base, edit `.workflow/spec-merge-request.md`, and
  require B to adopt selected items from Candidate A

After selection, the chosen owner remains the worker for planning,
implementation, and self-review. The other slot becomes the reviewer and writes
the protected acceptance tests. Dual spec mode requires an interactive terminal
and `HUMAN_GATE=1`; unattended runs should leave it disabled.

## Custom Agent Commands

If `AGENT_A` or `AGENT_B` is not `claude`, `codex`, or `agy`, the workflow
treats it as a custom agent command. The command is run with the slot-specific
args followed by a short prompt-file instruction as the final argument:

```bash
$AGENT_A $AGENT_A_ARGS "Read the full workflow prompt from this repository file and follow it exactly: .workflow/runs/<RUN_ID>/NNN-*-prompt.md"
$AGENT_B $AGENT_B_ARGS "Read the full workflow prompt from this repository file and follow it exactly: .workflow/runs/<RUN_ID>/NNN-*-prompt.md"
```

Custom commands must be agentic: they need to read the referenced prompt file,
inspect and edit the repository as needed, and exit non-zero on execution
failure. A custom reviewer must write `.workflow/review.md` and
`.workflow/verdict.json`; stdout JSON verdicts are not parsed. Custom agents
do not get automatic session resume, and `MODEL_A` / `MODEL_B` are not
translated into model flags for them. Put model flags in `AGENT_A_ARGS` /
`AGENT_B_ARGS`.

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
| `MODEL_A` | CLI default | Model override for built-in worker slots. Custom agents should pass model flags through `AGENT_A_ARGS`. |
| `MODEL_B` | CLI default | Model override for built-in reviewer slots. Custom agents should pass model flags through `AGENT_B_ARGS`. |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` | empty | Extra CLI arguments for built-in agent commands, split on whitespace and appended to agent calls. |
| `AGENT_A_ARGS` / `AGENT_B_ARGS` | empty | Extra CLI arguments for custom agent commands, split on whitespace and appended before the prompt-file instruction argument. |
| `MAX_ROUNDS` | `3` | Maximum review or quality-gate repair rounds per stage. |
| `HUMAN_GATE` | `1` | Pause for human approval after the spec review. Set `0` for unattended runs. |
| `DUAL_SPEC` | `0` | `1` enables the dual spec flow: A/B write independent candidates, cross-review once, produce comparison tables, and wait for human owner selection. Requires `HUMAN_GATE=1` and an interactive terminal. |
| `GATE_CMD` | auto-detected | Full quality gate. Go projects use `go build ./... && go vet ./... && go test ./...`, npm projects with a `test` script use `npm test`, Cargo projects use `cargo test`, and projects without a detected gate skip deterministic gates unless you set it. |
| `BUILD_GATE_CMD` | auto-detected | Lightweight per-task build gate. Go projects use `go build ./...`, Cargo projects use `cargo build`, and projects without a detected build gate skip this per-task gate unless you set it. |
| `AUTO_BRANCH` | `1` | Create an `auto/<timestamp>` branch before running. |
| `USE_WORKTREE` | `0` | Run in a separate Git worktree. |
| `OPEN_PR` | `0` | Push and create a GitHub PR at the end. By default, commands are only printed. |
| `NOTIFY_CMD` | empty | Notification command. The message is passed as the first argument. |
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

## Protected Acceptance Tests

Acceptance tests are written by the reviewer and then protected. During
implementation, the worker must not edit, delete, or skip files listed in
`.workflow/protected-tests.txt`.

If a protected test is wrong, stop and handle it manually. A human can edit the
test and update `.workflow/protected-base.sha`, or remove the file from
`.workflow/protected-tests.txt`.

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
- `codex`, `agy`, and identical custom agent commands cannot be used as both
  worker and reviewer at the same time. Use distinct wrapper command names when
  both slots share the same underlying custom CLI.

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

`HUMAN_GATE=1` requires a TTY. In unattended environments, set `HUMAN_GATE=0`
and use `NOTIFY_CMD` or PR review for human control.

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
