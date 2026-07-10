# adversarial-ai-coding

`adversarial-ai-coding` is a Bash workflow for agentic software development.

## Multi-Agent Adversarial Coding Workflow

One AI agent is the worker, and a second AI agent is the reviewer that reviews
the work and writes adversarial acceptance tests.

The workflow is designed around spec-first development, deterministic quality
gates, protected acceptance tests, small commits, and human review before costly
implementation starts.

The Traditional Chinese (中文) README is available at [`README.zh-TW.md`].

## How It Works

The script runs a staged workflow:

```text
Write spec             Worker writes, reviewer checks
                       Optional DUAL_SPEC=1: A/B write independent specs,
                       cross-review once, compare, then human selects owner
Final spec approval    Reviewer checks the final spec, then human approves it
Write plan             Worker creates checkbox tasks
Write acceptance tests Reviewer writes tests, worker reviews them
Implement tasks        Worker completes one checkbox task per commit
Full gate + review     Worker runs the full gate if configured, reviewer checks the branch
Final review           Worker self-reviews, reviewer gives final approval
Finish                 Script prints push and PR commands
```

The worker and reviewer can be different agents:

- `claude` for Claude Code CLI
- `codex` for Codex CLI
- `agy` for Antigravity CLI
- A custom agent CLI or wrapper command

Using different agent commands for worker and reviewer is recommended because their
failure modes are different.

```mermaid
flowchart TD
  subgraph S1["Stage: Write spec (owner writes / reviewer reviews)"]
    spec["spec.md includes Assumptions and Open Questions<br/>Headless AI cannot ask humans, so assumptions must be explicit."]
  end

  subgraph S2["Stage: Human approval"]
    human{"HUMAN_GATE approval<br/>Review the spec before costly implementation starts."}
  end

  subgraph S3["Stage: Write implementation plan (owner writes / reviewer reviews)"]
    plan["plan.md uses '- [ ]' checkbox tasks<br/>Each task maps to one commit."]
  end

  subgraph S4["Stage: Write acceptance tests (reviewer writes / owner reviews)"]
    tests["Adversarial TDD separates test author from worker.<br/>Protected acceptance tests may be red at first."]
  end

  subgraph S5["Stage: Implement tasks"]
    tasks["For each checkbox task:<br/>Owner implements -> lightweight build gate if configured -> protected-test diff check -> commit"]
  end

  subgraph S6["Stage: Full quality gate and branch review"]
    fullgate["Run the full quality gate if configured.<br/>Acceptance tests must pass when the gate runs."]
    branchreview["Reviewer reviews the complete branch diff."]
    fullgate --> branchreview
  end

  subgraph S7["Stage: Final review and fixes"]
    final["Owner handles accumulated suggestions and self-review findings.<br/>Run gates if configured, then reviewer performs final acceptance."]
  end

  subgraph S8["Stage: Finish"]
    finish["Print git push / gh pr create commands and run metrics.<br/>OPEN_PR=1 runs push and PR creation automatically."]
  end

  spec --> human --> plan --> tests --> tasks --> fullgate --> branchreview --> final --> finish

  style S1 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S2 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S3 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S4 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S5 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S6 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S7 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
  style S8 fill:#ffffff,stroke:#6b7280,stroke-width:1.5px,stroke-dasharray:6 4,color:#111827
```

## Requirements

- Bash. On Windows, use Git Bash or WSL.
- `git`
- [`jq`](https://jqlang.github.io/jq/)
- The AI CLIs you plan to use, already installed and logged in:
  - `claude`
  - `codex`
  - `agy` is optional
- Any custom agent or wrapper commands you configure through `AGENT_A` or
  `AGENT_B`, available on `PATH`. Legacy `ENGINE_A` / `ENGINE_B` variables are
  still accepted.
- Run the script from the root of the target Git repository.

## Quick Start

Run the installed script from the root of the project you want to work on. Keep
the `resources/` directory beside the script in the `adversarial-ai-coding`
checkout; the target project does not need a copied script or template.

```bash
cd /path/to/your-project
AAC=/path/to/adversarial-ai-coding/adversarial-ai-coding.sh
```

Run a task with the default agents, where Claude is the worker and Codex is the
reviewer:

```bash
bash "$AAC" "Add --json output to the CLI"
```

You can also write the task in a file:

```bash
bash "$AAC" task.md
```

Swap the worker and reviewer agents:

```bash
AGENT_A=codex AGENT_B=claude bash "$AAC" task.md
```

Use custom agent or wrapper commands:

```bash
AGENT_A=gemini AGENT_A_ARGS='--model gemini-2.5-pro --yolo' \
AGENT_B=my-review-wrapper AGENT_B_ARGS='--strict' \
  bash "$AAC" task.md
```

Enable dual spec mode:

```bash
DUAL_SPEC=1 bash "$AAC" task.md
```

Print the agent rules template for manual merging into an existing `AGENTS.md`:

```bash
bash "$AAC" print-agents
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

If `AGENT_A` or `AGENT_B` is not `claude`, `codex`, or `agy`, the script
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
| `AGENT_A` (`ENGINE_A`) | `claude` | Worker agent command: `claude`, `codex`, `agy`, or a custom command. `ENGINE_A` is a legacy alias. |
| `AGENT_B` (`ENGINE_B`) | `codex` | Reviewer agent command. In the acceptance-test stage, the roles are swapped. `ENGINE_B` is a legacy alias. |
| `MODEL_A` | CLI default | Model override for built-in worker slots. Custom agents should pass model flags through `AGENT_A_ARGS`. |
| `MODEL_B` | CLI default | Model override for built-in reviewer slots. Custom agents should pass model flags through `AGENT_B_ARGS`. |
| `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` | empty | Extra CLI arguments for built-in agent commands, split on whitespace and appended to agent calls. |
| `AGENT_A_ARGS` / `AGENT_B_ARGS` (`ENGINE_A_ARGS` / `ENGINE_B_ARGS`) | empty | Extra CLI arguments for custom agent commands, split on whitespace and appended before the prompt-file instruction argument. The `ENGINE_*_ARGS` names are legacy aliases. |
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
| `AGENTS_TEMPLATE` | `resources/AGENTS.template.md` beside the script | Path to the `AGENTS.md` template. |
| `PROMPTS_DIR` | `resources/prompts` beside the script | Directory for workflow prompt templates. |
| `SPEC_DIR` | `specs/<timestamp>` | Directory for `spec.md` and `plan.md`. |
| `RUNS_DIR` | `.workflow/runs` | Directory for archived workflow run artifacts. |
| `TOOLS` | git/go build/test/vet allowlist | Claude Code `--allowedTools` value. |

On Windows, if you want Go race tests in the gate, use:

```bash
GATE_CMD='go build ./... && go vet ./... && go test -race -ldflags "-extldflags=-Wl,--default-image-base-low" ./...' \
  bash "$AAC" task.md
```

## Resuming an Interrupted Run

Every run records its progress under `.workflow/state/<run-id>/`: the resolved
task, the effective settings, a stage completion ledger, and the remaining
implementation tasks. When a run aborts, it prints a paste-ready command:

```bash
RESUME_RUN=20260710-153012 ./adversarial-ai-coding.sh
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
AGENT_B=agy RESUME_RUN=last ./adversarial-ai-coding.sh
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

The script writes live workflow state under `.workflow/` and archives each run
under `.workflow/runs/<RUN_ID>/` by default.

```text
adversarial-ai-coding/
|-- adversarial-ai-coding.sh
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

- Deterministic gates are run by the script, not trusted from AI output.
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

Run helper tests. These do not call any AI agent:

```bash
bash tests/helpers.test.sh
```

Run the manual E2E fixture setup without calling AI:

```bash
E2E_SETUP_ONLY=1 bash tests/e2e/run.sh
```

Run the full E2E only when changing core workflow behavior. It calls real AI
agents and consumes quota:

```bash
bash tests/e2e/run.sh
```

On Windows, if Go cannot initialize its default build cache from Git Bash, set a
writable cache path:

```bash
export GOCACHE=/tmp/go-build-aac
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
configured, the script uses `BUILD_GATE_CMD` for per-task checks and `GATE_CMD`
after all tasks finish.

### Reviewer reports corrupted files on Windows

Some AI tools may misdecode non-ASCII UTF-8 content on Windows. Keep generated
specs, plans, and test data ASCII when possible. Represent non-ASCII source
test data with Unicode escapes, as described in `resources/AGENTS.template.md`.

### Rate limit or quota errors

By default, the script waits and retries on rate-limit or quota errors, for every
agent. When the message states how long to wait, the script waits exactly that
long instead of guessing. It understands three shapes:

| Message | Agent | Wait |
| --- | --- | --- |
| `resets 10:50am` | Claude | Until that clock time, plus 2 minutes |
| `try again in 90s` | Codex | That duration, plus 30 seconds |
| `try again at Jul 14th, 2026 7:23 PM` | Codex | Until that timestamp, plus 30 seconds |

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
