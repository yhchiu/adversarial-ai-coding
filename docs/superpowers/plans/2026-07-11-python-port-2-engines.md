# Python Port — Plan 2 of 6: Engines and Retry Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the engine adapters (claude/codex/agy/generic, worker and reviewer variants), the model/args resolution helpers, the notify hook, and the `engine_call` rate-limit retry loop.

**Architecture:** Plan 2 of the series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. Builds on
plan 1's `config.Settings` and `ratelimit` parsers. Engine adapters are
plain functions dispatched by engine name; subprocess execution is
concentrated in two module-level runners that tests monkeypatch. The retry
loop lives in `ratelimit.py` and is fully injectable (no real sleeping in
tests). Mutable per-run engine state (worker session, last cost) lives in
one small `EngineSession` dataclass owned by the caller — no module
globals.

**Tech Stack:** Python 3.12+, stdlib only (`subprocess`, `shutil`, `json`, `shlex` not needed — bash splits on whitespace), pytest.

## Global Constraints

- Runtime dependencies: none (stdlib only); pytest is dev-group only.
- The bash files are FROZEN: never edit `adversarial-ai-coding.sh`,
  `tests/helpers.test.sh`, `tests/resume.test.sh`, `tests/e2e/run.sh`.
- Behavior parity: each ported function must produce the same observable
  behavior as the cited bash lines. Bash's silent-failure semantics matter:
  a helper that bash lets fail quietly must not raise in Python (plan 1
  learned this twice — empty-env fallback, `date -d` fall-through).
- Extra CLI args (`CLAUDE_ARGS` etc.) split on whitespace exactly like
  bash's unquoted expansion: use `str.split()`.
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By.
- Run tests with `uv run pytest -q` from the repo root; the full suite
  (55 tests at plan start) must stay green after every task.
- Machine note: if `uv run` fails oddly, clear `PYTHONHOME`/`PYTHONPATH`
  first (a system-wide Python 2.7 leaks in on this machine).

## File Structure

```
src/adversarial_ai_coding/engines.py    # Task 1 (helpers) + Task 2 (adapters, notify)
src/adversarial_ai_coding/ratelimit.py  # Task 3 (engine_call retry loop), Task 4 (hour guard)
tests/test_engines.py                   # Tasks 1-2
tests/test_engine_call.py               # Task 3
tests/test_ratelimit_parsing.py         # Task 4 (extended)
.github/workflows/ci.yml                # Task 4 (setup-uv cache, one line)
```

## Bash-Function Mapping (this plan's parity ledger)

| bash (adversarial-ai-coding.sh) | Python |
|---|---|
| `is_builtin_engine` :400 | `engines.is_builtin_engine` |
| `engine_model` :689 | `engines.engine_model` |
| `resolve_model_args` :407 | `engines.resolve_model_args` |
| `generic_engine_args` :1090 | `engines.generic_engine_args` |
| `need` :341, `validate_engines` :343 | `engines.validate_engines` |
| `notify` :361 | `engines.notify` |
| `w_claude` :1040 / `r_claude` :1243 | `engines.run_worker/run_reviewer` (claude branch) |
| `w_codex` :1062 / `r_codex` :1265 | `engines.run_worker/run_reviewer` (codex branch) |
| `w_agy` :1077 / `r_agy` :1274 | `engines.run_worker/run_reviewer` (agy branch) |
| `w_generic` :1105 / `r_generic` :1283 / `run_generic_engine` :1098 | `engines.run_worker/run_reviewer` (generic branch) |
| `worker_fn_for_engine` :1109 / `reviewer_fn_for_engine` :1287 | dispatch inside `run_worker`/`run_reviewer` |
| `VERDICT_SCHEMA` :1224 | `engines.VERDICT_SCHEMA` |
| `WORKER_SESSION`/`CURRENT_ENGINE`/`LAST_COST` :1036-1038 | `engines.EngineSession` (no globals) |
| `engine_call` :1131 | `ratelimit.engine_call` |
| `QUOTA_ABORT_RC` :72 | `ratelimit.QUOTA_ABORT_RC` |
| `archive_engine_attempt` :1118 | plan 3 (`archive.py`); here an injected callback |

Deliberate divergences introduced by this plan (documented in code):
- Windows: argv[0] is resolved via `shutil.which` so `.cmd`/`.exe` shims
  work without a shell (bash resolved via PATH natively).
- claude success output that is not valid JSON leaves the session
  unchanged and returns the raw text instead of dying mid-parse (bash's
  errexit is suspended inside `engine_call`'s condition context, so jq
  failures degraded similarly rather than aborting).

---

### Task 1: Engine selection and model helpers

Bash reference: `adversarial-ai-coding.sh:341-359` (need, validate_engines),
`:400-422` (is_builtin_engine, resolve_model_args), `:689-696`
(engine_model), `:1090-1096` (generic_engine_args).
Bash tests ported: `tests/helpers.test.sh:63-95`.

**Files:**
- Create: `src/adversarial_ai_coding/engines.py`
- Test: `tests/test_engines.py`

**Interfaces:**
- Consumes: `config.Settings`, `config.SettingsError` (plan 1).
- Produces (exact signatures later tasks and plans rely on):
  - `engines.is_builtin_engine(name: str) -> bool` — true for claude/codex/agy.
  - `engines.engine_model(name: str, settings: Settings) -> str` — the
    slot's model override for a BUILT-IN engine, `""` otherwise (custom
    engines ignore MODEL_A/MODEL_B; bash :689-696).
  - `engines.resolve_model_args(name: str, settings: Settings) -> str` —
    CLAUDE_ARGS/CODEX_ARGS/AGY_ARGS for built-ins; the slot's
    ENGINE_*_ARGS for a custom engine matching slot A or B; `""` otherwise.
  - `engines.generic_engine_args(name: str, settings: Settings) -> str` —
    the slot args for a custom engine command (bash :1090).
  - `engines.validate_engines(settings: Settings, which: Callable[[str], str | None] = shutil.which) -> None`
    — raises `SettingsError` with the bash messages: missing command
    ("Missing required command:<name>"), or same non-claude engine on both
    slots (":343-359" wording, see code).

- [ ] **Step 1: Write the failing tests**

`tests/test_engines.py`:

```python
"""Ports tests/helpers.test.sh:63-95 (engine_model, aliases, builtin checks)."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.engines import (
    engine_model,
    generic_engine_args,
    is_builtin_engine,
    resolve_model_args,
    validate_engines,
)


def make(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def test_is_builtin_engine():
    assert is_builtin_engine("claude")
    assert is_builtin_engine("codex")
    assert is_builtin_engine("agy")
    assert not is_builtin_engine("custom-agent")


def test_engine_model_slot_a_uses_model_a():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex", "MODEL_A": "haiku", "MODEL_B": "mini"})
    assert engine_model("claude", s) == "haiku"
    assert engine_model("codex", s) == "mini"


def test_engine_model_unset_is_empty_for_cli_default():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    assert engine_model("claude", s) == ""


def test_engine_model_custom_engine_ignores_model_a():
    s = make({"ENGINE_A": "custom-agent", "ENGINE_B": "codex", "MODEL_A": "ignored",
              "ENGINE_A_ARGS": "--model custom"})
    assert engine_model("custom-agent", s) == ""


def test_resolve_model_args_builtin_uses_cli_args():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex",
              "CLAUDE_ARGS": "--fast", "CODEX_ARGS": "-c model_reasoning_effort=low"})
    assert resolve_model_args("claude", s) == "--fast"
    assert resolve_model_args("codex", s) == "-c model_reasoning_effort=low"


def test_resolve_model_args_custom_engine_uses_slot_args():
    s = make({"ENGINE_A": "custom-agent", "ENGINE_B": "codex",
              "ENGINE_A_ARGS": "--model custom --flag"})
    assert resolve_model_args("custom-agent", s) == "--model custom --flag"
    assert generic_engine_args("custom-agent", s) == "--model custom --flag"


def test_resolve_model_args_unknown_engine_is_empty():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    assert resolve_model_args("stranger", s) == ""
    assert generic_engine_args("stranger", s) == ""


def test_validate_engines_missing_command():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    with pytest.raises(SettingsError, match="Missing required command:claude"):
        validate_engines(s, which=lambda name: None)


def test_validate_engines_same_builtin_engine_rejected():
    s = make({"ENGINE_A": "codex", "ENGINE_B": "codex"})
    with pytest.raises(SettingsError, match="cannot both use codex"):
        validate_engines(s, which=lambda name: "C:/fake/" + name)


def test_validate_engines_same_custom_engine_rejected():
    s = make({"ENGINE_A": "wrapper", "ENGINE_B": "wrapper"})
    with pytest.raises(SettingsError, match="custom engine command wrapper"):
        validate_engines(s, which=lambda name: "C:/fake/" + name)


def test_validate_engines_both_claude_is_allowed():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "claude"})
    validate_engines(s, which=lambda name: "C:/fake/" + name)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engines.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'adversarial_ai_coding.engines'`

- [ ] **Step 3: Write `src/adversarial_ai_coding/engines.py` (helpers part)**

```python
"""Engine adapters and selection helpers.

Port of adversarial-ai-coding.sh:341-359 (validate_engines), 400-422
(is_builtin_engine, resolve_model_args), 689-696 (engine_model),
1090-1096 (generic_engine_args). Task 2 adds the subprocess adapters.
"""

from __future__ import annotations

import shutil
from typing import Callable

from .config import Settings, SettingsError

BUILTIN_ENGINES = ("claude", "codex", "agy")


def is_builtin_engine(name: str) -> bool:
    return name in BUILTIN_ENGINES


def engine_model(name: str, settings: Settings) -> str:
    # Custom engines ignore MODEL_A/MODEL_B; they get args via ENGINE_*_ARGS.
    if not is_builtin_engine(name):
        return ""
    if name == settings.engine_a and settings.model_a:
        return settings.model_a
    if name == settings.engine_b and settings.model_b:
        return settings.model_b
    return ""


def resolve_model_args(name: str, settings: Settings) -> str:
    if name == "claude":
        return settings.claude_args
    if name == "codex":
        return settings.codex_args
    if name == "agy":
        return settings.agy_args
    return generic_engine_args(name, settings)


def generic_engine_args(name: str, settings: Settings) -> str:
    if name == settings.engine_a:
        return settings.engine_a_args
    if name == settings.engine_b:
        return settings.engine_b_args
    return ""


def validate_engines(
    settings: Settings, which: Callable[[str], str | None] = shutil.which
) -> None:
    for name in (settings.engine_a, settings.engine_b):
        if which(name) is None:
            raise SettingsError(f"Missing required command:{name}")
    # codex and agy resume the most recent session. Custom engines may have
    # the same limitation, so v1 requires distinct command names (bash :349-358).
    if settings.engine_a == settings.engine_b and settings.engine_a != "claude":
        if is_builtin_engine(settings.engine_a):
            raise SettingsError(
                f"A and B cannot both use {settings.engine_a} because session "
                "resume would interfere. Use different engines."
            )
        raise SettingsError(
            f"A and B cannot both use custom engine command {settings.engine_a}. "
            "Use separate wrapper command names for worker and reviewer."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engines.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/engines.py tests/test_engines.py
git commit -m "feat: port engine selection and model helpers

Port is_builtin_engine, engine_model, resolve_model_args,
generic_engine_args, and validate_engines. Custom engines ignore the
MODEL_A/MODEL_B overrides and read their slot's ENGINE_*_ARGS instead,
and two slots may share an engine command only when it is claude,
because codex/agy (and possibly custom CLIs) resume the most recent
session. validate_engines takes an injectable which() so tests do not
depend on installed CLIs."
```

---

### Task 2: Subprocess adapters (worker/reviewer) and notify

Bash reference: `adversarial-ai-coding.sh:361-364` (notify), `:1036-1115`
(worker engines, generic runner), `:1222-1293` (reviewer engines,
VERDICT_SCHEMA).
Bash tests ported: `tests/helpers.test.sh:97-123` (w_generic argv capture).

**Files:**
- Modify: `src/adversarial_ai_coding/engines.py` (append)
- Test: `tests/test_engines.py` (append)

**Interfaces:**
- Consumes: Task 1 helpers.
- Produces:
  - `engines.VERDICT_SCHEMA: str` — the exact JSON schema string from bash :1224.
  - `@dataclass engines.EngineSession: worker_session: str = ""; last_cost: str = ""`
    — mutable; the workflow (plan 5) resets `worker_session` at stage
    boundaries, exactly like bash resets `WORKER_SESSION` in `begin_stage`.
  - `@dataclass engines.EngineIO: engine_out: Path; verdict_path: Path; echo: Callable[[str], None]`
    — `engine_out` is bash's `$ENGINE_OUT`; `verdict_path` is `$WF/verdict.json`
    (only the claude reviewer writes it); `echo` receives streamed output
    line-by-line (the caller tees to console/log/artifact in plans 3/5).
  - `@dataclass engines.EngineResult: rc: int; text: str` — `text` is what
    bash printed to stdout: the `.result` field for claude, the full
    merged output for the others.
  - `engines.run_worker(name: str, prompt: str, settings: Settings, session: EngineSession, io: EngineIO) -> EngineResult`
  - `engines.run_reviewer(name: str, prompt: str, settings: Settings, session: EngineSession, io: EngineIO) -> EngineResult`
  - `engines.notify(settings: Settings, message: str) -> None` — no-op when
    `NOTIFY_CMD` is empty; on command failure prints the bash warning to
    stderr and keeps going.
  - Test seam: module-level `_run_captured(argv) -> tuple[int, str]` and
    `_run_streaming(argv, io) -> tuple[int, str]`; adapters call them, and
    unit tests monkeypatch them. `_run_streaming` merges stderr into stdout
    (bash `2>&1 | tee`); `_run_captured` captures stdout only and lets
    stderr pass through (bash `out=$(claude ...)`).

- [ ] **Step 1: Write the failing tests (append to `tests/test_engines.py`)**

```python
import json
import sys
from pathlib import Path

from adversarial_ai_coding import engines
from adversarial_ai_coding.engines import (
    VERDICT_SCHEMA,
    EngineIO,
    EngineResult,
    EngineSession,
    notify,
    run_reviewer,
    run_worker,
)


def make_io(tmp_path, lines=None):
    sink = [] if lines is None else lines
    return EngineIO(
        engine_out=tmp_path / "engine-out.txt",
        verdict_path=tmp_path / "verdict.json",
        echo=sink.append,
    ), sink


def test_verdict_schema_matches_bash():
    schema = json.loads(VERDICT_SCHEMA)
    assert schema["required"] == ["approved", "blockers", "suggestions"]
    assert schema["properties"]["approved"]["type"] == "boolean"


def test_generic_worker_passes_args_and_prompt_as_final_arg(tmp_path):
    # helpers.test.sh: "generic:w_generic passes args and prompt as final arg"
    capture = tmp_path / "generic-capture.txt"
    fake = tmp_path / "fake_agent.py"
    fake.write_text(
        "import sys, pathlib\n"
        f"cap = pathlib.Path(r'{capture}')\n"
        "lines = [f'argc={len(sys.argv) - 1}']\n"
        "lines += [f'arg{i}={a}' for i, a in enumerate(sys.argv[1:], 1)]\n"
        "cap.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
        "print('custom engine ran')\n",
        encoding="utf-8",
    )
    s = Settings.from_env(
        {"AGENT_A": sys.executable, "AGENT_A_ARGS": f"{fake} --flag value", "AGENT_B": "codex"},
        run_id="r",
    )
    io, sink = make_io(tmp_path)
    session = EngineSession()
    result = run_worker(sys.executable, "hello prompt", s, session, io)
    assert result.rc == 0
    captured = capture.read_text(encoding="utf-8")
    # argv after the interpreter: [fake.py, --flag, value, prompt] — the slot
    # args are whitespace-split and the prompt is always the final argument.
    assert "argc=4" in captured
    assert f"arg1={fake}" in captured
    assert "arg2=--flag" in captured
    assert "arg3=value" in captured
    assert "arg4=hello prompt" in captured
    assert io.engine_out.read_text(encoding="utf-8").strip() == "custom engine ran"
    assert sink and sink[-1].strip() == "custom engine ran"


def test_claude_worker_parses_json_and_tracks_session(monkeypatch, tmp_path):
    payload = json.dumps(
        {"session_id": "sess-1", "total_cost_usd": 0.42, "result": "did the work"}
    )
    monkeypatch.setattr(engines, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({"TOOLS": "Bash(git *)"}, run_id="r")
    io, _ = make_io(tmp_path)
    session = EngineSession()
    result = run_worker("claude", "prompt text", s, session, io)
    assert result.rc == 0
    assert result.text == "did the work"
    assert session.worker_session == "sess-1"
    assert session.last_cost == "0.42"
    assert json.loads(io.engine_out.read_text(encoding="utf-8")) == json.loads(payload)


def test_claude_worker_resumes_session_and_builds_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv):
        seen["argv"] = argv
        return (0, json.dumps({"session_id": "s2", "result": "ok"}))

    monkeypatch.setattr(engines, "_run_captured", fake_run)
    monkeypatch.setattr(engines.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {"MODEL_A": "haiku", "CLAUDE_ARGS": "--fast", "TOOLS": "Bash(git *)"}, run_id="r"
    )
    io, _ = make_io(tmp_path)
    session = EngineSession(worker_session="prev-session")
    run_worker("claude", "the prompt", s, session, io)
    argv = seen["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == "the prompt"
    assert "--output-format" in argv and "json" in argv
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Bash(git *)"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert "--fast" in argv
    assert argv[argv.index("--resume") + 1] == "prev-session"


def test_claude_worker_failure_writes_engine_out_and_keeps_rc(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(engines, "_run_captured", lambda argv: (2, "quota text"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_worker("claude", "p", s, EngineSession(), io)
    assert result.rc == 2
    assert io.engine_out.read_text(encoding="utf-8").strip() == "quota text"
    err = capsys.readouterr().err
    assert "quota text" in err
    assert "claude exited with code 2" in err


def test_claude_worker_invalid_json_success_keeps_session(monkeypatch, tmp_path):
    # Deliberate lenient divergence: bash's jq failures inside engine_call's
    # condition context degraded to empty values rather than aborting.
    monkeypatch.setattr(engines, "_run_captured", lambda argv: (0, "not json at all"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = EngineSession(worker_session="keep-me")
    result = run_worker("claude", "p", s, session, io)
    assert result.rc == 0
    assert result.text == "not json at all"
    assert session.worker_session == "keep-me"


def test_codex_worker_fresh_then_resume_argv(monkeypatch, tmp_path):
    calls = []

    def fake_stream(argv, io):
        calls.append(argv)
        return (0, "codex output")

    monkeypatch.setattr(engines, "_run_streaming", fake_stream)
    monkeypatch.setattr(engines.shutil, "which", lambda name: name)
    s = Settings.from_env({"MODEL_B": "gpt-5.5", "AGENT_B": "codex",
                           "CODEX_ARGS": "-c model_reasoning_effort=low"}, run_id="r")
    io, _ = make_io(tmp_path)
    session = EngineSession()
    run_worker("codex", "p1", s, session, io)
    assert session.worker_session == "last"
    run_worker("codex", "p2", s, session, io)
    fresh, resumed = calls
    assert fresh[:4] == ["codex", "exec", "--sandbox", "workspace-write"]
    assert '-c' in fresh and 'model="gpt-5.5"' in fresh
    assert "model_reasoning_effort=low" in fresh
    assert fresh[-1] == "p1"
    assert resumed[:4] == ["codex", "exec", "resume", "--last"]
    assert 'sandbox_mode="workspace-write"' in resumed
    assert resumed[-1] == "p2"


def test_agy_worker_continue_flag(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engines, "_run_streaming", lambda argv, io: (calls.append(argv), (0, "x"))[1])
    monkeypatch.setattr(engines.shutil, "which", lambda name: name)
    s = Settings.from_env({"AGENT_A": "agy", "AGENT_B": "codex"}, run_id="r")
    io, _ = make_io(tmp_path)
    session = EngineSession()
    run_worker("agy", "p1", s, session, io)
    assert session.worker_session == "continue"
    run_worker("agy", "p2", s, session, io)
    assert "--continue" not in calls[0]
    assert "--continue" in calls[1]
    assert calls[0][:3] == ["agy", "--print", "p1"]
    assert "--dangerously-skip-permissions" in calls[0]


def test_claude_reviewer_writes_verdict_from_structured_output(monkeypatch, tmp_path):
    payload = json.dumps({
        "structured_output": {"approved": True, "blockers": [], "suggestions": ["s1"]},
        "total_cost_usd": 0.1,
        "result": "review text",
    })
    monkeypatch.setattr(engines, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, EngineSession(), io)
    assert result.text == "review text"
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is True and verdict["suggestions"] == ["s1"]


def test_claude_reviewer_missing_structured_output_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "_run_captured",
                        lambda argv: (0, json.dumps({"result": "no verdict"})))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, EngineSession(), io)
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is False
    assert verdict["blockers"] == ["reviewer did not produce a structured verdict"]


def test_claude_reviewer_argv_has_schema(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(engines, "_run_captured",
                        lambda argv: (seen.update(argv=argv), (0, "{}"))[1])
    monkeypatch.setattr(engines.shutil, "which", lambda name: name)
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, EngineSession(), io)
    argv = seen["argv"]
    assert argv[argv.index("--json-schema") + 1] == VERDICT_SCHEMA


def test_agy_reviewer_uses_30m_timeout_and_no_continue(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(engines, "_run_streaming",
                        lambda argv, io: (seen.update(argv=argv), (0, "x"))[1])
    monkeypatch.setattr(engines.shutil, "which", lambda name: name)
    s = Settings.from_env({"AGENT_A": "agy", "AGENT_B": "codex"}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("agy", "p", s, EngineSession(worker_session="continue"), io)
    argv = seen["argv"]
    assert "--print-timeout" in argv and "30m" in argv
    assert "--continue" not in argv  # reviewers always start fresh


def test_notify_noop_when_unset_and_warns_on_failure(tmp_path, capsys):
    s = Settings.from_env({}, run_id="r")
    notify(s, "hello")  # no NOTIFY_CMD: silent no-op
    s2 = Settings.from_env({"NOTIFY_CMD": "definitely-not-a-command-xyz"}, run_id="r")
    notify(s2, "hello")
    assert "notification command failed" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engines.py -q`
Expected: FAIL — ImportError on the new names.

- [ ] **Step 3: Append the adapters to `src/adversarial_ai_coding/engines.py`**

```python
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Exact schema string from adversarial-ai-coding.sh:1224.
VERDICT_SCHEMA = (
    '{"type":"object","properties":{"approved":{"type":"boolean"},'
    '"blockers":{"type":"array","items":{"type":"string"}},'
    '"suggestions":{"type":"array","items":{"type":"string"}}},'
    '"required":["approved","blockers","suggestions"]}'
)

VERDICT_FALLBACK = {
    "approved": False,
    "blockers": ["reviewer did not produce a structured verdict"],
    "suggestions": [],
}


@dataclass
class EngineSession:
    """Bash WORKER_SESSION/LAST_COST (sh:1036-1038), owned by the workflow.

    The workflow resets worker_session at stage boundaries (begin_stage);
    reviewer calls never read it — each review round starts fresh.
    """

    worker_session: str = ""
    last_cost: str = ""


@dataclass
class EngineIO:
    engine_out: Path                      # bash $ENGINE_OUT
    verdict_path: Path                    # bash $WF/verdict.json
    echo: "Callable[[str], None]"         # streamed output sink, line by line


@dataclass
class EngineResult:
    rc: int
    text: str


def _resolve_argv0(name: str) -> str:
    # Windows: claude/codex/agy install as .cmd shims; Popen needs the
    # resolved path (bash resolved via PATH natively).
    return shutil.which(name) or name


def _run_captured(argv: list[str]) -> tuple[int, str]:
    # bash: out=$(cmd ...) — stdout captured, stderr passes through.
    proc = subprocess.run(argv, capture_output=False, stdout=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout.rstrip("\n")


def _run_streaming(argv: list[str], io: EngineIO) -> tuple[int, str]:
    # bash: cmd ... 2>&1 | tee "$ENGINE_OUT" — merged output streamed and saved.
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lines: list[str] = []
    assert proc.stdout is not None
    with io.engine_out.open("w", encoding="utf-8") as out_file:
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            out_file.write(line + "\n")
            io.echo(line)
    rc = proc.wait()
    return rc, "\n".join(lines)


def _write_engine_out(io: EngineIO, text: str) -> None:
    io.engine_out.write_text(text + "\n", encoding="utf-8")


def _claude_common_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("claude", settings)
    if model:
        args += ["--model", model]
    args += settings.claude_args.split()
    return args


def _worker_claude(prompt: str, settings: Settings, session: EngineSession,
                   io: EngineIO) -> EngineResult:
    argv = [_resolve_argv0("claude"), "-p", prompt,
            "--output-format", "json", "--permission-mode", "acceptEdits",
            "--allowedTools", settings.tools]
    argv += _claude_common_args(settings)
    if session.worker_session:
        argv += ["--resume", session.worker_session]
    rc, out = _run_captured(argv)
    _write_engine_out(io, out)   # engine_call reads it for rate-limit detection
    if rc != 0:
        import sys
        print(out, file=sys.stderr)
        print(f"(claude exited with code {rc}; raw output is shown above)", file=sys.stderr)
        return EngineResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        # Lenient divergence: bash's jq failure degraded to empty fields.
        return EngineResult(0, out)
    session.worker_session = str(payload.get("session_id") or session.worker_session)
    cost = payload.get("total_cost_usd")
    session.last_cost = "" if cost is None else str(cost)
    return EngineResult(0, str(payload.get("result") or ""))


def _reviewer_claude(prompt: str, settings: Settings, session: EngineSession,
                     io: EngineIO) -> EngineResult:
    argv = [_resolve_argv0("claude"), "-p", prompt]
    argv += _claude_common_args(settings)
    argv += ["--output-format", "json", "--permission-mode", "acceptEdits",
             "--allowedTools", settings.tools, "--json-schema", VERDICT_SCHEMA]
    rc, out = _run_captured(argv)
    _write_engine_out(io, out)
    if rc != 0:
        import sys
        print(out, file=sys.stderr)
        print(f"(claude exited with code {rc}; raw output is shown above)", file=sys.stderr)
        return EngineResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        payload = {}
    verdict = payload.get("structured_output") or VERDICT_FALLBACK
    io.verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    cost = payload.get("total_cost_usd")
    session.last_cost = "" if cost is None else str(cost)
    return EngineResult(0, str(payload.get("result") or ""))


def _codex_model_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("codex", settings)
    if model:
        args += ["-c", f'model="{model}"']
    args += settings.codex_args.split()
    return args


def _worker_codex(prompt: str, settings: Settings, session: EngineSession,
                  io: EngineIO) -> EngineResult:
    margs = _codex_model_args(settings)
    if not session.worker_session:
        argv = [_resolve_argv0("codex"), "exec", "--sandbox", "workspace-write",
                *margs, prompt]
        rc, out = _run_streaming(argv, io)
        session.worker_session = "last"
    else:
        # exec resume has no --sandbox flag, so override config with -c (sh:1072).
        argv = [_resolve_argv0("codex"), "exec", "resume", "--last",
                "-c", 'sandbox_mode="workspace-write"', *margs, prompt]
        rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _reviewer_codex(prompt: str, settings: Settings, session: EngineSession,
                    io: EngineIO) -> EngineResult:
    argv = [_resolve_argv0("codex"), "exec", "--sandbox", "workspace-write",
            *_codex_model_args(settings), prompt]
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _agy_model_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("agy", settings)
    if model:
        args += ["--model", model]
    args += settings.agy_args.split()
    return args


def _worker_agy(prompt: str, settings: Settings, session: EngineSession,
                io: EngineIO) -> EngineResult:
    # --dangerously-skip-permissions approves every tool action; prefer an
    # isolated branch, worktree, or container when using agy (sh:1078-1079).
    argv = [_resolve_argv0("agy"), "--print", prompt,
            "--print-timeout", "60m", "--dangerously-skip-permissions"]
    argv += _agy_model_args(settings)
    if session.worker_session:
        argv += ["--continue"]
    rc, out = _run_streaming(argv, io)
    session.worker_session = "continue"
    return EngineResult(rc, out)


def _reviewer_agy(prompt: str, settings: Settings, session: EngineSession,
                  io: EngineIO) -> EngineResult:
    argv = [_resolve_argv0("agy"), "--print", prompt,
            "--print-timeout", "30m", "--dangerously-skip-permissions"]
    argv += _agy_model_args(settings)
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _run_generic(name: str, prompt: str, settings: Settings, io: EngineIO) -> EngineResult:
    argv = [_resolve_argv0(name), *generic_engine_args(name, settings).split(), prompt]
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def run_worker(name: str, prompt: str, settings: Settings, session: EngineSession,
               io: EngineIO) -> EngineResult:
    if name == "claude":
        return _worker_claude(prompt, settings, session, io)
    if name == "codex":
        return _worker_codex(prompt, settings, session, io)
    if name == "agy":
        return _worker_agy(prompt, settings, session, io)
    return _run_generic(name, prompt, settings, io)


def run_reviewer(name: str, prompt: str, settings: Settings, session: EngineSession,
                 io: EngineIO) -> EngineResult:
    if name == "claude":
        return _reviewer_claude(prompt, settings, session, io)
    if name == "codex":
        return _reviewer_codex(prompt, settings, session, io)
    if name == "agy":
        return _reviewer_agy(prompt, settings, session, io)
    return _run_generic(name, prompt, settings, io)


def notify(settings: Settings, message: str) -> None:
    if not settings.notify_cmd:
        return
    import sys
    argv = settings.notify_cmd.split() + [message]
    try:
        rc = subprocess.run(argv).returncode
    except OSError:
        rc = 1
    if rc != 0:
        print(f"(notification command failed:{settings.notify_cmd})", file=sys.stderr)
```

Fix the `Callable` import at the top of the file if needed
(`from typing import Callable` already exists from Task 1) and move the
`import sys` statements to the module header during implementation —
the code above marks where stderr output happens.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engines.py -q`
Expected: all PASS.

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/engines.py tests/test_engines.py
git commit -m "feat: port worker and reviewer engine adapters

Port w_/r_ claude, codex, agy, and generic engines. Claude runs
captured with JSON output (session id, cost, structured verdict for
reviews); codex and agy stream merged output tee-style into ENGINE_OUT.
Session semantics match bash: codex fresh exec then exec resume --last,
agy --continue after the first call, reviewers always fresh. Subprocess
execution sits behind two module-level runners so tests can monkeypatch
them; argv[0] resolves via shutil.which for Windows .cmd shims. Also
port notify with its failure warning."
```

---

### Task 3: `engine_call` retry loop

Bash reference: `adversarial-ai-coding.sh:72` (QUOTA_ABORT_RC),
`:1117-1169` (archive_engine_attempt is an injected callback here;
engine_call is ported in full).
Bash tests ported: `tests/helpers.test.sh:651-694`.

**Files:**
- Modify: `src/adversarial_ai_coding/ratelimit.py` (append)
- Test: `tests/test_engine_call.py`

**Interfaces:**
- Consumes: `ratelimit.is_rate_limited`, `ratelimit.parse_reset_wait`,
  `ratelimit.human_duration` (plan 1), `config.Settings`,
  `engines.EngineResult`.
- Produces:
  - `ratelimit.QUOTA_ABORT_RC = 75` — EX_TEMPFAIL; plan 5's cli maps it to
    a resumable abort.
  - `@dataclass ratelimit.RetryEvents:`
    `archive_attempt: Callable[[int, int], None]` (attempt number, rc; bash
    archive_engine_attempt — plan 3 wires the real archiver),
    `log_retry: Callable[[str], None]`, `notify: Callable[[str], None]`,
    `sleep: Callable[[float], None]`.
  - `ratelimit.engine_call(attempt: Callable[[], "EngineResult"], *, engine_out: Path, settings: Settings, events: RetryEvents, now: Callable[[], int] | None = None) -> "EngineResult"`
    — runs `attempt()` until success or a non-retryable failure. Returns
    the last `EngineResult`, with `rc == QUOTA_ABORT_RC` for every
    quota give-up. `now` feeds `parse_reset_wait` for deterministic tests.

- [ ] **Step 1: Write the failing tests**

`tests/test_engine_call.py`:

```python
"""Ports tests/helpers.test.sh:651-694 (engine_call stub retry behavior)."""

from datetime import datetime
from pathlib import Path

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.engines import EngineResult
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC, RetryEvents, engine_call

RATE_LIMITED = 'api_error_status":429 hit your session limit'
NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())


class Stub:
    """fake_engine from the bash suite: writes stub text and fails, or succeeds."""

    def __init__(self, engine_out: Path, stub_text: str):
        self.engine_out = engine_out
        self.stub_text = stub_text
        self.calls = 0

    def __call__(self) -> EngineResult:
        self.calls += 1
        if not self.stub_text:
            return EngineResult(0, "ok")
        self.engine_out.write_text(self.stub_text + "\n", encoding="utf-8")
        return EngineResult(1, self.stub_text)


def run(tmp_path, stub_text, retry_on_limit="1", retry_max="2"):
    engine_out = tmp_path / "engine-out.txt"
    stub = Stub(engine_out, stub_text)
    slept, archived, notes = [], [], []
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: archived.append((attempt, rc)),
        log_retry=lambda msg: notes.append(msg),
        notify=lambda msg: notes.append(msg),
        sleep=slept.append,
    )
    settings = Settings.from_env(
        {"RETRY_ON_LIMIT": retry_on_limit, "RETRY_BASE_WAIT": "1", "RETRY_MAX": retry_max},
        run_id="r",
    )
    result = engine_call(stub, engine_out=engine_out, settings=settings,
                         events=events, now=lambda: NOW)
    return result, stub, slept, archived


def test_default_retries_to_limit_then_typed_quota_abort(tmp_path):
    result, stub, slept, _ = run(tmp_path, RATE_LIMITED)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 3  # 1 call + 2 retries
    assert len(slept) == 2


def test_retry_off_no_retry_typed_quota_abort(tmp_path):
    result, stub, slept, _ = run(tmp_path, RATE_LIMITED, retry_on_limit="0")
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 1
    assert slept == []


def test_ordinary_error_no_retry(tmp_path):
    result, stub, _, _ = run(tmp_path, "ordinary build failure")
    assert result.rc == 1
    assert stub.calls == 1


def test_success_passes_immediately(tmp_path):
    result, stub, _, _ = run(tmp_path, "")
    assert result.rc == 0
    assert stub.calls == 1


def test_reset_beyond_ceiling_aborts_without_sleeping(tmp_path):
    # bash: date -d "+10 days"; here a fixed absolute date 10 days past NOW.
    far = "You've hit your usage limit. try again at Jul 20, 2026 9:00 AM."
    result, stub, slept, _ = run(tmp_path, far)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 1
    assert slept == []


def test_reset_within_ceiling_waits_and_retries(tmp_path):
    near = "You've hit your usage limit. try again at Jul 10, 2026 10:00 AM."
    result, stub, slept, _ = run(tmp_path, near)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 3
    assert slept == [3600 + 30, 3600 + 30]


def test_exponential_backoff_when_unparseable(tmp_path):
    result, stub, slept, _ = run(tmp_path, "rate limit but no reset info",
                                 retry_max="3")
    assert stub.calls == 4
    assert slept == [1, 2, 4]  # RETRY_BASE_WAIT=1 doubling per retry


def test_every_attempt_is_archived_with_rc(tmp_path):
    # helpers.test.sh: "engine_call:saves raw output for every retry attempt"
    _, _, _, archived = run(tmp_path, RATE_LIMITED)
    assert archived == [(1, 1), (2, 1), (3, 1)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_engine_call.py -q`
Expected: FAIL — ImportError on QUOTA_ABORT_RC / RetryEvents / engine_call.

- [ ] **Step 3: Append the retry loop to `src/adversarial_ai_coding/ratelimit.py`**

```python
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .config import Settings
    from .engines import EngineResult

# EX_TEMPFAIL: an agent call gave up on quota/rate limit; the run is resumable (sh:72).
QUOTA_ABORT_RC = 75


@dataclass
class RetryEvents:
    archive_attempt: Callable[[int, int], None]
    log_retry: Callable[[str], None]
    notify: Callable[[str], None]
    sleep: Callable[[float], None]


def engine_call(
    attempt: "Callable[[], EngineResult]",
    *,
    engine_out: Path,
    settings: "Settings",
    events: RetryEvents,
    now: Callable[[], int] | None = None,
) -> "EngineResult":
    """Port of engine_call (sh:1131-1169): retry only on rate limits.

    Every quota give-up returns rc=QUOTA_ABORT_RC so callers abort the run
    as resumable instead of treating it like a quality failure.
    """
    from .engines import EngineResult  # local import to avoid a cycle

    n = 0
    attempt_no = 1
    while True:
        result = attempt()
        events.archive_attempt(attempt_no, result.rc)
        if result.rc == 0:
            return result
        if not is_rate_limited(engine_out):
            return result
        if not settings.retry_on_limit:
            return EngineResult(QUOTA_ABORT_RC, result.text)
        if n >= settings.retry_max:
            events.log_retry(
                f"!! Rate limit did not clear after {settings.retry_max} retries; giving up."
            )
            return EngineResult(QUOTA_ABORT_RC, result.text)
        wait = parse_reset_wait(engine_out, now() if now else None)
        if wait is not None and wait > settings.retry_max_reset_wait:
            # The message told us exactly when the quota returns and it is far
            # away. Backing off would burn hours of sleep and still fail (sh:1149-1155).
            events.log_retry(
                f"!! Quota resets in {human_duration(wait)}, beyond "
                f"RETRY_MAX_RESET_WAIT={settings.retry_max_reset_wait}s. "
                "Not waiting; rerun after the reset."
            )
            events.notify("adversarial-ai-coding: quota exhausted; run aborted")
            return EngineResult(QUOTA_ABORT_RC, result.text)
        n += 1
        if wait is None:
            wait = min(settings.retry_base_wait * (1 << (n - 1)), settings.retry_max_wait)
        events.log_retry(
            f"== Rate limit hit; waiting {wait // 60} minutes before retry "
            f"{n}/{settings.retry_max} =="
        )
        events.notify(f"adversarial-ai-coding: rate limit hit; retry attempt {n}")
        events.sleep(wait)
        attempt_no += 1
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_engine_call.py -q`
Expected: all PASS.

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/ratelimit.py tests/test_engine_call.py
git commit -m "feat: port engine_call rate-limit retry loop

Retry only on detected rate limits: ordinary failures return
immediately, quota give-ups return the typed QUOTA_ABORT_RC (75) so the
run aborts as resumable. Prefer the parsed reset time; abort without
sleeping when it exceeds RETRY_MAX_RESET_WAIT; otherwise exponential
backoff from RETRY_BASE_WAIT capped at RETRY_MAX_WAIT. Attempt
archiving, logging, notify, sleep, and the clock are injected so the
ported bash stub tests run instantly and deterministically."
```

---

### Task 4: Hour-range guard (plan-1 carry-over) and CI cache

Carried over from plan 1's final whole-branch review: GNU date rejects
12-hour clock values with hour 0 or above 12; the Python parser accepted
them. Also apply the reviewer's CI suggestion (uv cache).

**Files:**
- Modify: `src/adversarial_ai_coding/ratelimit.py` (hour guards)
- Modify: `tests/test_ratelimit_parsing.py` (two tests)
- Modify: `.github/workflows/ci.yml` (one line)

**Interfaces:** no new names; behavior-only tightening of `parse_reset_wait`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_ratelimit_parsing.py`)**

```python
def test_hour_zero_clock_falls_through(tmp_path):
    # GNU date rejects "0:30am"; bash fell through to no match.
    assert parse_reset_wait(out_file(tmp_path, "resets 0:30am\n"), NOW) is None


def test_hour_zero_absolute_date_returns_none(tmp_path):
    p = out_file(tmp_path, "try again at Jul 14th, 2026 0:23 PM.\n")
    assert parse_reset_wait(p, NOW) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ratelimit_parsing.py -q`
Expected: the two new tests FAIL (both currently parse), everything else passes.

- [ ] **Step 3: Add the guards**

In `parse_reset_wait`, at the start of the Format 1 clock branch and the
Format 3 absolute branch, reject an hour outside GNU date's 12-hour
grammar before constructing the datetime:

```python
        hour12 = int(m.group(1))
        if not 1 <= hour12 <= 12:
            m = None  # GNU date rejects hour 0 or >12 with an am/pm marker
```

Format 1's guard must FALL THROUGH to Formats 2/3 (same structure as the
existing ValueError handling from commit d59de17); Format 3's guard falls
through to the final `return None`. Adjust to the actual code shape at
implementation time — the binding requirement is the two new tests plus
all existing tests staying green (`resets 19:30pm` already covered).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 5: Add the uv cache to CI**

In `.github/workflows/ci.yml`, `test-python` job, extend the setup-uv step:

```yaml
      - name: Install uv
        uses: astral-sh/setup-uv@v6
        with:
          enable-cache: true
```

Run: `git diff .github/workflows/ci.yml`
Expected: only the two `with:`/`enable-cache:` lines added.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/ratelimit.py tests/test_ratelimit_parsing.py .github/workflows/ci.yml
git commit -m "fix: reject impossible 12-hour clock hours like GNU date

Guard both reset-time formats so hour 0 or above 12 with an am/pm
marker is treated as unparseable, matching GNU date, with fall-through
preserved. Also enable the uv cache in the CI python job."
```

---

## Verification at the End of This Plan

Run: `uv run pytest -q`
Expected: all tests pass (55 from plan 1 + this plan's additions), no skips.

Manual smoke (optional, requires the real CLIs): none — real engine
integration is exercised by plan 6's acceptance run.

## Not in This Plan (deliberately)

- `archive_engine_attempt` writing real artifacts: plan 3 wires
  `RetryEvents.archive_attempt` to `archive.py`.
- `work()` / `run_review()` (they orchestrate archive + metrics + engines):
  plan 4/5.
- `compose_review_prompt` and the verdict-file instruction for non-claude
  reviewers: plan 4 (`review.py`).
- Console entry point: plan 5.
