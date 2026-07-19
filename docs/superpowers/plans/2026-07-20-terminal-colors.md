# Terminal Color Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable ANSI colors to the workflow's own terminal status messages, with dark/light themes and per-category overrides, while guaranteeing the run log and redirected output never contain control codes.

**Architecture:** One new zero-dependency module `style.py` holds themes, an env-var config parser, a prefix-based line classifier, and a `Styler` that paints text only at print time. cli.py wires `styler.out` / `styler.err` into `WorkflowContext.echo` / `echo_err` and its own frame prints; no other module changes. Because `ctx.log_file` writes the raw message before echoing, log plainness is structural.

**Tech Stack:** Python 3.12+, stdlib only (re, sys, ctypes/msvcrt on Windows), pytest.

**Spec:** `docs/superpowers/specs/2026-07-20-terminal-colors-design.md`

## Global Constraints

- Zero runtime dependencies (`dependencies = []` in pyproject.toml stays empty).
- Configuration via environment variables only; color vars are NEVER written to the resume snapshot (like `NOTIFY_CMD`).
- Message TEXT must not change anywhere — colors wrap existing strings at print time only. The terminal text doubles as the log format (bash parity).
- Do not touch: agent raw-output dumps in agents.py, the bare-print warnings in runstate.py / agents.py, `USAGE`, `print-agents` output, `Task:{task}` (content-carrying prints stay plain `print`).
- Invalid color config raises `SettingsError` (fail-fast, consistent with `_to_int` in config.py).
- Run tests with `uv run pytest -q` (unit suite; never `-m e2e`).
- Commits: Conventional Commit format, detailed body, NO `Co-Authored-By` trailer.

---

### Task 1: Color engine (`style.py`)

**Files:**
- Create: `src/adversarial_ai_coding/style.py`
- Test: `tests/test_style.py`

**Interfaces:**
- Consumes: `SettingsError` from `adversarial_ai_coding.config` (existing).
- Produces (used by Task 2):
  - `Styler.from_env(env, *, stdout_isatty=None, stderr_isatty=None, enable_vt=None) -> Styler` — raises `SettingsError` on bad `COLOR` / `COLOR_THEME` / `COLOR_<CATEGORY>` values.
  - `Styler.plain() -> Styler` — never-coloring fallback.
  - `styler.out(text: str) -> None` — print to stdout, painted iff enabled. Signature-compatible with `WorkflowContext.echo`.
  - `styler.err(text: str) -> None` — print to stderr, painted iff enabled. Signature-compatible with `WorkflowContext.echo_err`.
  - `classify(line: str) -> str | None`, `THEMES`, `enable_windows_vt(stream) -> bool` (also exported for tests).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_style.py`:

```python
"""Unit tests for the terminal color engine.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

import io
import sys

import pytest

from adversarial_ai_coding.config import SettingsError
from adversarial_ai_coding.style import (
    THEMES,
    Styler,
    classify,
    enable_windows_vt,
)


def make(env=None, *, out_tty=True, err_tty=True, vt_ok=True):
    return Styler.from_env(
        env or {},
        stdout_isatty=out_tty,
        stderr_isatty=err_tty,
        enable_vt=lambda _stream: vt_ok,
    )


# --- classifier: real message samples from the codebase ---

@pytest.mark.parametrize(
    ("line", "category"),
    [
        ("!! Workflow interrupted (exit=1).", "error"),
        ("!! Protected acceptance test files were modified:", "error"),
        ("================ [spec] ================", "stage"),
        ("== skip [spec] (already completed in run test)", "stage"),
        ("--- Task 1/3:demo task ---", "stage"),
        ("### Human checkpoint: review spec.md, especially scope", "checkpoint"),
        (">>> Worker(claude) is running...", "progress"),
        ("(warning: reviewer execution failed)", "warning"),
        (
            "(worker left uncommitted changes; script is creating a "
            "fallback commit)",
            "warning",
        ),
        ("Phase red check passed", "success"),
        ("[spec] Review approved", "success"),
        ("Spec approved by human", "success"),
        ("Task:demo", None),
        ("Quality gate:go test ./...", None),
        ("", None),
        ("plain line", None),
    ],
)
def test_classify(line, category):
    assert classify(line) == category


# --- on/off decision matrix ---

def test_auto_follows_per_stream_isatty():
    s = make(out_tty=True, err_tty=False)
    assert (s.out_enabled, s.err_enabled) == (True, False)


def test_never_beats_tty():
    s = make({"COLOR": "never"})
    assert (s.out_enabled, s.err_enabled) == (False, False)


def test_always_beats_non_tty():
    s = make({"COLOR": "always"}, out_tty=False, err_tty=False)
    assert (s.out_enabled, s.err_enabled) == (True, True)


def test_no_color_disables_auto():
    assert make({"NO_COLOR": "1"}).out_enabled is False


def test_explicit_color_beats_no_color():
    assert make({"COLOR": "always", "NO_COLOR": "1"}).out_enabled is True


def test_force_color_enables_non_tty():
    assert make({"FORCE_COLOR": "1"}, out_tty=False).out_enabled is True


def test_no_color_beats_force_color():
    assert make({"NO_COLOR": "1", "FORCE_COLOR": "1"}).out_enabled is False


def test_term_dumb_disables_auto():
    assert make({"TERM": "dumb"}).out_enabled is False


def test_invalid_color_mode_raises():
    with pytest.raises(SettingsError, match="COLOR must be auto, always, or never"):
        make({"COLOR": "sometimes"})


def test_empty_color_means_auto():
    assert make({"COLOR": ""}).out_enabled is True


# --- themes and per-category overrides ---

def test_dark_is_default_theme():
    assert make().colors == THEMES["dark"]
    assert make().colors["error"] == "1;91"


def test_light_theme_selected():
    assert make({"COLOR_THEME": "light"}).colors == THEMES["light"]
    assert make({"COLOR_THEME": "light"}).colors["error"] == "1;31"


def test_invalid_theme_raises():
    with pytest.raises(SettingsError, match="COLOR_THEME must be dark or light"):
        make({"COLOR_THEME": "solarized"})


def test_override_color_name():
    assert make({"COLOR_ERROR": "blue"}).colors["error"] == "34"


def test_override_bright_name():
    assert make({"COLOR_PROGRESS": "bright-cyan"}).colors["progress"] == "96"


def test_override_bold_bright_name():
    assert make({"COLOR_ERROR": "bold-bright-red"}).colors["error"] == "1;91"


def test_override_raw_sgr():
    assert make({"COLOR_STAGE": "1;42;30"}).colors["stage"] == "1;42;30"


def test_override_invalid_raises_naming_the_variable():
    with pytest.raises(SettingsError, match="COLOR_ERROR"):
        make({"COLOR_ERROR": "salmon"})


def test_override_wrong_prefix_order_raises():
    with pytest.raises(SettingsError, match="COLOR_ERROR"):
        make({"COLOR_ERROR": "bright-bold-red"})


def test_empty_override_means_theme_default():
    assert make({"COLOR_ERROR": ""}).colors["error"] == THEMES["dark"]["error"]


# --- Windows VT ---

def test_vt_failure_degrades_auto():
    s = make(vt_ok=False)
    assert (s.out_enabled, s.err_enabled) == (False, False)


def test_vt_failure_keeps_always():
    assert make({"COLOR": "always"}, vt_ok=False).out_enabled is True


def test_vt_failure_keeps_force_color():
    assert make({"FORCE_COLOR": "1"}, out_tty=False, vt_ok=False).out_enabled is True


def test_vt_not_probed_when_disabled():
    def boom(_stream):
        raise AssertionError("enable_vt must not be called when color is off")

    Styler.from_env(
        {"COLOR": "never"},
        stdout_isatty=True,
        stderr_isatty=True,
        enable_vt=boom,
    )


def test_enable_windows_vt_handles_non_console_stream():
    # io.StringIO has no OS handle: False on Windows, trivially True elsewhere.
    assert enable_windows_vt(io.StringIO()) is (sys.platform != "win32")


# --- painting ---

def test_paint_wraps_classified_line_with_sgr_and_reset():
    s = make({"COLOR": "always"})
    assert (
        s.paint(">>> Worker(claude) is running...")
        == "\x1b[36m>>> Worker(claude) is running...\x1b[0m"
    )


def test_paint_leaves_unclassified_lines_alone():
    s = make({"COLOR": "always"})
    assert s.paint("plain line") == "plain line"


def test_paint_styles_banner_after_leading_newline():
    s = make({"COLOR": "always"})
    first, second = s.paint(
        "\n================ [spec] ================"
    ).split("\n", 1)
    assert first == ""
    assert second == "\x1b[1;96m================ [spec] ================\x1b[0m"


def test_paint_multiline_styles_only_classified_lines():
    s = make({"COLOR": "always"})
    lines = s.paint(
        "!! Protected acceptance test files were modified:\n  tests/a_test.py"
    ).split("\n")
    assert lines[0] == (
        "\x1b[1;91m!! Protected acceptance test files were modified:\x1b[0m"
    )
    assert lines[1] == "  tests/a_test.py"


# --- printing and the plain() fallback ---

def test_out_paints_only_when_enabled(capsys):
    make({"COLOR": "always"}).out(">>> Worker(claude) is running...")
    make({"COLOR": "never"}).out(">>> Worker(claude) is running...")
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "\x1b[36m>>> Worker(claude) is running...\x1b[0m"
    assert out[1] == ">>> Worker(claude) is running..."


def test_err_prints_to_stderr_painted(capsys):
    make({"COLOR": "always"}).err("!! boom")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "\x1b[1;91m!! boom\x1b[0m\n"


def test_plain_styler_never_colors(capsys):
    s = Styler.plain()
    assert (s.out_enabled, s.err_enabled) == (False, False)
    s.err("!! boom")
    assert capsys.readouterr().err == "!! boom\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_style.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'adversarial_ai_coding.style'`

- [ ] **Step 3: Write the implementation**

Create `src/adversarial_ai_coding/style.py`:

```python
"""Terminal color styling for the workflow's own status messages.

Colors are applied only at print time (the echo boundary wired in
cli.py), so message strings, the run log, and archived artifacts always
stay plain text. Configuration is environment-only and never persisted
to the resume snapshot -- like NOTIFY_CMD, colors are per-attempt
presentation settings.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, TextIO

from .config import SettingsError

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "error": "1;91",
        "stage": "1;96",
        "checkpoint": "1;95",
        "progress": "36",
        "warning": "93",
        "success": "32",
    },
    "light": {
        "error": "1;31",
        "stage": "1;34",
        "checkpoint": "1;35",
        "progress": "34",
        "warning": "33",
        "success": "32",
    },
}

_NAMED_COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}

_RAW_SGR = re.compile(r"^[0-9]+(;[0-9]+)*$")

# Fixed phrases from workflow.py / review.py / phaseflow.py; the only
# non-prefix classification rules.
_SUCCESS_SUFFIXES = ("check passed", "Review approved", "approved by human")

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


def classify(line: str) -> str | None:
    """Map one output line to a style category by message conventions."""
    text = line.strip()
    if text.startswith("!! "):
        return "error"
    if text.startswith(("====", "== skip", "--- Task")):
        return "stage"
    if text.startswith("### "):
        return "checkpoint"
    if text.startswith(">>> "):
        return "progress"
    if text.startswith("(") and text.endswith(")"):
        return "warning"
    if text.endswith(_SUCCESS_SUFFIXES):
        return "success"
    return None


def _sgr_from_value(name: str, raw: str) -> str:
    """Parse a COLOR_<CATEGORY> value: a color name or raw SGR params."""
    if _RAW_SGR.match(raw):
        return raw
    parts = raw.split("-")
    bold = parts[0] == "bold"
    if bold:
        parts = parts[1:]
    bright = bool(parts) and parts[0] == "bright"
    if bright:
        parts = parts[1:]
    if len(parts) == 1 and parts[0] in _NAMED_COLORS:
        code = _NAMED_COLORS[parts[0]] + (60 if bright else 0)
        return f"1;{code}" if bold else str(code)
    raise SettingsError(
        f"{name} must be a color name like red, bright-cyan, or "
        f"bold-bright-red, or raw SGR parameters like 1;91, got: {raw}"
    )


def _resolve_mode(env: Mapping[str, str]) -> str:
    mode = env.get("COLOR") or "auto"
    if mode not in ("auto", "always", "never"):
        raise SettingsError(
            f"COLOR must be auto, always, or never, got: {mode}"
        )
    return mode


def _stream_enabled(
    mode: str, env: Mapping[str, str], isatty: bool
) -> tuple[bool, bool]:
    """Return (enabled, forced); forced skips the VT-failure degrade."""
    if mode == "always":
        return True, True
    if mode == "never":
        return False, False
    if env.get("NO_COLOR"):
        return False, False
    if env.get("FORCE_COLOR"):
        return True, True
    if env.get("TERM") == "dumb":
        return False, False
    return isatty, False


def enable_windows_vt(stream: TextIO) -> bool:
    """Enable VT processing on a Windows console stream; True on success.

    Always True off Windows. Windows Terminal ships with VT enabled;
    this call is for classic conhost.
    """
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError):
        return False
    kernel32 = ctypes.windll.kernel32
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return True
    return bool(
        kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
    )


@dataclass(frozen=True)
class Styler:
    """Paints classified lines; per-stream on/off fixed at construction."""

    colors: Mapping[str, str]
    out_enabled: bool
    err_enabled: bool

    @classmethod
    def plain(cls) -> "Styler":
        """A never-coloring styler, for use before config parsing succeeds."""
        return cls(colors=THEMES["dark"], out_enabled=False, err_enabled=False)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        stdout_isatty: bool | None = None,
        stderr_isatty: bool | None = None,
        enable_vt: Callable[[TextIO], bool] | None = None,
    ) -> "Styler":
        mode = _resolve_mode(env)
        theme_name = env.get("COLOR_THEME") or "dark"
        if theme_name not in THEMES:
            raise SettingsError(
                f"COLOR_THEME must be dark or light, got: {theme_name}"
            )
        colors = dict(THEMES[theme_name])
        for category in colors:
            var = f"COLOR_{category.upper()}"
            raw = env.get(var) or ""
            if raw:
                colors[category] = _sgr_from_value(var, raw)
        if stdout_isatty is None:
            stdout_isatty = sys.stdout.isatty()
        if stderr_isatty is None:
            stderr_isatty = sys.stderr.isatty()
        if enable_vt is None:
            enable_vt = enable_windows_vt
        out_enabled, out_forced = _stream_enabled(mode, env, stdout_isatty)
        err_enabled, err_forced = _stream_enabled(mode, env, stderr_isatty)
        if out_enabled and not enable_vt(sys.stdout) and not out_forced:
            out_enabled = False
        if err_enabled and not enable_vt(sys.stderr) and not err_forced:
            err_enabled = False
        return cls(
            colors=colors, out_enabled=out_enabled, err_enabled=err_enabled
        )

    def paint(self, text: str) -> str:
        """Style each classified line; unclassified lines pass through."""
        styled = []
        for line in text.split("\n"):
            category = classify(line)
            sgr = self.colors.get(category) if category else None
            styled.append(f"\x1b[{sgr}m{line}\x1b[0m" if sgr else line)
        return "\n".join(styled)

    def out(self, text: str) -> None:
        print(self.paint(text) if self.out_enabled else text)

    def err(self, text: str) -> None:
        print(
            self.paint(text) if self.err_enabled else text, file=sys.stderr
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_style.py -q`
Expected: all PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (no existing test touches `style.py`; nothing else changed)

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/style.py tests/test_style.py
git commit -m "feat(style): add color theme engine with env config

Add a zero-dependency style module for terminal colors:

- classify() maps output lines to categories (error, stage,
  checkpoint, progress, warning, success) using the existing
  message-prefix conventions plus three fixed success phrases.
- THEMES holds dark (default) and light SGR tables; COLOR_<CATEGORY>
  env vars override single categories with a color name
  (red, bright-cyan, bold-bright-red) or raw SGR params (1;91).
- Styler.from_env resolves on/off per stream: explicit COLOR beats
  NO_COLOR beats FORCE_COLOR beats isatty auto-detection; TERM=dumb
  disables auto mode. Invalid values raise SettingsError.
- enable_windows_vt turns on VT processing for classic conhost;
  auto mode degrades to plain when it fails, forced modes emit anyway.
- Colors are applied at print time only, so messages, the run log,
  and archives stay plain."
```

---

### Task 2: Wire the styler into cli.py

**Files:**
- Modify: `src/adversarial_ai_coding/cli.py`
- Test: `tests/test_style_wiring.py`

**Interfaces:**
- Consumes (from Task 1): `Styler.from_env(env)`, `Styler.plain()`, `styler.out(text)`, `styler.err(text)` — all defined in `adversarial_ai_coding.style`.
- Produces: no new public API. `WorkflowContext` is constructed with `echo=styler.out, echo_err=styler.err`; `_print_resume_hint` and `_abort_message` gain a trailing `echo_err` parameter (module-private helpers, only called from cli.py).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_style_wiring.py`. The fake-agent wrapper helper is duplicated from tests/test_resume_integration.py on purpose (test modules do not import each other):

```python
"""cli.py styler wiring: colored terminal, plain run log and redirects.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

import os
import sys
from pathlib import Path

from adversarial_ai_coding import cli
from adversarial_ai_coding.style import Styler

FAKE = str(Path(__file__).parent / "fake_agent.py")


def _make_wrapper(work: Path, role: str) -> str:
    if os.name == "nt":
        path = work / f"fake-{role}.cmd"
        path.write_text(
            f'@"{sys.executable}" "{FAKE}" --role fake-{role} %*\r\n',
            encoding="utf-8",
        )
    else:
        path = work / f"fake-{role}"
        path.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" --role fake-{role} "$@"\n',
            encoding="utf-8",
        )
        path.chmod(0o755)
    return str(path)


def wf_env(work: Path, **overrides) -> dict:
    env = {
        "HUMAN_GATE": "0",
        "DUAL_SPEC": "0",
        "AUTO_BRANCH": "1",
        "USE_WORKTREE": "0",
        "OPEN_PR": "0",
        "GATE_CMD": "exit 0",
        "BUILD_GATE_CMD": "exit 0",
        "RETRY_ON_LIMIT": "0",
        "NOTIFY_CMD": "",
        "FAKE_CALLS_LOG": str(work / "calls.log"),
        "FAKE_ABORT_ON": str(work / "abort-on"),
        "AGENT_A": _make_wrapper(work, "worker"),
        "AGENT_B": _make_wrapper(work, "reviewer"),
    }
    env.update(overrides)
    return env


def run_cli(repo, env, monkeypatch, args=None):
    # The fake-agent wrappers run as subprocesses and read FAKE_* from the
    # real process environment, so every var goes through setenv too.
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PYTHONPATH", "")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    argv = ["demo task"] if args is None else args
    return cli.main(argv, env, stdin_isatty=False)


def run_log_text(repo: Path) -> str:
    logs = list((repo / ".workflow" / "runs").rglob("*-run.log"))
    assert logs, "expected an archived run log"
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def test_invalid_color_value_fails_fast(new_repo, tmp_path, monkeypatch, capsys):
    # Runs inside the throwaway repo with fake agents so the pre-wiring
    # red run is safe: it completes a fake workflow instead of failing
    # fast, and the rc assertion is what fails.
    env = wf_env(tmp_path, COLOR="sometimes")
    rc = run_cli(new_repo, env, monkeypatch)
    assert rc == 1
    assert "COLOR must be auto, always, or never" in capsys.readouterr().err


def test_full_run_color_always_paints_terminal_but_not_run_log(
    new_repo, tmp_path, monkeypatch, capsys
):
    env = wf_env(tmp_path, COLOR="always")
    assert run_cli(new_repo, env, monkeypatch) == 0
    out = capsys.readouterr().out
    # Stage banner and progress lines carry dark-theme SGR codes.
    assert "\x1b[1;96m================" in out
    assert "\x1b[36m>>> " in out
    # The archived run log stays plain.
    assert "\x1b[" not in run_log_text(new_repo)


def test_full_run_auto_mode_emits_no_codes_when_not_a_tty(
    new_repo, tmp_path, monkeypatch, capsys
):
    # pytest capture streams are not ttys, so auto behaves like a redirect.
    env = wf_env(tmp_path)
    assert run_cli(new_repo, env, monkeypatch) == 0
    captured = capsys.readouterr()
    assert "\x1b[" not in captured.out
    assert "\x1b[" not in captured.err
    assert "\x1b[" not in run_log_text(new_repo)


def test_ctx_log_file_stays_plain_when_echo_is_styled(make_ctx, capsys):
    styler = Styler.from_env(
        {"COLOR": "always"},
        stdout_isatty=False,
        stderr_isatty=False,
        enable_vt=lambda _stream: True,
    )
    ctx = make_ctx()
    ctx.echo = styler.out
    ctx.log("!! Protected acceptance test files were modified:")
    assert "\x1b[1;91m" in capsys.readouterr().out
    log_text = ctx.archive.log_path.read_text(encoding="utf-8")
    assert "\x1b[" not in log_text
    assert "!! Protected acceptance test files were modified:" in log_text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_style_wiring.py -q`
Expected: `test_invalid_color_value_fails_fast` FAILS (the fake workflow completes with rc 0 instead of failing fast) and `test_full_run_color_always_paints_terminal_but_not_run_log` FAILS (no SGR codes in output). The other two PASS already — they are regression guards pinning the absence of codes (`auto` on non-tty; plain log file via the Task 1 seam) and must stay green after wiring.

- [ ] **Step 3: Modify cli.py**

3a. Add the import (after the `.runstate` import block, before `.workflow`):

```python
from .style import Styler
```

3b. Extend the `typing` import:

```python
from typing import Callable, Mapping
```

3c. Replace `_print_resume_hint` with an echo-parameter version:

```python
def _print_resume_hint(
    run_id: str,
    use_worktree: bool,
    workspace: Path,
    printed: set,
    echo_err: Callable[[str], None],
) -> None:
    if printed:
        return
    printed.add(True)
    if use_worktree:
        echo_err(
            f"To resume this run:\n  cd {workspace} && "
            f"RESUME_RUN={run_id} adversarial-ai-coding"
        )
    else:
        echo_err(
            f"To resume this run:\n  RESUME_RUN={run_id} adversarial-ai-coding"
        )
```

3d. Replace `_abort_message` likewise:

```python
def _abort_message(
    rc: int,
    state,
    run_id,
    use_worktree,
    workspace,
    hint_printed,
    echo_err,
) -> None:
    if rc != 0 and state is not None and not state.is_completed():
        echo_err(f"!! Workflow interrupted (exit={rc}).")
        _print_resume_hint(
            run_id, use_worktree, workspace, hint_printed, echo_err
        )
```

3e. In `main()`, just before the `try:` line, add the fallback styler; then make building the real styler the FIRST statement inside `try:` (before the task-file block, so `Reading task description from file:` can route through it and a bad color value aborts before any side effect):

```python
    styler = Styler.plain()
    try:
        styler = Styler.from_env(env)
```

3f. Convert `main()`'s frame prints to the styler. Exact mapping (message text unchanged everywhere):

| Current call | New call |
| --- | --- |
| `print(f"Reading task description from file:{task_arg}")` | `styler.out(f"Reading task description from file:{task_arg}")` |
| `print(f"Resuming run {run_id} (state: {state.state_dir})", file=sys.stderr)` | `styler.err(f"Resuming run {run_id} (state: {state.state_dir})")` |
| `print("Run this script from the root of the target git repository.", file=sys.stderr)` | `styler.err("Run this script from the root of the target git repository.")` |
| `print(f"Workflow settings:A={...}")` (whole f-string) | `styler.out(...)` same argument |
| `print(f"Importing spec:{...}" + ...)` (whole expression) | `styler.out(...)` same argument |
| `resume_workspace(..., lambda message: print(message, file=sys.stderr))` | `resume_workspace(..., styler.err)` |
| `print(f"Created worktree:{workspace} (branch auto/{run_id}; " "remove later with git worktree remove)")` | `styler.out(...)` same argument |
| `bootstrap_agents_md(workspace, default_agents_template(env), print, lambda message: print(message, file=sys.stderr))` | `bootstrap_agents_md(workspace, default_agents_template(env), styler.out, styler.err)` |
| `print(f"Quality gate:{gate_cmd}")` | `styler.out(f"Quality gate:{gate_cmd}")` |
| `print("(warning: no quality gate command detected; ...)", file=sys.stderr)` | `styler.err("(warning: no quality gate command detected; deterministic " "gates are disabled. Set GATE_CMD to enable one.)")` |
| `print(exc, file=sys.stderr)` in `except WorkflowAbort` | `styler.err(str(exc))` |
| `print(exc, file=sys.stderr)` in `except (SettingsError, ...)` | `styler.err(str(exc))` |

Keep as plain `print` (content-carrying or pre-styler, per spec): `USAGE`, the `print-agents` block, `print(f"Task:{task}")`.

3g. Pass the echo into the three `_abort_message` call sites, e.g.:

```python
    except KeyboardInterrupt:
        _abort_message(
            130, state, run_id, use_worktree, workspace, hint_printed,
            styler.err,
        )
        return 130
```

(same trailing `styler.err` argument for the `WorkflowAbort` and `SettingsError`/`RunStateError`/`PromptTemplateError` handlers). Add `echo=styler.out, echo_err=styler.err` to the `WorkflowContext(...)` constructor call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_style_wiring.py -q`
Expected: all 4 PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS. Existing tests are unaffected because pytest capture streams are not ttys, so auto mode leaves every byte unchanged. If a cli test fails, check it does not call `_print_resume_hint` / `_abort_message` positionally with the old arity.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/cli.py tests/test_style_wiring.py
git commit -m "feat(cli): colorize workflow terminal output

Wire the style module into the CLI so the workflow's own status
messages are colored on interactive terminals:

- Build Styler.from_env at the top of main()'s try block; a bad
  COLOR/COLOR_THEME/COLOR_<CATEGORY> value fails fast through the
  existing SettingsError handler. A Styler.plain() fallback covers
  the handlers themselves.
- Pass echo=styler.out / echo_err=styler.err to WorkflowContext, so
  every ctx.echo/echo_err/log call site gets colors with no call-site
  changes, while ctx.log_file keeps writing plain text.
- Route cli.py frame prints (resume notes, settings line, quality
  gate, warnings, abort messages) through the styler; _abort_message
  and _print_resume_hint take an echo parameter. USAGE, print-agents
  output, and the Task: line stay plain because they carry content.
- Integration tests: a full fake-agent run with COLOR=always paints
  the terminal but leaves the archived run log ANSI-free; auto mode
  emits no codes on non-tty streams; invalid COLOR exits 1."
```

---

### Task 3: Document the color settings (bilingual)

**Files:**
- Modify: `README.md` (the `## Configuration` table)
- Modify: `README.zh-TW.md` (the `## 環境變數` table)
- Test: `tests/test_documentation.py`

**Interfaces:**
- Consumes: nothing from other tasks (docs only).
- Produces: nothing consumed later.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_documentation.py`, following the file's existing bilingual pattern:

```python
def test_color_settings_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "`COLOR`" in readme
        assert "COLOR_THEME" in readme
        assert "NO_COLOR" in readme
        assert "COLOR_ERROR" in readme
        assert "bold-bright-red" in readme
        assert "1;91" in readme
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_documentation.py::test_color_settings_are_documented_bilingually -q`
Expected: FAIL (assertions on README.md)

- [ ] **Step 3: Add the table rows**

In `README.md`, insert after the `NOTIFY_CMD` row of the `## Configuration` table:

```markdown
| `COLOR` | `auto` | Colorize the workflow's own status messages. `auto` colors only when the stream is a terminal (honors `NO_COLOR` and `FORCE_COLOR`; `TERM=dumb` disables), `always` forces color, `never` disables it. Redirected output and the archived run log never contain color codes. |
| `COLOR_THEME` | `dark` | Status message color theme: `dark` or `light`. |
| `COLOR_<CATEGORY>` | theme default | Per-category color override for `STAGE`, `PROGRESS`, `ERROR`, `WARNING`, `CHECKPOINT`, `SUCCESS`. Accepts a color name (`red`, `bright-cyan`, `bold-bright-red`) or raw SGR parameters (`1;91`), e.g. `COLOR_ERROR=bold-bright-red`. |
```

In `README.zh-TW.md`, insert after the `NOTIFY_CMD` row of the `## 環境變數` table:

```markdown
| `COLOR` | `auto` | 為 workflow 自身的狀態訊息上色。`auto` 只在輸出是終端機時上色(遵守 `NO_COLOR` 與 `FORCE_COLOR`;`TERM=dumb` 停用),`always` 強制上色,`never` 停用。重導向的輸出與封存的 run log 永遠不含色碼。 |
| `COLOR_THEME` | `dark` | 狀態訊息主題:`dark` 或 `light`。 |
| `COLOR_<CATEGORY>` | 主題預設 | 逐類別覆寫顏色,類別為 `STAGE`、`PROGRESS`、`ERROR`、`WARNING`、`CHECKPOINT`、`SUCCESS`。接受顏色名(`red`、`bright-cyan`、`bold-bright-red`)或原始 SGR 參數(`1;91`),例如 `COLOR_ERROR=bold-bright-red`。 |
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_documentation.py -q`
Expected: all PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add README.md README.zh-TW.md tests/test_documentation.py
git commit -m "docs(readme): document color configuration

Add COLOR, COLOR_THEME, and COLOR_<CATEGORY> rows to the
configuration tables in both READMEs, covering the auto/always/never
switch, NO_COLOR/FORCE_COLOR handling, the dark/light themes, and
per-category overrides with color names or raw SGR parameters. A
documentation test pins the bilingual coverage."
```

---

## Verification

After all tasks:

- `uv run pytest -q` — full suite green.
- Manual smoke check in an interactive terminal (colors visible):
  `uv run adversarial-ai-coding` with no args shows plain usage; a run in a scratch repo shows colored banners.
- Redirect check: `uv run adversarial-ai-coding 2>&1 | more` (or `> out.txt`) shows no `\x1b[` sequences.
