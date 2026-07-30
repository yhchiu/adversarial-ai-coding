"""Agent adapters and selection helpers.

Port of adversarial-ai-coding.sh:341-359 (validate_engines), 400-422
(is_builtin_engine, resolve_model_args), 689-696 (engine_model),
1090-1096 (generic_engine_args). Task 2 adds the subprocess adapters.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
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
    base_slot: str = ""


def agent_ref(slot: str, settings: Settings) -> AgentRef:
    if slot == "A":
        return AgentRef(slot="A", name=settings.agent_a)
    if slot == "B":
        return AgentRef(slot="B", name=settings.agent_b)
    raise ValueError(f"Unknown agent slot:{slot}")


def impl_ref(owner: AgentRef, settings: Settings) -> AgentRef:
    if not (settings.impl_agent or settings.impl_model or settings.impl_args):
        return owner
    name = settings.impl_agent or owner.name
    base_slot = owner.slot if name == owner.name else ""
    ref = AgentRef(slot="I", name=name, base_slot=base_slot)
    _validate_impl_args(settings, (ref.name,))
    return ref


def agent_model(ref: AgentRef, settings: Settings) -> str:
    # Custom agents ignore MODEL_A/MODEL_B; they get args via AGENT_*_ARGS.
    if not is_builtin_agent(ref.name):
        return ""
    if ref.slot == "I":
        if settings.impl_model:
            return settings.impl_model
        if ref.base_slot not in {"A", "B"}:
            return ""
        base_ref = agent_ref(ref.base_slot, settings)
        if ref.name != base_ref.name:
            return ""
        return agent_model(base_ref, settings)
    if ref.slot == "A":
        return settings.model_a
    if ref.slot == "B":
        return settings.model_b
    return ""


def generic_agent_args(ref: AgentRef, settings: Settings) -> str:
    if ref.slot == "A":
        return settings.agent_a_args
    if ref.slot == "B":
        return settings.agent_b_args
    if ref.slot == "I":
        return settings.impl_args
    return ""


def _arg_sources(ref: AgentRef, settings: Settings) -> list[tuple[str, str]]:
    sources: list[tuple[str, str]] = []
    if ref.name == "claude":
        sources.append(("CLAUDE_ARGS", settings.claude_args))
    elif ref.name == "codex":
        sources.append(("CODEX_ARGS", settings.codex_args))
    elif ref.name == "agy":
        sources.append(("AGY_ARGS", settings.agy_args))
    elif ref.slot == "A":
        sources.append(("AGENT_A_ARGS", generic_agent_args(ref, settings)))
    elif ref.slot == "B":
        sources.append(("AGENT_B_ARGS", generic_agent_args(ref, settings)))
    elif ref.slot == "I":
        sources.append(("IMPL_ARGS", generic_agent_args(ref, settings)))
    if ref.slot == "I" and is_builtin_agent(ref.name):
        sources.append(("IMPL_ARGS", settings.impl_args))
    return [(variable, raw) for variable, raw in sources if raw]


def agent_args(ref: AgentRef, settings: Settings) -> list[str]:
    return [
        token
        for variable, raw in _arg_sources(ref, settings)
        for token in _split_cli_args(variable, raw)
    ]


def resolve_model_args(ref: AgentRef, settings: Settings) -> str:
    return " ".join(raw for _, raw in _arg_sources(ref, settings))


def validate_agents(
    settings: Settings, which: Callable[[str], str | None] = shutil.which
) -> None:
    required = [settings.agent_a, settings.agent_b]
    if settings.impl_agent:
        required.append(settings.impl_agent)
    for name in required:
        if which(name) is None:
            raise SettingsError(f"Missing required command:{name}")
    _validate_reserved_args(settings)
    _validate_custom_impl_command(settings)
    # Built-in agents resume by exact session IDs. Custom agents still have an
    # unknown session model, so identical custom command names remain blocked.
    if settings.agent_a == settings.agent_b:
        if is_builtin_agent(settings.agent_a):
            return
        raise SettingsError(
            f"A and B cannot both use custom agent command {settings.agent_a}. "
            "Use separate wrapper command names for worker and reviewer."
        )


def _impl_owner_candidates(settings: Settings) -> tuple[str, ...]:
    if settings.dual_spec:
        return settings.agent_a, settings.agent_b
    return (settings.agent_a,)


def _custom_impl_conflict(name: str) -> SettingsError:
    return SettingsError(
        f"Implementation slot cannot reuse custom agent command {name}. "
        "Set IMPL_AGENT to a different wrapper command."
    )


def _validate_custom_impl_command(settings: Settings) -> None:
    if settings.impl_agent:
        if not is_builtin_agent(settings.impl_agent) and settings.impl_agent in {
            settings.agent_a,
            settings.agent_b,
        }:
            raise _custom_impl_conflict(settings.impl_agent)
        return

    if not (settings.impl_model or settings.impl_args):
        return
    for name in _impl_owner_candidates(settings):
        if not is_builtin_agent(name):
            raise _custom_impl_conflict(name)


def _split_cli_args(variable: str, raw: str) -> list[str]:
    try:
        return shlex.split(raw, posix=True)
    except ValueError as exc:
        raise SettingsError(
            f"{variable} contains invalid shell quoting: {exc}"
        ) from None


def _matches_option(token: str, option: str) -> bool:
    return token == option or token.startswith(f"{option}=")


def _matches_short_option(token: str, option: str) -> bool:
    return token == option or (
        token.startswith(option) and len(token) > len(option)
    )


def _option_value(
    tokens: list[str], index: int, short: str, long: str
) -> str:
    token = tokens[index]
    if token in {short, long}:
        return tokens[index + 1] if index + 1 < len(tokens) else ""
    if token.startswith(f"{long}="):
        return token.removeprefix(f"{long}=")
    if token.startswith(short) and token != short:
        return token.removeprefix(short).removeprefix("=")
    return ""


def _model_conflict(variable: str) -> SettingsError:
    return SettingsError(
        f"{variable} cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


def _validate_builtin_arg_tokens(
    variable: str, adapter: str, tokens: list[str]
) -> None:
    for index, token in enumerate(tokens):
        if _matches_option(token, "--model") or _matches_short_option(token, "-m"):
            raise _model_conflict(variable)

        if adapter == "claude" and (
            token in {"-c", "-r"}
            or any(
                _matches_option(token, option)
                for option in {
                    "--continue",
                    "--resume",
                    "--session-id",
                    "--fork-session",
                    "--no-session-persistence",
                    "--from-pr",
                    "--output-format",
                    "--verbose",
                    "--json-schema",
                }
            )
        ):
            raise SettingsError(
                f"{variable} cannot contain workflow-owned argument:{token}"
            )

        if adapter == "codex":
            if (
                _matches_option(token, "--json")
                or token == "resume"
                or _matches_option(token, "--sandbox")
                or _matches_short_option(token, "-s")
                or any(
                    _matches_option(token, option)
                    for option in {
                        "--dangerously-bypass-approvals-and-sandbox",
                        "--yolo",
                        "--ephemeral",
                    }
                )
            ):
                raise SettingsError(
                    f"{variable} cannot contain session-control argument:{token}"
                )

            value = _option_value(tokens, index, "-c", "--config")
            key = value.split("=", 1)[0].strip()
            if key == "model":
                raise _model_conflict(variable)
            if key == "sandbox_mode":
                raise SettingsError(
                    f"{variable} cannot override sandbox_mode; the workflow owns it"
                )

        if adapter == "agy" and any(
            _matches_option(token, option)
            for option in {"--log-file", "--continue", "--conversation"}
        ):
            raise SettingsError(
                f"{variable} cannot contain session-control argument:{token}"
            )


def _validate_reserved_args(settings: Settings) -> None:
    for variable, adapter, raw in (
        ("CLAUDE_ARGS", "claude", settings.claude_args),
        ("CODEX_ARGS", "codex", settings.codex_args),
        ("AGY_ARGS", "agy", settings.agy_args),
    ):
        _validate_builtin_arg_tokens(variable, adapter, _split_cli_args(variable, raw))

    _split_cli_args("AGENT_A_ARGS", settings.agent_a_args)
    _split_cli_args("AGENT_B_ARGS", settings.agent_b_args)
    adapters = (
        (settings.impl_agent,)
        if settings.impl_agent
        else _impl_owner_candidates(settings)
    )
    _validate_impl_args(settings, adapters)


def _validate_impl_args(settings: Settings, adapters: tuple[str, ...]) -> None:
    tokens = _split_cli_args("IMPL_ARGS", settings.impl_args)
    for adapter in dict.fromkeys(adapters):
        if is_builtin_agent(adapter):
            _validate_builtin_arg_tokens("IMPL_ARGS", adapter, tokens)


@dataclass
class AgentSession:
    """One active worker session and its full agent-ref owner.

    Changing owners discards the stored worker session. The workflow also
    resets both fields at stage boundaries; reviewer calls never read them.
    """

    worker_session: str = ""
    last_cost: str = ""
    owner: AgentRef | None = None


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


def agent_prefix(ref: AgentRef) -> str:
    """The marker that tells streamed agent lines apart from workflow ones.

    A custom agent may be configured as a full path, so use the file name
    to keep the prefix short.
    """
    return f"[{ref.slot} {PurePath(ref.name).name or ref.name}] "


def _echo_agent(io: AgentIO, ref: AgentRef, text: str) -> None:
    """Echo agent output prefixed per line; files never get the prefix."""
    prefix = agent_prefix(ref)
    for line in text.split("\n"):
        io.echo(prefix + line)


def _run_streaming(argv: list[str], io: AgentIO, ref: AgentRef) -> tuple[int, str]:
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
            _echo_agent(io, ref, line)
    rc = proc.wait()
    return rc, "\n".join(lines)


# The tool input field that best identifies what a call is touching,
# most specific first. Everything else in the input is dropped so a Write
# with a thousand-line body still costs one short line.
_TOOL_ARG_KEYS = (
    "file_path",
    "notebook_path",
    "command",
    # Before "path": a search names what it looks for, not where it looks.
    "pattern",
    "url",
    "query",
    "path",
    "prompt",
    "description",
)
_TOOL_ARG_LIMIT = 100


def _tool_summary(block: dict[str, object]) -> str:
    """One line naming a tool call and the thing it acts on."""
    name = block.get("name")
    label = name if isinstance(name, str) and name else "tool"
    payload = block.get("input")
    detail = ""
    if isinstance(payload, dict):
        for key in _TOOL_ARG_KEYS:
            value = payload.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                detail = str(value).split("\n", 1)[0].strip()
                if detail:
                    break
                detail = ""
    if len(detail) > _TOOL_ARG_LIMIT:
        detail = detail[:_TOOL_ARG_LIMIT] + "..."
    return f" . {label} {detail}".rstrip()


def render_claude_event(line: str) -> tuple[list[str], str]:
    """Render one claude NDJSON line into (echo lines, envelope).

    The envelope is non-empty only for the final result event, which is
    what the adapters parse for the session id, the cost, and the result.
    Keeping this pure is what makes the whole rendering layer testable
    without a subprocess, while the caller stays free to echo per line.
    """
    text = line.rstrip("\r\n")
    if not text.strip():
        return [], ""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Merged stderr and CLI warnings are exactly what the user needs
        # to see when something goes wrong, so pass them straight through.
        return [text], ""
    if not isinstance(payload, dict):
        return [text], ""
    event_type = payload.get("type")
    if event_type == "result":
        return [], text
    if event_type != "assistant":
        return [], ""
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return [], ""
    echoed: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            body = block.get("text")
            if isinstance(body, str):
                echoed += [part for part in body.split("\n") if part.strip()]
        elif kind == "tool_use":
            echoed.append(_tool_summary(block))
    return echoed, ""


def _run_claude_stream(
    argv: list[str], io: AgentIO, ref: AgentRef
) -> tuple[int, str]:
    """Stream claude NDJSON, returning (rc, envelope).

    The envelope is the same JSON object the old `--output-format json`
    call produced, so every caller downstream is unchanged. When no
    result event arrives -- a crash, a kill, a quota abort -- it falls
    back to the whole raw stream, because ratelimit.py
    reads only agent_out and an empty file would silently stop quota
    retries in exactly the case that needs them.
    """
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    raw: list[str] = []
    envelope = ""
    assert proc.stdout is not None
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    with io.raw_out.open("w", encoding="utf-8") as raw_file:
        for raw_line in proc.stdout:
            raw_file.write(raw_line)
            raw.append(raw_line.rstrip("\r\n"))
            echo_lines, found = render_claude_event(raw_line)
            for text in echo_lines:
                _echo_agent(io, ref, text)
            if found:
                envelope = found
    rc = proc.wait()
    return rc, envelope or "\n".join(raw)


# Codex names its tool calls after the item type; these read better.
# Unknown types keep their own name so a codex upgrade stays visible.
_CODEX_ITEM_LABELS = {"command_execution": "run", "file_change": "edit"}

# Codex reports a shell call as the full interpreter invocation, so on
# Windows the first ~66 characters are the powershell.exe path and the
# real command falls outside the truncation limit. Only strip the wrapper
# when the leading program really is a shell: matching "-c" on anything
# would maul commands like `git -c user.name=x commit`.
_SHELLS = frozenset(
    {
        "powershell.exe",
        "powershell",
        "pwsh.exe",
        "pwsh",
        "cmd.exe",
        "cmd",
        "bash.exe",
        "bash",
        "sh.exe",
        "sh",
        "zsh.exe",
        "zsh",
    }
)
_SHELL_WRAPPER = re.compile(
    r"^(?P<exe>\"[^\"]+\"|'[^']+'|\S+)\s+(?:-Command|-c|/c|/C)\s+(?P<rest>.+)$",
    re.DOTALL | re.IGNORECASE,
)


def _strip_shell_wrapper(command: str) -> str:
    """Return the inner command of a `<shell> -Command <cmd>` invocation.

    The rest of the string is sliced out verbatim rather than tokenized
    and rejoined, which would lose the agent's own quoting.
    """
    match = _SHELL_WRAPPER.match(command)
    if match is None:
        return command
    exe = match.group("exe").strip("\"'")
    if PurePath(exe).name.lower() not in _SHELLS:
        return command
    inner = match.group("rest")
    for quote in ("'", '"'):
        if len(inner) > 1 and inner.startswith(quote) and inner.endswith(quote):
            inner = inner[1:-1]
            break
    return inner or command


def _relative_path(raw: str, cwd: Path | None) -> str:
    if cwd is None:
        return raw
    try:
        return str(Path(raw).relative_to(cwd))
    except ValueError:
        return raw


def _codex_file_change_detail(item: dict[str, object], cwd: Path | None) -> str:
    changes = item.get("changes")
    if not isinstance(changes, list) or not changes:
        return ""
    first = changes[0]
    if not isinstance(first, dict):
        return ""
    raw_path = first.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return ""
    detail = _relative_path(raw_path, cwd)
    kind = first.get("kind")
    if isinstance(kind, str) and kind:
        detail = f"{detail} ({kind})"
    if len(changes) > 1:
        detail = f"{detail} +{len(changes) - 1} more"
    return detail


def _codex_item_summary(item: dict[str, object], cwd: Path | None) -> str:
    """One line naming a codex tool call and the thing it acts on."""
    kind = item.get("type")
    label = kind if isinstance(kind, str) and kind else "item"
    detail = ""
    if kind == "command_execution":
        command = item.get("command")
        if isinstance(command, str) and command:
            detail = _strip_shell_wrapper(command).split("\n", 1)[0].strip()
    elif kind == "file_change":
        detail = _codex_file_change_detail(item, cwd)
    if len(detail) > _TOOL_ARG_LIMIT:
        detail = detail[:_TOOL_ARG_LIMIT] + "..."
    return f" . {_CODEX_ITEM_LABELS.get(label, label)} {detail}".rstrip()


def render_codex_event(
    line: str, cwd: Path | None = None
) -> tuple[str, bool, str]:
    """Render one codex NDJSON line into (text, should echo, thread id).

    The text is what lands in agent_out; only some of it is worth putting
    on the terminal. Unknown event types still render as raw JSON so a
    codex upgrade stays diagnosable, and quota wording in an error event
    keeps reaching ratelimit.py through agent_out.
    """
    text = line.rstrip("\r\n")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text, True, ""
    if not isinstance(payload, dict):
        return _jq_raw(payload), False, ""
    event_type = payload.get("type")
    if event_type == "thread.started":
        parsed_id = payload.get("thread_id")
        return "", False, parsed_id if isinstance(parsed_id, str) else ""
    if event_type == "item.started":
        # The live heartbeat: this fires when the tool call starts, not
        # when it finishes, so a ten-minute command is visible up front.
        item = payload.get("item")
        if isinstance(item, dict):
            return _codex_item_summary(item, cwd), True, ""
        return json.dumps(payload, ensure_ascii=False), False, ""
    if event_type == "item.completed":
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            body = item.get("text")
            rendered = _jq_raw(body) if body is not None else ""
            return rendered, bool(rendered), ""
        return json.dumps(payload, ensure_ascii=False), False, ""
    if event_type == "error":
        value = payload.get("message")
        rendered = (
            _jq_raw(value)
            if value is not None
            else json.dumps(payload, ensure_ascii=False)
        )
        return rendered, True, ""
    if event_type == "turn.failed":
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message") is not None:
            rendered = _jq_raw(error["message"])
        elif error is not None:
            rendered = _jq_raw(error)
        else:
            rendered = json.dumps(payload, ensure_ascii=False)
        return rendered, True, ""
    return json.dumps(payload, ensure_ascii=False), False, ""


def _run_codex_json(
    argv: list[str], io: AgentIO, ref: AgentRef
) -> tuple[int, str, str]:
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
    cwd = Path.cwd()
    assert proc.stdout is not None
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    io.agent_out.parent.mkdir(parents=True, exist_ok=True)
    with (
        io.raw_out.open("w", encoding="utf-8") as raw_file,
        io.agent_out.open("w", encoding="utf-8") as rendered_file,
    ):
        for raw_line in proc.stdout:
            raw_file.write(raw_line)
            text, should_echo, parsed_id = render_codex_event(raw_line, cwd)
            if parsed_id:
                thread_id = parsed_id
            if text:
                rendered.append(text)
                rendered_file.write(text.rstrip("\n") + "\n")
                if should_echo:
                    _echo_agent(io, ref, text)
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
    args += agent_args(ref, settings)
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
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        settings.tools,
    ]
    argv += _claude_common_args(ref, settings)
    if session.worker_session:
        argv += ["--resume", session.worker_session]
    rc, out = _run_claude_stream(argv, io, ref)
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
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        settings.tools,
        "--json-schema",
        VERDICT_SCHEMA,
    ]
    rc, out = _run_claude_stream(argv, io, ref)
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
    args += agent_args(ref, settings)
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
    rc, out, thread_id = _run_codex_json(argv, io, ref)
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
    rc, out, _ = _run_codex_json(argv, io, ref)
    session.last_cost = ""
    return AgentResult(rc, out)


def _agy_model_args(ref: AgentRef, settings: Settings) -> list[str]:
    args: list[str] = []
    model = agent_model(ref, settings)
    if model:
        args += ["--model", model]
    args += agent_args(ref, settings)
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
    rc, out = _run_streaming(argv, io, ref)
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
    rc, out = _run_streaming(argv, io, ref)
    return AgentResult(rc, out)


def _run_generic(
    ref: AgentRef, prompt: str, settings: Settings, io: AgentIO
) -> AgentResult:
    argv = [
        _resolve_argv0(ref.name),
        *agent_args(ref, settings),
        prompt,
    ]
    rc, out = _run_streaming(argv, io, ref)
    return AgentResult(rc, out)


def run_worker(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    if session.owner != ref:
        session.worker_session = ""
        session.owner = ref
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
