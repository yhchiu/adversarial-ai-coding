"""Engine adapters and selection helpers.

Port of adversarial-ai-coding.sh:341-359 (validate_engines), 400-422
(is_builtin_engine, resolve_model_args), 689-696 (engine_model),
1090-1096 (generic_engine_args). Task 2 adds the subprocess adapters.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings, SettingsError

BUILTIN_ENGINES = ("claude", "codex", "agy")

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


@dataclass
class EngineSession:
    """Bash WORKER_SESSION/LAST_COST (sh:1036-1038), owned by the workflow.

    The workflow resets worker_session at stage boundaries (begin_stage);
    reviewer calls never read it -- each review round starts fresh.
    """

    worker_session: str = ""
    last_cost: str = ""


@dataclass
class EngineIO:
    engine_out: Path
    verdict_path: Path
    echo: Callable[[str], None]


@dataclass
class EngineResult:
    rc: int
    text: str


def _resolve_argv0(name: str) -> str:
    # Windows: claude/codex/agy install as .cmd shims; Popen needs the
    # resolved path (bash resolved via PATH natively).
    return shutil.which(name) or name


def _run_captured(argv: list[str]) -> tuple[int, str]:
    # bash: out=$(cmd ...) -- stdout captured, stderr passes through.
    proc = subprocess.run(
        argv,
        capture_output=False,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def _run_streaming(argv: list[str], io: EngineIO) -> tuple[int, str]:
    # bash: cmd ... 2>&1 | tee "$ENGINE_OUT" -- merged output streamed and saved.
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
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


def _jq_raw(value: object) -> str:
    """Render a decoded JSON value like jq -r."""
    if isinstance(value, str):
        return value
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _jq_coalesce_empty(payload: dict[str, object], field: str) -> str:
    """Render `.field // empty` followed by jq's raw-output conversion."""
    value = payload.get(field)
    if value is None or value is False:
        return ""
    return _jq_raw(value)


def _claude_common_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("claude", settings)
    if model:
        args += ["--model", model]
    args += settings.claude_args.split()
    return args


def _worker_claude(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    argv = [
        _resolve_argv0("claude"),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        settings.tools,
    ]
    argv += _claude_common_args(settings)
    if session.worker_session:
        argv += ["--resume", session.worker_session]
    rc, out = _run_captured(argv)
    _write_engine_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return EngineResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        # Lenient divergence: bash's jq failure degraded to empty fields.
        return EngineResult(0, out)
    if payload is None:
        session.worker_session = "null"
        session.last_cost = ""
        return EngineResult(0, "")
    if not isinstance(payload, dict):
        session.worker_session = ""
        session.last_cost = ""
        return EngineResult(5, "")
    session.worker_session = _jq_raw(payload.get("session_id"))
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return EngineResult(0, _jq_coalesce_empty(payload, "result"))


def _reviewer_claude(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    argv = [_resolve_argv0("claude"), "-p", prompt]
    argv += _claude_common_args(settings)
    argv += [
        "--output-format",
        "json",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        settings.tools,
        "--json-schema",
        VERDICT_SCHEMA,
    ]
    rc, out = _run_captured(argv)
    _write_engine_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return EngineResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return EngineResult(5, "")
    if payload is None:
        io.verdict_path.write_text(json.dumps(VERDICT_FALLBACK), encoding="utf-8")
        session.last_cost = ""
        return EngineResult(0, "")
    if not isinstance(payload, dict):
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return EngineResult(5, "")
    structured_output = payload.get("structured_output")
    verdict = (
        VERDICT_FALLBACK
        if structured_output is None or structured_output is False
        else structured_output
    )
    io.verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return EngineResult(0, _jq_coalesce_empty(payload, "result"))


def _codex_model_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("codex", settings)
    if model:
        args += ["-c", f'model="{model}"']
    args += settings.codex_args.split()
    return args


def _worker_codex(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    model_args = _codex_model_args(settings)
    if not session.worker_session:
        argv = [
            _resolve_argv0("codex"),
            "exec",
            "--sandbox",
            "workspace-write",
            *model_args,
            prompt,
        ]
        rc, out = _run_streaming(argv, io)
        session.worker_session = "last"
    else:
        # exec resume has no --sandbox flag, so override config with -c (sh:1072).
        argv = [
            _resolve_argv0("codex"),
            "exec",
            "resume",
            "--last",
            "-c",
            'sandbox_mode="workspace-write"',
            *model_args,
            prompt,
        ]
        rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _reviewer_codex(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    argv = [
        _resolve_argv0("codex"),
        "exec",
        "--sandbox",
        "workspace-write",
        *_codex_model_args(settings),
        prompt,
    ]
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _agy_model_args(settings: Settings) -> list[str]:
    args: list[str] = []
    model = engine_model("agy", settings)
    if model:
        args += ["--model", model]
    args += settings.agy_args.split()
    return args


def _worker_agy(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    # --dangerously-skip-permissions approves every tool action; prefer an
    # isolated branch, worktree, or container when using agy (sh:1078-1079).
    argv = [
        _resolve_argv0("agy"),
        "--print",
        prompt,
        "--print-timeout",
        "60m",
        "--dangerously-skip-permissions",
    ]
    argv += _agy_model_args(settings)
    if session.worker_session:
        argv += ["--continue"]
    rc, out = _run_streaming(argv, io)
    session.worker_session = "continue"
    return EngineResult(rc, out)


def _reviewer_agy(
    prompt: str, settings: Settings, session: EngineSession, io: EngineIO
) -> EngineResult:
    argv = [
        _resolve_argv0("agy"),
        "--print",
        prompt,
        "--print-timeout",
        "30m",
        "--dangerously-skip-permissions",
    ]
    argv += _agy_model_args(settings)
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def _run_generic(
    name: str, prompt: str, settings: Settings, io: EngineIO
) -> EngineResult:
    argv = [_resolve_argv0(name), *generic_engine_args(name, settings).split(), prompt]
    rc, out = _run_streaming(argv, io)
    return EngineResult(rc, out)


def run_worker(
    name: str,
    prompt: str,
    settings: Settings,
    session: EngineSession,
    io: EngineIO,
) -> EngineResult:
    if name == "claude":
        return _worker_claude(prompt, settings, session, io)
    if name == "codex":
        return _worker_codex(prompt, settings, session, io)
    if name == "agy":
        return _worker_agy(prompt, settings, session, io)
    return _run_generic(name, prompt, settings, io)


def run_reviewer(
    name: str,
    prompt: str,
    settings: Settings,
    session: EngineSession,
    io: EngineIO,
) -> EngineResult:
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
    argv = settings.notify_cmd.split() + [message]
    try:
        rc = subprocess.run(argv).returncode
    except OSError:
        rc = 1
    if rc != 0:
        print(f"(notification command failed:{settings.notify_cmd})", file=sys.stderr)
