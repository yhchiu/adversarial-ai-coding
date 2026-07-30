# Claude Streaming Output Design

Date: 2026-07-30
Status: Approved

## Goal

The `claude` adapter is the only agent path that shows the user nothing
while it runs. Both its worker and reviewer calls use
`--output-format json` through `_run_captured`, which buffers everything
until the process exits, so a ten-minute implementation step looks
identical to a hung run.

Switch both calls to `--output-format stream-json` and echo progress as
it arrives, so the user can see what the agent is doing right now.

## Scope

- `_worker_claude` and `_reviewer_claude` stream their output.
- Every streamed agent line (claude, codex, agy, and custom wrappers)
  gains a `[<slot> <name>] ` prefix and its own dim color category, so
  agent output is never confused with the workflow's own messages.

Out of scope, recorded as follow-ups:

- The stream carries a structured `rate_limit_event` (with a `resetsAt`
  epoch and a `rateLimitType`), which is more reliable than the text
  scraping in `ratelimit.py`. Not adopted here.
- ~~`_run_codex_json` still echoes only `agent_message` items, so codex
  tool activity stays invisible. This change only adds its prefix.~~
  Done the same day; see "Codex tool activity" at the end of this file.

## Verified CLI facts

Probed against `claude` 2.1.220 before designing:

- `--output-format stream-json` requires `--verbose` under `--print`;
  without it the CLI exits 1 with
  `Error: When using --print, --output-format=stream-json requires --verbose`.
- `--json-schema` works together with `stream-json`, so the reviewer path
  needs no special handling.
- The final `result` event carries `session_id`, `total_cost_usd`,
  `result`, and `structured_output` — exactly the fields the current
  adapters read from the `--output-format json` envelope.
- Emitted event types: `system` (`hook_started`, `hook_response`,
  `init`), `assistant`, `user`, `rate_limit_event`, `result`.

## Architecture

### 1. Envelope compatibility

`_run_claude_stream(argv, io, ref) -> (rc, envelope)` replaces
`_run_captured` on the two claude paths and returns the same shape the
old call did:

- `envelope` is the raw `result` event line.
- When no `result` event arrives (a crash, a killed process, a quota
  abort), `envelope` falls back to the entire raw stream.

Everything after the call in `_worker_claude` / `_reviewer_claude` is
unchanged, so the jq-coercion semantics, the lenient invalid-envelope
divergence, and the verdict fallback all keep working as-is.

The fallback is what protects quota handling: `ratelimit.py` reads only
`agent_out`, and detects a rate limit by scraping it for
`"api_error_status": 429` and provider wording. If a stream that died
mid-flight left `agent_out` empty, quota retries would silently stop
working in exactly the case that needs them.

### 2. Artifacts

| File | Content |
| --- | --- |
| `agent_out` | The `result` event line, or the whole raw stream when there is none. Unchanged in meaning from today. |
| `raw_out` | The complete NDJSON stream. |

Prefixes are added at the echo boundary only. No file ever contains a
prefix.

### 3. Per-line rendering

`render_claude_event(line) -> (echo_lines, envelope)` is a pure function
over one NDJSON line, so the whole rendering layer is testable without a
subprocess. The streamer applies it line by line, which is what keeps the
output live.

| Line | Echoed | Envelope |
| --- | --- | --- |
| not valid JSON | the line as-is (merged stderr, CLI warnings) | — |
| valid JSON, not an object | the line as-is | — |
| `type: assistant`, `text` block | each non-empty line of the text | — |
| `type: assistant`, `tool_use` block | one summary line | — |
| `type: assistant`, other block | nothing | — |
| `type: result` | nothing | the raw line |
| `system`, `user`, `rate_limit_event`, anything else | nothing | — |

`--include-partial-messages` is deliberately not used. Messages are
echoed when they complete, so no half-sentences and no console
thrashing; the tool summaries already provide the heartbeat.

### 4. Tool summaries

One line per tool call: the tool name plus its single most identifying
argument, taken as the first present of

`file_path`, `notebook_path`, `command`, `pattern`, `url`, `query`,
`path`, `prompt`, `description`

`pattern` deliberately outranks `path`: a search is identified by what it
looks for, not by where it looks.

rendered as its first line and truncated to 100 characters with a
trailing `...`. Anything else in the tool input is dropped, so a `Write`
with a thousand-line body still costs one short line.

```
[A claude]  . Read src/adversarial_ai_coding/agents.py
[A claude]  . Bash pytest -q tests/test_agents.py
```

### 5. Prefix and color

Every streamed agent line is echoed as `[<slot> <name>] <text>`, where
slot is `A`, `B`, or `I` and name is the adapter. Both are already on
`AgentRef`.

`style.py` gains an `agent` category, and `classify()` returns it for any
line matching `^\[[ABI] [^\]]+\] ` before any other rule is considered.
That is what stops an agent's own markdown — `### Summary`, `>>> note` —
from being painted as a workflow human gate or progress line. agy and
codex already had this misclassification; prefixing all three fixes it
in one place.

| Category | dark | light |
| --- | --- | --- |
| `agent` | `90` | `90` |

`COLOR_AGENT` comes for free: `Styler.from_env` already derives one
`COLOR_<CATEGORY>` variable per theme key.

## Configuration

None. Streaming is always on, exactly like codex and agy, which have
never had a switch. Since `agent_out` keeps its meaning, an off switch
would not protect anything.

`--verbose` joins `--output-format` and `--json-schema` in the
workflow-owned argument list that `CLAUDE_ARGS` may not contain.

## Testing

- `render_claude_event` unit tests over fixed NDJSON fixtures: assistant
  text, tool_use summaries, argument priority and truncation, non-JSON
  lines, ignored event types, the result envelope.
- `_run_claude_stream` tests: `agent_out` gets the result event,
  `raw_out` gets the whole stream, and a stream with no result event
  falls back to the raw text in `agent_out`.
- Existing claude adapter tests keep their assertions and only swap which
  runner they patch, which is the point of keeping the return shape.
- style tests: the prefix classifies as `agent`, an agent line starting
  with `### ` is not a checkpoint, and `COLOR_AGENT` overrides the theme.
- Wiring test: streamed lines reach the echo with a prefix while the file
  written alongside them does not have one.

No e2e. This is a presentation-layer change and a real claude call is
slow, costs money, and cannot run in CI.

## Commit plan

1. `docs(spec): design claude streaming output` — this file.
2. `feat(style): add an agent category for streamed agent lines` —
   style.py plus the prefix at the three streaming call sites.
3. `feat(agents): stream claude output as NDJSON` — the streamer, the
   renderer, the fallback, and the reserved `--verbose`.
4. `docs: document claude streaming and record the parity divergence` —
   README.md, README.zh-TW.md, python-port-parity.md.

## Codex tool activity

Added the same day, once claude's streaming showed how much the tool
lines carry. Codex had the same dark stretches: it echoed only
`agent_message` items, and every other event was `json.dumps`-ed into
`agent_out` without ever reaching the terminal.

Codex marks tool calls with two events. `item.started` fires when the
call begins and `item.completed` when it ends, so `item.started` is the
live heartbeat and the only one echoed — a ten-minute command is visible
up front rather than after the fact. Exit codes are not echoed, matching
the decision not to print tool results for claude; the workflow's own
gates are what decide pass or fail.

Verified item types (codex-cli 0.146.0): `command_execution` (with
`command`, `status`, and on completion `exit_code` and
`aggregated_output`) and `file_change` (with `changes: [{path, kind}]`).

Rendering, in `render_codex_event`, a pure per-line function mirroring
`render_claude_event`:

| Item | Line |
| --- | --- |
| `command_execution` | ` . run <command>` |
| `file_change` | ` . edit <path> (<kind>)`, plus ` +N more` beyond the first change |
| anything else | ` . <item.type>`, so a codex upgrade is visible without a code change |

Codex reports a shell call as the whole interpreter invocation, so on
Windows the first ~66 characters are the `powershell.exe` path and the
real command falls outside the truncation limit. The wrapper is stripped
by slicing the original string after the `-Command` / `-c` / `/c` flag,
never by tokenizing and rejoining, which would lose the agent's own
quoting. Stripping only applies when the leading program really is a
shell, or `git -c user.name=x commit` would be mauled into
`user.name=x commit`.

File paths are shown relative to the working directory, and left
absolute when they fall outside it.

These summary lines replace what `item.started` used to dump into
`agent_out`, which is a straight improvement to that artifact. Every
other event keeps its existing handling: `item.completed`, `error`,
`turn.failed`, and unknown event types still render exactly as before,
so quota wording continues to reach `ratelimit.py` through `agent_out`
and unknown events stay diagnosable. The `aggregated_output` of a
completed command is still recorded as raw JSON; trimming that belongs
to a separate data-quality change.

Committed as one commit: implementation, tests, and documentation
together, because the README live-output row and this file's follow-up
note are wrong the moment the code lands.
