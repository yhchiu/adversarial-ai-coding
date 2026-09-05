# Troubleshooting

For the Traditional Chinese version, see
[疑難排解](troubleshooting.zh-TW.md).

## Start with the run artifacts

Do not rerun the whole workflow until you know which stage failed. Replace
`<RUN_ID>` below with the run ID printed by AAC.

| Evidence | Location | What it tells you |
| --- | --- | --- |
| Complete run log | `aac/.run/archive/<RUN_ID>/logs/001-run.log` | Stage transitions, commands, retries, and the final error |
| Last readable agent output | `aac/.run/last-agent-output.txt` | The text AAC rendered from the most recent agent call |
| Last raw CLI stream | `aac/.run/last-agent-cli.raw` | Unmodified JSONL or CLI output for adapter/schema problems |
| Resolved settings | `aac/.run/state/<RUN_ID>/settings.json` | The settings saved for resume |
| Stage ledger | `aac/.run/state/<RUN_ID>/ledger.json` | Which stages completed and will be skipped on resume |

After correcting the cause, use the exact `RESUME_RUN=...` command printed by
AAC. Resume skips completed stages, so it avoids paying for work that already
succeeded.

## Reviewer did not write `verdict.json`

AAC reads reviewer artifacts from files; it does not parse a JSON verdict from
stdout. A reviewer must write:

- the review text to `aac/.run/review.md`; and
- the machine-readable verdict to `aac/.run/verdict.json` with the shape
  `{"approved": false, "blockers": ["must-fix issue"], "suggestions": []}`.

Check `logs/001-run.log` for the reviewer exit code and the last tool call. If a
built-in reviewer was denied a command, fix its permissions as described below.
If this is a custom reviewer, fix the wrapper so it writes both files in the
current repository. Repeated missing or invalid verdicts consume review rounds
and eventually stop at `MAX_ROUNDS`.

## The run is stuck on a permission prompt

AAC calls agents non-interactively, so nobody can answer a prompt.

| Adapter | Arguments and settings added by AAC | Effective behavior |
| --- | --- | --- |
| Claude Code | `--permission-mode acceptEdits --allowedTools <TOOLS>` | File edits and common filesystem commands are approved by `acceptEdits`; `TOOLS` supplies additional pre-approved tool rules. |
| Codex | Fresh workers and reviewers use `--sandbox workspace-write`; resumed workers use `-c sandbox_mode="workspace-write"`. AAC does not add `--approve-for-me` or otherwise override the approval reviewer. | Codex enforces a workspace-write sandbox and inherits its approval policy and reviewer from Codex configuration or permitted user arguments. |
| Antigravity (`agy`) | `--dangerously-skip-permissions` | All tool permission prompts are bypassed; AAC does not add a separate sandbox. |
| OpenCode | `--auto` | Every permission not explicitly denied by the user's OpenCode configuration is approved automatically. |
| Custom adapter | None | The wrapper command and `AGENT_A_ARGS`, `AGENT_B_ARGS`, or `IMPL_ARGS` own all permission and sandbox behavior. |

The remedy is different for each adapter.

### Claude Code: put the exact command in `TOOLS`

AAC passes `TOOLS` to Claude Code as `--allowedTools`. The default is
detected from the workspace: `Bash(git *)` always, and then the rules
of every ecosystem the workspace holds.

| Detected from | Rules added |
|---|---|
| always | `Bash(git *)` |
| `go.mod` | `Bash(go test *),Bash(go build *),Bash(go vet *)` |
| `package.json` | `Bash(npm test)` |
| `Cargo.toml` | `Bash(cargo build),Bash(cargo test)` |
| a Python project naming pytest | `Bash(pytest *),Bash(uv run pytest *),Bash(poetry run pytest *),Bash(python -m pytest *),Bash(python3 -m pytest *)` |

A workspace that matches none of them keeps every rule above, because
its gate is one you set by hand and a narrower guess would only take
away rules the run had. The startup line prints what a run resolved to.

Setting `TOOLS` replaces that entire value; it does not append to it. Preserve
every rule the run still needs and add the blocked command explicitly. A Go
project that also needs formatting can state just what it uses:

```bash
TOOLS='Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *),Bash(gofmt *)' \
  aac request.md
```

The rule added in that example is `Bash(gofmt *)`; everything else in it is
the Go part of the default, which covers every ecosystem gate detection
knows. Other least-privilege starting points are:

```bash
# npm project: allow only the scripts this workflow needs.
TOOLS='Bash(git *),Bash(npm test),Bash(npm run build),Bash(npm run lint)' \
  aac request.md

# Cargo project: allow the auto-detected build and full gates.
TOOLS='Bash(git *),Bash(cargo build),Bash(cargo test)' \
  aac request.md

# Python project with a custom pytest gate.
TOOLS='Bash(git *),Bash(uv run pytest *)' \
  aac request.md
```

Use the narrowest command pattern that matches the command shown in the log.
Do not replace the Go rules with `Bash(go *)`: that also permits `go run`, which
can execute arbitrary code. The allowlist applies to both Claude workers and
Claude reviewers. See the
[Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
for the current `--allowedTools` rule syntax.

### Codex

AAC owns Codex's sandbox configuration and launches it with
`workspace-write`, including resumed sessions. Do not add `--sandbox`, `-s`,
`--yolo`, or a `sandbox_mode` override to `AGENT_A_ARGS`, `AGENT_B_ARGS`, or
`IMPL_ARGS`; AAC rejects those reserved options during preflight.

If Codex cannot write a file, confirm that the target is inside the repository
and is writable by the current OS user. A path outside the workspace is not a
reason to disable the sandbox; move the artifact into the repository or perform
that external operation separately.

### Antigravity (`agy`)

AAC already starts `agy` with `--dangerously-skip-permissions`. If a current
version still prompts, record the exact prompt and compare `agy --help` with the
installed version; there is no additional AAC permission setting to enable.
Because this mode is broad, use `USE_WORKTREE=1` or a container.

### OpenCode

AAC starts OpenCode with `--auto`. A deny rule in the user's OpenCode config
still wins. Inspect the denied tool/path in `logs/001-run.log`, then narrow or
remove that deny rule only if the operation is intended.

## No interactive terminal is available for approval

`HUMAN_GATE=1` requires a TTY. `HUMAN_GATE_PLAN=1` independently requires a
TTY and is checked before the first AI call. For CI or another unattended
environment, use:

```bash
HUMAN_GATE=0 HUMAN_GATE_PLAN=0 NOTIFY_CMD='your-notifier' aac request.md
```

This removes interactive approval; it does not create an equivalent safety
gate. Use protected tests, deterministic `GATE_CMD` checks, and PR review to
replace that control.

## The per-task quality gate keeps failing

The two gates serve different points in the workflow:

- `BUILD_GATE_CMD` runs after each implementation task and should be a fast
  compile or type-check. Acceptance tests may legitimately remain red until
  later tasks are complete.
- `GATE_CMD` runs after all implementation tasks and should contain the full
  build, lint, and test suite.

Go defaults are `go build ./...` per task and
`go build ./... && go vet ./... && go test ./...` at the full gate. Cargo uses
`cargo build` and `cargo test`. An npm project with a `test` script uses
`npm test` for the full gate and has no auto-detected per-task build gate.

If `BUILD_GATE_CMD` currently runs the full acceptance suite, replace it with a
compile-only command or leave it empty when no meaningful fast gate exists.
Remember that the workflow runs these gate commands itself; `TOOLS` matters
only when a Claude agent also tries to run a command while diagnosing or fixing
the failure.

## Reviewer reports corrupted files on Windows

First inspect `git diff` or the file bytes outside the agent. If the file is
valid UTF-8 and only the review is garbled, keep generated specs, plans, and
test data ASCII when practical. Represent non-ASCII source test data with
Unicode escapes such as `\u4e0a`, following
[`resources/AGENTS.template.md`](../resources/AGENTS.template.md).

Do not rewrite a valid file merely because one agent rendered it with the
Windows system code page. If the bytes really changed, restore only the
affected file from version control and resume the run.

## Rate-limit and quota errors

AAC reads only an agent's own error channel for quota detection. It does not
scan command output produced by the agent, so a test named
`test_ratelimit_parsing.py` cannot accidentally put a run to sleep.

The channel differs by adapter:

- Claude uses its structured result and reported reset epoch.
- Codex uses `error` and `turn.failed` events plus non-JSON CLI lines.
- OpenCode uses `error` events plus non-JSON CLI lines, retaining the provider's
  HTTP status because provider wording varies.
- Agy has no structured event boundary, so its complete output is scanned.

When a reset time is available, AAC waits until that time with a small buffer:

| Message or field | Agent | Wait |
| --- | --- | --- |
| Reported reset epoch | Claude | Until that moment, plus 2 minutes |
| `resets 10:50am` | Claude | Until that clock time, plus 2 minutes |
| `try again in 90s` | Codex | That duration, plus 30 seconds |
| `try again at Jul 14th, 2026 7:23 PM` | Codex | Until that timestamp, plus 30 seconds |
| `try again at 12:50 AM` | Codex | Until the next occurrence, plus 30 seconds |

An xAI/OpenCode `personal-team-blocked:spending-limit` error and a Grok Build
`usage balance exhausted` error require account action. AAC immediately exits
with code 75 without sleeping or resending. Other limit messages without a
parseable reset time use exponential backoff from `RETRY_BASE_WAIT`, capped by
`RETRY_MAX_WAIT` and `RETRY_MAX`.

If a parsed reset is farther away than `RETRY_MAX_RESET_WAIT` (six hours by
default), AAC exits with code 75 instead of sleeping for days. Exit code 75 is a
resumable quota abort: wait for the reset or change the exhausted agent/model,
then use the printed `RESUME_RUN` command. Set `RETRY_ON_LIMIT=0` when every
limit should fail immediately.
