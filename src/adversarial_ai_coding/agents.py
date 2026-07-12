"""Agent adapters and selection helpers.

Port of adversarial-ai-coding.sh:341-359 (validate_engines), 400-422
(is_builtin_engine, resolve_model_args), 689-696 (engine_model),
1090-1096 (generic_engine_args). Task 2 adds the subprocess adapters.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Settings, SettingsError

BUILTIN_AGENTS = ("claude", "codex", "agy")

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


def is_builtin_agent(name: str) -> bool:
    return name in BUILTIN_AGENTS


@dataclass(frozen=True)
class AgentRef:
    slot: str
    name: str


def agent_ref(slot: str, settings: Settings) -> AgentRef:
    if slot == "A":
        return AgentRef(slot="A", name=settings.agent_a)
    if slot == "B":
        return AgentRef(slot="B", name=settings.agent_b)
    raise ValueError(f"Unknown agent slot:{slot}")


def agent_model(ref: AgentRef, settings: Settings) -> str:
    # Custom agents ignore MODEL_A/MODEL_B; they get args via AGENT_*_ARGS.
    if not is_builtin_agent(ref.name):
        return ""
    if ref.slot == "A":
        return settings.model_a
    if ref.slot == "B":
        return settings.model_b
    return ""


def resolve_model_args(ref: AgentRef, settings: Settings) -> str:
    if ref.name == "claude":
        return settings.claude_args
    if ref.name == "codex":
        return settings.codex_args
    if ref.name == "agy":
        return settings.agy_args
    return generic_agent_args(ref, settings)


def generic_agent_args(ref: AgentRef, settings: Settings) -> str:
    if ref.slot == "A":
        return settings.agent_a_args
    if ref.slot == "B":
        return settings.agent_b_args
    return ""


def validate_agents(
    settings: Settings, which: Callable[[str], str | None] = shutil.which
) -> None:
    for name in (settings.agent_a, settings.agent_b):
        if which(name) is None:
            raise SettingsError(f"Missing required command:{name}")
    _validate_reserved_args(settings)
    # Built-in agents resume by exact session IDs. Custom agents still have an
    # unknown session model, so identical custom command names remain blocked.
    if settings.agent_a == settings.agent_b and settings.agent_a != "claude":
        if settings.agent_a in {"codex", "agy"}:
            return
        if is_builtin_agent(settings.agent_a):
            raise SettingsError(
                f"A and B cannot both use {settings.agent_a} because session "
                "resume would interfere. Use different agents."
            )
        raise SettingsError(
            f"A and B cannot both use custom agent command {settings.agent_a}. "
            "Use separate wrapper command names for worker and reviewer."
        )


def _validate_reserved_args(settings: Settings) -> None:
    tokens = settings.codex_args.split()
    for index, token in enumerate(tokens):
        normalized = token.strip("'\"")
        if (
            normalized in {"--json", "resume", "--sandbox", "-s"}
            or normalized.startswith("--sandbox=")
            or normalized.startswith("-s=")
        ):
            raise SettingsError(
                f"CODEX_ARGS cannot contain session-control argument:{token}"
            )
        if normalized in {"-c", "--config"} and index + 1 < len(tokens):
            value = tokens[index + 1].strip("'\"")
            if value.startswith("sandbox_mode="):
                raise SettingsError(
                    "CODEX_ARGS cannot override sandbox_mode; the workflow owns it"
                )
        if normalized.startswith("--config=sandbox_mode="):
            raise SettingsError(
                "CODEX_ARGS cannot override sandbox_mode; the workflow owns it"
            )
    for token in settings.agy_args.split():
        normalized = token.strip("'\"")
        if (
            normalized in {"--log-file", "--continue", "--conversation"}
            or normalized.startswith("--log-file=")
            or normalized.startswith("--continue=")
            or normalized.startswith("--conversation=")
        ):
            raise SettingsError(
                f"AGY_ARGS cannot contain session-control argument:{token}"
            )


@dataclass
class AgentSession:
    """Bash WORKER_SESSION/LAST_COST (sh:1036-1038), owned by the workflow.

    The workflow resets worker_session at stage boundaries (begin_stage);
    reviewer calls never read it -- each review round starts fresh.
    """

    worker_session: str = ""
    last_cost: str = ""


@dataclass
class AgentIO:
    agent_out: Path
    raw_out: Path
    verdict_path: Path
    echo: Callable[[str], None]


@dataclass
class AgentResult:
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
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.rstrip("\n")


def _run_streaming(argv: list[str], io: AgentIO) -> tuple[int, str]:
    # bash: cmd ... 2>&1 | tee "$ENGINE_OUT" -- merged output streamed and saved.
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert proc.stdout is not None
    with io.agent_out.open("w", encoding="utf-8") as out_file:
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            out_file.write(line + "\n")
            io.echo(line)
    rc = proc.wait()
    return rc, "\n".join(lines)


def _run_codex_json(argv: list[str], io: AgentIO) -> tuple[int, str, str]:
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    rendered: list[str] = []
    thread_id = ""
    assert proc.stdout is not None
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    io.agent_out.parent.mkdir(parents=True, exist_ok=True)
    with (
        io.raw_out.open("w", encoding="utf-8") as raw_file,
        io.agent_out.open("w", encoding="utf-8") as rendered_file,
    ):
        for raw_line in proc.stdout:
            raw_file.write(raw_line)
            line = raw_line.rstrip("\r\n")
            text = ""
            should_echo = False
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                text = line
                should_echo = True
            else:
                if not isinstance(payload, dict):
                    text = _jq_raw(payload)
                else:
                    event_type = payload.get("type")
                    if event_type == "thread.started":
                        parsed_id = payload.get("thread_id")
                        if isinstance(parsed_id, str) and parsed_id:
                            thread_id = parsed_id
                    elif event_type == "item.completed":
                        item = payload.get("item")
                        if isinstance(item, dict) and item.get("type") == "agent_message":
                            text = _jq_raw(item.get("text")) if item.get("text") is not None else ""
                            should_echo = bool(text)
                        else:
                            text = json.dumps(payload, ensure_ascii=False)
                    elif event_type == "error":
                        value = payload.get("message")
                        text = _jq_raw(value) if value is not None else json.dumps(payload, ensure_ascii=False)
                        should_echo = True
                    elif event_type == "turn.failed":
                        error = payload.get("error")
                        if isinstance(error, dict) and error.get("message") is not None:
                            text = _jq_raw(error["message"])
                        elif error is not None:
                            text = _jq_raw(error)
                        else:
                            text = json.dumps(payload, ensure_ascii=False)
                        should_echo = True
                    elif event_type != "thread.started":
                        text = json.dumps(payload, ensure_ascii=False)
            if text:
                rendered.append(text)
                rendered_file.write(text.rstrip("\n") + "\n")
                if should_echo:
                    io.echo(text)
    rc = proc.wait()
    return rc, "\n".join(rendered), thread_id


def _write_agent_out(io: AgentIO, text: str) -> None:
    io.agent_out.write_text(text + "\n", encoding="utf-8")


def _jq_raw(value: object) -> str:
    """Render a decoded JSON value like jq -r."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _jq_coalesce_empty(payload: dict[str, object], field: str) -> str:
    """Render `.field // empty` followed by jq's raw-output conversion."""
    value = payload.get(field)
    if value is None or value is False:
        return ""
    return _jq_raw(value)


def _claude_common_args(ref: AgentRef, settings: Settings) -> list[str]:
    args: list[str] = []
    model = agent_model(ref, settings)
    if model:
        args += ["--model", model]
    args += settings.claude_args.split()
    return args


def _worker_claude(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
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
    argv += _claude_common_args(ref, settings)
    if session.worker_session:
        argv += ["--resume", session.worker_session]
    rc, out = _run_captured(argv)
    _write_agent_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return AgentResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        # Lenient divergence: bash's jq failure degraded to empty fields.
        return AgentResult(0, out)
    if payload is None:
        session.worker_session = "null"
        session.last_cost = ""
        return AgentResult(0, "")
    if not isinstance(payload, dict):
        session.worker_session = ""
        session.last_cost = ""
        return AgentResult(5, "")
    session.worker_session = _jq_raw(payload.get("session_id"))
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return AgentResult(0, _jq_coalesce_empty(payload, "result"))


def _reviewer_claude(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    argv = [_resolve_argv0("claude"), "-p", prompt]
    argv += _claude_common_args(ref, settings)
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
    _write_agent_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return AgentResult(rc, out)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return AgentResult(5, "")
    if payload is None:
        io.verdict_path.write_text(json.dumps(VERDICT_FALLBACK), encoding="utf-8")
        session.last_cost = ""
        return AgentResult(0, "")
    if not isinstance(payload, dict):
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return AgentResult(5, "")
    structured_output = payload.get("structured_output")
    verdict = (
        VERDICT_FALLBACK
        if structured_output is None or structured_output is False
        else structured_output
    )
    io.verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return AgentResult(0, _jq_coalesce_empty(payload, "result"))


def _codex_model_args(ref: AgentRef, settings: Settings) -> list[str]:
    args: list[str] = []
    model = agent_model(ref, settings)
    if model:
        args += ["-c", f'model="{model}"']
    args += settings.codex_args.split()
    return args


def _worker_codex(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    model_args = _codex_model_args(ref, settings)
    if not session.worker_session:
        argv = [
            _resolve_argv0("codex"),
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            *model_args,
            prompt,
        ]
    else:
        # exec resume has no --sandbox flag, so override config with -c (sh:1072).
        argv = [
            _resolve_argv0("codex"),
            "exec",
            "resume",
            "--json",
            "-c",
            'sandbox_mode="workspace-write"',
            *model_args,
            session.worker_session,
            prompt,
        ]
    rc, out, thread_id = _run_codex_json(argv, io)
    if thread_id:
        session.worker_session = thread_id
    elif not session.worker_session:
        io.echo(
            "(warning: codex did not report a thread ID; the next worker call "
            "will start a fresh session)"
        )
    session.last_cost = ""
    return AgentResult(rc, out)


def _reviewer_codex(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    argv = [
        _resolve_argv0("codex"),
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        *_codex_model_args(ref, settings),
        prompt,
    ]
    rc, out, _ = _run_codex_json(argv, io)
    session.last_cost = ""
    return AgentResult(rc, out)


def _agy_model_args(ref: AgentRef, settings: Settings) -> list[str]:
    args: list[str] = []
    model = agent_model(ref, settings)
    if model:
        args += ["--model", model]
    args += settings.agy_args.split()
    return args


_AGY_UUID = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
_AGY_CONVERSATION = re.compile(
    rf"^(?:Created conversation\s+|Print mode:\s*conversation=)"
    rf"({_AGY_UUID})(?![0-9a-f-])",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_agy_conversation_id(log_path: Path) -> str:
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    matches = [match.lower() for match in _AGY_CONVERSATION.findall(text)]
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _new_agy_attempt_log(io: AgentIO) -> Path:
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(
        prefix="agy-attempt-", suffix=".log", dir=io.raw_out.parent
    )
    os.close(fd)
    log_path = Path(raw_path)
    log_path.unlink(missing_ok=True)
    io.raw_out = log_path
    return log_path


def _worker_agy(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    # --dangerously-skip-permissions approves every tool action; prefer an
    # isolated branch, worktree, or container when using agy (sh:1078-1079).
    log_path = _new_agy_attempt_log(io)
    argv = [
        _resolve_argv0("agy"),
        "--print",
        prompt,
        "--print-timeout",
        "60m",
        "--dangerously-skip-permissions",
    ]
    argv += _agy_model_args(ref, settings)
    argv += ["--log-file", str(log_path)]
    if session.worker_session:
        argv += ["--conversation", session.worker_session]
    rc, out = _run_streaming(argv, io)
    conversation_id = _parse_agy_conversation_id(log_path)
    if conversation_id:
        session.worker_session = conversation_id
    elif not session.worker_session:
        io.echo(
            "(warning: agy did not report a conversation ID; the next worker "
            "call will start a fresh session)"
        )
    session.last_cost = ""
    return AgentResult(rc, out)


def _reviewer_agy(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    argv = [
        _resolve_argv0("agy"),
        "--print",
        prompt,
        "--print-timeout",
        "30m",
        "--dangerously-skip-permissions",
    ]
    argv += _agy_model_args(ref, settings)
    rc, out = _run_streaming(argv, io)
    return AgentResult(rc, out)


def _run_generic(
    ref: AgentRef, prompt: str, settings: Settings, io: AgentIO
) -> AgentResult:
    argv = [
        _resolve_argv0(ref.name),
        *generic_agent_args(ref, settings).split(),
        prompt,
    ]
    rc, out = _run_streaming(argv, io)
    return AgentResult(rc, out)


def run_worker(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    io.raw_out.unlink(missing_ok=True)
    if ref.name == "claude":
        return _worker_claude(ref, prompt, settings, session, io)
    if ref.name == "codex":
        return _worker_codex(ref, prompt, settings, session, io)
    if ref.name == "agy":
        return _worker_agy(ref, prompt, settings, session, io)
    return _run_generic(ref, prompt, settings, io)


def run_reviewer(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    io.raw_out.unlink(missing_ok=True)
    if ref.name == "claude":
        return _reviewer_claude(ref, prompt, settings, session, io)
    if ref.name == "codex":
        return _reviewer_codex(ref, prompt, settings, session, io)
    if ref.name == "agy":
        return _reviewer_agy(ref, prompt, settings, session, io)
    return _run_generic(ref, prompt, settings, io)


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
