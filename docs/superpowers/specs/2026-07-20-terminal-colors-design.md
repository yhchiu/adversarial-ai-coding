# Terminal Color Output Design

Date: 2026-07-20
Status: Approved

## Goal

Add configurable ANSI colors to the workflow's own terminal status messages
so runs are easier to follow. Colors must never reach the run log file,
redirected output, or non-terminal streams.

## Scope

- Color only the workflow's own frame messages (stage banners, progress,
  errors, warnings, human checkpoints, success lines).
- Do NOT color: agent raw output dumps, file contents, metrics summaries,
  usage text, or any archived artifact.
- Out of scope (stay plain in v1): the deep bare-print warnings at
  `runstate.py` (empty plan task list) and `agents.py` (notification
  command failure), and the raw-output dumps on agent failure.

## Architecture

One new zero-dependency module: `src/adversarial_ai_coding/style.py`
(~150-200 lines). Three parts:

### 1. Categories and classifier

`classify(line) -> category | None` works on a single line with
surrounding whitespace stripped. First matching rule wins:

| Category     | Rule (existing message conventions)                             |
| ------------ | --------------------------------------------------------------- |
| `error`      | starts with `!! `                                               |
| `stage`      | starts with `====`, `== skip`, or `--- Task`                    |
| `checkpoint` | starts with `### `                                              |
| `progress`   | starts with `>>> `                                              |
| `warning`    | starts with `(` and ends with `)` — the parenthetical-note convention, e.g. `(warning: ...)`, `(worker left uncommitted changes; ...)`, `(reviewer did not write verdict.json; ...)` |
| `success`    | ends with `check passed`, `Review approved`, or `approved by human` (exact fixed phrases, locked by tests) |
| (none)       | anything else — printed unstyled                                |

Multi-line messages are classified and painted per line (each styled line
gets its own SGR prefix and reset suffix). Example: in
`!! Protected acceptance test files were modified:\n{listing}` only the
first line is red; the listing stays plain. This keeps the
"frame vs content" separation and prevents color bleeding across lines.

### 2. Themes

```python
THEMES = {"dark": {...}, "light": {...}}  # category -> raw SGR params
```

| Category     | dark (default) | light  |
| ------------ | -------------- | ------ |
| `error`      | `1;91`         | `1;31` |
| `stage`      | `1;96`         | `1;34` |
| `checkpoint` | `1;95`         | `1;35` |
| `progress`   | `36`           | `34`   |
| `warning`    | `93`           | `33`   |
| `success`    | `32`           | `32`   |

### 3. Styler

- `Styler.from_env(env)` — parses config, probes `sys.stdout` /
  `sys.stderr` isatty separately, enables Windows VT.
- `styler.paint(text)` — returns styled text.
- `styler.out(text)` / `styler.err(text)` — print to stdout/stderr with
  per-stream styling decisions.

Color is applied only at print time. Message strings themselves are never
modified, so `ctx.log_file` (run log), archives, and metrics stay plain
automatically.

## Configuration

Environment variables only (project convention). Never persisted to the
resume snapshot — like `NOTIFY_CMD`, color settings are per-attempt
presentation settings.

| Variable                  | Values                    | Default      |
| ------------------------- | ------------------------- | ------------ |
| `COLOR`                   | `auto` / `always` / `never` | `auto`     |
| `COLOR_THEME`             | `dark` / `light`          | `dark`       |
| `COLOR_STAGE`             | color name or raw SGR     | (from theme) |
| `COLOR_PROGRESS`          | color name or raw SGR     | (from theme) |
| `COLOR_ERROR`             | color name or raw SGR     | (from theme) |
| `COLOR_WARNING`           | color name or raw SGR     | (from theme) |
| `COLOR_CHECKPOINT`        | color name or raw SGR     | (from theme) |
| `COLOR_SUCCESS`           | color name or raw SGR     | (from theme) |

Per-category override values accept either:

- a color name: one of `black`, `red`, `green`, `yellow`, `blue`,
  `magenta`, `cyan`, `white` (SGR 30-37), optionally prefixed with
  `bright-` (SGR 90-97) and/or `bold-` (prepends `1;`), in the order
  `bold-bright-<name>` — e.g. `red`, `bright-cyan`, `bold-bright-red`; or
- raw SGR parameters (digits and semicolons only, e.g. `1;91`).

Empty string means unset (project convention `env.get(key) or default`).

### On/off precedence

1. Explicit `COLOR=always` or `COLOR=never` wins over everything.
2. Otherwise `NO_COLOR` set (non-empty) -> off (no-color.org).
3. Otherwise `FORCE_COLOR` set (non-empty) -> on.
4. Otherwise `auto`: per-stream — stdout is colored iff
   `sys.stdout.isatty()`, stderr iff `sys.stderr.isatty()`.
   `TERM=dumb` -> off. This keeps control codes out of I/O redirection:
   `adversarial-ai-coding task > out.txt` gives plain stdout while stderr
   stays colored.

### Windows

At startup, for each stream that resolved to colored, enable
`ENABLE_VIRTUAL_TERMINAL_PROCESSING` via ctypes `SetConsoleMode` (needed
for classic conhost; Windows Terminal already supports VT). If enabling
fails: `auto` -> degrade that stream to plain; `always` -> emit anyway
(user explicitly forced).

## Error handling

- Invalid `COLOR`, `COLOR_THEME`, or `COLOR_<CATEGORY>` value -> raise
  `SettingsError` at startup naming the variable and the legal values
  (fail-fast, consistent with `_to_int`). Raised from
  `Styler.from_env(env)` inside cli.py's try block, so the existing
  `except SettingsError` handler reports it; no new handling needed.
- VT enable failure is not an error: silent degrade (see above).

## Wiring (all in cli.py)

1. Build `styler = Styler.from_env(env)` at the top of `main()`'s try
   block.
2. Pass `echo=styler.out, echo_err=styler.err` to the `WorkflowContext`
   constructor. Every `ctx.echo` / `ctx.echo_err` / `ctx.log` call site
   in workflow.py, review.py, phaseflow.py, dual_spec.py, gitops.py gets
   colors with zero call-site changes. `ctx.log_file` never goes through
   the styler.
3. Route cli.py's own categorized prints through the styler:
   `!! Workflow interrupted`, `(warning: no quality gate ...)`, and
   exception messages (`WorkflowAbort` messages mostly start with `!!`
   and classify as `error` automatically). `_abort_message` and
   `_print_resume_hint` gain an echo parameter (explicit injection,
   no global singleton).
4. Pass styler-backed callables to `bootstrap_agents_md` and
   `resume_workspace`, which already accept echo callables.
5. Content-carrying prints stay on plain `print` and must NOT go through
   the styler: `USAGE`, `print-agents` output, and `Task:{task}` (task
   text is user content and may contain lines like `### Acceptance
   Criteria` that would misclassify as frame messages).

## Testing

Unit plus light integration; no e2e (presentation-layer feature).

- style.py unit tests: on/off decision matrix
  (`COLOR` x `NO_COLOR` x `FORCE_COLOR` x isatty x `TERM=dumb`); theme
  selection; per-category overrides (names, raw SGR, invalid ->
  `SettingsError`); `classify()` against real message samples from the
  codebase; multi-line painting with per-line reset.
- Integration: `ctx.log` with a styled echo -> log file contains no ANSI
  codes; `COLOR=always` -> codes present on stdout; non-tty + auto -> no
  codes.
- Windows VT: mock the ctypes call; verify auto-degrade and
  always-forced behavior.
- Existing tests are unaffected: pytest capture is not a tty, so auto
  mode disables color and output bytes are unchanged.

## Commit plan

1. `feat(style): add color theme engine with env config` — style.py +
   unit tests.
2. `feat(cli): colorize workflow terminal output` — cli.py wiring +
   integration tests.
3. `docs(readme): document color configuration` — README.md
   `## Configuration` section + README.zh-TW.md sync.
