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
from dataclasses import dataclass, field
from pathlib import Path, PurePath, PureWindowsPath
from typing import Callable

from .config import Settings, SettingsError

BUILTIN_AGENTS = ("claude", "codex", "agy", "opencode")

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
    # The owner is settled by the time the workflow asks for this ref, so
    # every rule of the resolved command applies, including the ones that
    # had to wait at startup while two dual-spec candidates were live.
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
    if ref.slot == "A":
        sources.append(("AGENT_A_ARGS", generic_agent_args(ref, settings)))
    elif ref.slot == "B":
        sources.append(("AGENT_B_ARGS", generic_agent_args(ref, settings)))
    elif ref.slot == "I":
        sources.append(("IMPL_ARGS", generic_agent_args(ref, settings)))
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
            raise SettingsError("Missing required command:{name}", name=name)
    _validate_reserved_args(settings)
    _validate_custom_impl_command(settings)
    # Built-in agents resume by exact session IDs. Custom agents still have an
    # unknown session model, so identical custom command names remain blocked.
    if settings.agent_a == settings.agent_b:
        if is_builtin_agent(settings.agent_a):
            return
        raise SettingsError(
            "A and B cannot both use custom agent command {command}. "
            "Use separate wrapper command names for worker and reviewer.",
            command=settings.agent_a,
        )


def _impl_owner_candidates(settings: Settings) -> tuple[str, ...]:
    if settings.dual_spec:
        return settings.agent_a, settings.agent_b
    return (settings.agent_a,)


def _impl_candidates(settings: Settings) -> tuple[str, ...]:
    """Every command the implementation slot could end up running.

    With dual spec the owner is whichever of A and B a human selects
    several stages later, so both are live possibilities at startup.
    """
    if settings.impl_agent:
        return (settings.impl_agent,)
    return tuple(dict.fromkeys(_impl_owner_candidates(settings)))


def _custom_impl_conflict(name: str) -> SettingsError:
    return SettingsError(
        "Implementation slot cannot reuse custom agent command {name}. "
        "Set IMPL_AGENT to a different wrapper command.",
        name=name,
    )


def _validate_custom_impl_command(settings: Settings) -> None:
    """Reject an explicit implementation wrapper that A or B already uses.

    An inherited custom owner is judged in `_impl_rejection` instead,
    because whether it is inherited at all depends on the owner.
    """
    if (
        settings.impl_agent
        and not is_builtin_agent(settings.impl_agent)
        and settings.impl_agent in {settings.agent_a, settings.agent_b}
    ):
        raise _custom_impl_conflict(settings.impl_agent)


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


def _matches_go_option(token: str, option: str) -> bool:
    """Match a Go flag package option written with one or two dashes.

    The flag package treats `-flag` and `--flag` as the same flag, so a
    name reserved for agy has to be blocked in both spellings. Attached
    values such as `-pVALUE` are not a Go form and stay unmatched, which
    is what keeps `--project` out of the `-p` rule.
    """
    name = option.lstrip("-")
    return any(
        token == f"{dashes}{name}" or token.startswith(f"{dashes}{name}=")
        for dashes in ("-", "--")
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


def _long_option_value(tokens: list[str], index: int, option: str) -> str:
    """The value of a long-only option already known to match at `index`."""
    token = tokens[index]
    if token == option:
        return tokens[index + 1] if index + 1 < len(tokens) else ""
    return token.removeprefix(f"{option}=")


def _go_option_value(tokens: list[str], index: int, option: str) -> str:
    """The value of a Go flag written with either one dash or two."""
    name = option.lstrip("-")
    token = tokens[index]
    for dashes in ("-", "--"):
        candidate = f"{dashes}{name}"
        if token == candidate:
            return tokens[index + 1] if index + 1 < len(tokens) else ""
        if token.startswith(f"{candidate}="):
            return token.removeprefix(f"{candidate}=")
    return ""


# Claude normalises "manual" to "default", and both wait for an answer no
# headless run can give. "plan" forbids the edits every implementation
# stage exists to make. The remaining modes only widen what the agent may
# do, which is the user's call.
HEADLESS_IMPOSSIBLE_CLAUDE_MODES = ("default", "manual", "plan")
USABLE_CLAUDE_MODES = "acceptEdits, auto, bypassPermissions, or dontAsk"
USABLE_AGY_MODES = "accept-edits"


def _headless_mode_conflict(
    variable: str, option: str, mode: str, usable: str
) -> SettingsError:
    return SettingsError(
        f"{variable} cannot set {option} {mode}; the workflow runs headless, "
        "so a mode that waits for an answer or forbids edits can never "
        f"finish a stage. Use {usable}."
    )


def _model_conflict(variable: str) -> SettingsError:
    return SettingsError(
        f"{variable} cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


def _tools_conflict(variable: str) -> SettingsError:
    return SettingsError(
        f"{variable} cannot set the tool allowlist; use TOOLS instead"
    )


def _validate_builtin_arg_tokens(
    variable: str, adapter: str, tokens: list[str]
) -> None:
    for index, token in enumerate(tokens):
        if adapter == "agy":
            # Go flags have no attached short form and agy has no -m, so
            # -model is simply the other spelling of --model. Reading -m
            # as an attached short form here would take -mode, a real agy
            # flag, for a model override.
            if _matches_go_option(token, "--model"):
                raise _model_conflict(variable)
        elif _matches_option(token, "--model") or _matches_short_option(token, "-m"):
            raise _model_conflict(variable)

        if adapter == "claude" and _matches_option(token, "--permission-mode"):
            mode = _long_option_value(tokens, index, "--permission-mode")
            if mode in HEADLESS_IMPOSSIBLE_CLAUDE_MODES:
                raise _headless_mode_conflict(
                    variable, "--permission-mode", mode, USABLE_CLAUDE_MODES
                )

        # TOOLS already owns this setting, the way MODEL_* owns the model.
        # Reserving the flag is what keeps the argv and the archived
        # metadata from reporting two different allowlists.
        if adapter == "claude" and any(
            _matches_option(token, option)
            for option in {"--allowedTools", "--allowed-tools"}
        ):
            raise _tools_conflict(variable)

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

        # Sandbox flags are reserved because `codex exec resume` has no
        # --sandbox at all, so the workflow has to inject sandbox_mode
        # through -c. A user value would leave fresh and resumed calls
        # running under different sandboxes.
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

        if adapter == "agy":
            if any(
                _matches_go_option(token, option)
                for option in {
                    "--log-file",
                    "--continue",
                    "-c",
                    "--conversation",
                }
            ):
                raise SettingsError(
                    f"{variable} cannot contain session-control argument:{token}"
                )

            # --print carries the workflow prompt, and agy's parser lets a
            # later value replace an earlier one, so any second spelling of
            # it silently discards the prompt the workflow sent. The timeout
            # and the output contract are workflow-owned for the same reason.
            if _matches_go_option(token, "--mode"):
                mode = _go_option_value(tokens, index, "--mode")
                if mode == "plan":
                    raise _headless_mode_conflict(
                        variable, "--mode", mode, USABLE_AGY_MODES
                    )

            if any(
                _matches_go_option(token, option)
                for option in {
                    "--print",
                    "-p",
                    "--prompt",
                    "--prompt-interactive",
                    "-i",
                    "--print-timeout",
                    "--output-format",
                    "--json-schema",
                }
            ):
                raise SettingsError(
                    f"{variable} cannot contain workflow-owned argument:{token}"
                )

        # Only flags `opencode run` really has, and only ones the workflow
        # passes itself. The reason is the parser, not danger: yargs
        # collects a repeated option into an array instead of keeping the
        # last value, so a second --format or --auto reaches opencode as
        # ["json", "json"] rather than overriding anything. --command
        # belongs here too: it replaces the message with a stored command,
        # which would drop the workflow's own prompt.
        if adapter == "opencode" and (
            any(
                _matches_option(token, option)
                for option in {
                    "--format",
                    "--session",
                    "--continue",
                    "--fork",
                    "--attach",
                    "--auto",
                    "--share",
                    "--command",
                    "--dir",
                }
            )
            or _matches_short_option(token, "-s")
            or _matches_short_option(token, "-c")
        ):
            raise SettingsError(
                f"{variable} cannot contain workflow-owned argument:{token}"
            )


def _validate_slot_arg_source(variable: str, command: str, raw: str) -> None:
    tokens = _split_cli_args(variable, raw)
    if is_builtin_agent(command):
        _validate_builtin_arg_tokens(variable, command, tokens)


def _validate_reserved_args(settings: Settings) -> None:
    _validate_slot_arg_source("AGENT_A_ARGS", settings.agent_a, settings.agent_a_args)
    _validate_slot_arg_source("AGENT_B_ARGS", settings.agent_b, settings.agent_b_args)
    # Quoting belongs to the value, not to any adapter, so it is settled
    # before the question of who will own the slot is even asked.
    _split_cli_args("IMPL_ARGS", settings.impl_args)
    _validate_impl_args(settings, _impl_candidates(settings))


def _impl_rejection(settings: Settings, candidate: str) -> SettingsError | None:
    """The error an implementation slot running `candidate` would raise."""
    if not settings.impl_agent and not is_builtin_agent(candidate):
        return _custom_impl_conflict(candidate)
    if not is_builtin_agent(candidate):
        return None
    try:
        _validate_builtin_arg_tokens(
            "IMPL_ARGS",
            candidate,
            _split_cli_args("IMPL_ARGS", settings.impl_args),
        )
    except SettingsError as exc:
        return exc
    return None


def _validate_impl_args(settings: Settings, candidates: tuple[str, ...]) -> None:
    """Refuse an implementation slot that no owner choice could rescue.

    A rule belonging to one dual-spec candidate cannot be applied while
    the other is still live: the user may well be about to pick the one
    that accepts the argument. A violation every candidate shares is a
    different thing. No selection can make it valid, so deferring it only
    moves the abort past four paid stages and the human selection gate,
    and leaves the user editing settings.json to recover the run.
    """
    if not (settings.impl_agent or settings.impl_model or settings.impl_args):
        return
    rejections = [_impl_rejection(settings, name) for name in candidates]
    # Reported as the first candidate's error rather than a summary: the
    # remedy it names is the same one every candidate needs.
    if rejections and all(rejections):
        raise rejections[0]


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
    """One agent call's outcome.

    `quota_text` is the narrow channel quota detection reads: only the text
    an adapter can vouch for as coming from the agent itself, never from a
    command the agent ran. `quota_reset_epoch` is an exact reset time when
    the agent reported one, sparing the parser a wall-clock string. Both
    are excluded from equality because they are diagnostic plumbing, not
    part of what the call produced.
    """

    rc: int
    text: str
    quota_text: str = field(default="", compare=False)
    quota_reset_epoch: int | None = field(default=None, compare=False)


def _resolve_argv0(name: str) -> str:
    # Windows: claude/codex/agy/opencode install as .cmd shims; Popen needs
    # the resolved path (bash resolved via PATH natively).
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
    "filePath",
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


@dataclass
class ClaudeEvent:
    """What one claude NDJSON line contributes.

    `envelope` is set only by the final result event. `reset_epoch` comes
    from a rate_limit_event and is only ever a wait time -- that event
    fires on ordinary successful calls too, so it never means the call
    was limited.
    """

    echo: list[str] = field(default_factory=list)
    envelope: str = ""
    reset_epoch: int | None = None


def render_claude_event(line: str) -> ClaudeEvent:
    """Render one claude NDJSON line.

    The envelope is what the adapters parse for the session id, the cost,
    and the result. Keeping this pure is what makes the whole rendering
    layer testable without a subprocess, while the caller stays free to
    echo per line.
    """
    text = line.rstrip("\r\n")
    if not text.strip():
        return ClaudeEvent()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Merged stderr and CLI warnings are exactly what the user needs
        # to see when something goes wrong, so pass them straight through.
        return ClaudeEvent(echo=[text])
    if not isinstance(payload, dict):
        return ClaudeEvent(echo=[text])
    event_type = payload.get("type")
    if event_type == "result":
        return ClaudeEvent(envelope=text)
    if event_type == "rate_limit_event":
        info = payload.get("rate_limit_info")
        resets_at = info.get("resetsAt") if isinstance(info, dict) else None
        if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool):
            return ClaudeEvent(reset_epoch=int(resets_at))
        return ClaudeEvent()
    if event_type != "assistant":
        return ClaudeEvent()
    message = payload.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        content = [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return ClaudeEvent()
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
    return ClaudeEvent(echo=echoed)


def _run_claude_stream(
    argv: list[str], io: AgentIO, ref: AgentRef
) -> tuple[int, str, int | None]:
    """Stream claude NDJSON, returning (rc, envelope, reset epoch).

    The envelope is the same JSON object the old `--output-format json`
    call produced, so every caller downstream is unchanged. When no
    result event arrives -- a crash, a kill, a quota abort -- it falls
    back to the whole raw stream, so the quota channel still has the
    agent's own wording to work with.

    The reset epoch is whatever the last rate_limit_event reported. It is
    a wait time only; the decision that a call was limited comes from the
    envelope's api_error_status.
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
    reset_epoch: int | None = None
    assert proc.stdout is not None
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    with io.raw_out.open("w", encoding="utf-8") as raw_file:
        for raw_line in proc.stdout:
            raw_file.write(raw_line)
            raw.append(raw_line.rstrip("\r\n"))
            event = render_claude_event(raw_line)
            for text in event.echo:
                _echo_agent(io, ref, text)
            if event.envelope:
                envelope = event.envelope
            if event.reset_epoch is not None:
                reset_epoch = event.reset_epoch
    rc = proc.wait()
    return rc, envelope or "\n".join(raw), reset_epoch


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
    # The path is text the agent wrote, not a path on this host, so the
    # basename must be read the same way everywhere. PureWindowsPath
    # accepts both separators; PurePath would follow the host and leave a
    # windows path unsplit -- and the wrapper unstripped -- on posix.
    if PureWindowsPath(exe).name.lower() not in _SHELLS:
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


@dataclass
class CodexEvent:
    """What one codex NDJSON line contributes.

    `text` lands in agent_out, `echo` decides whether it also reaches the
    terminal, and `quota` marks the text as something quota detection may
    read -- true only for channels the agent speaks through itself, never
    for the output of a command it ran.
    """

    text: str = ""
    echo: bool = False
    thread_id: str = ""
    quota: bool = False


# A completed command carries its whole captured output. agent_out is the
# readable artifact, and raw_out already holds the event verbatim, so the
# bulk is summarised here rather than duplicated.
_CODEX_BULK_FIELDS = ("aggregated_output",)


def _elide_bulk(payload: dict[str, object]) -> dict[str, object]:
    """Replace an item's bulky fields with a note about their size."""
    item = payload.get("item")
    if not isinstance(item, dict):
        return payload
    elided = {}
    for name in _CODEX_BULK_FIELDS:
        value = item.get(name)
        if isinstance(value, str) and value:
            elided[name] = (
                f"({len(value)} characters elided; see the .cli.raw artifact)"
            )
    if not elided:
        return payload
    return {**payload, "item": {**item, **elided}}


def render_codex_event(line: str, cwd: Path | None = None) -> CodexEvent:
    """Render one codex NDJSON line.

    The text is what lands in agent_out; only some of it is worth putting
    on the terminal. Unknown event types still render as raw JSON so a
    codex upgrade stays diagnosable.
    """
    text = line.rstrip("\r\n")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Codex merges its stderr into stdout, so a non-JSON line is the
        # CLI speaking -- including how it reports an exhausted quota.
        return CodexEvent(text=text, echo=True, quota=True)
    if not isinstance(payload, dict):
        return CodexEvent(text=_jq_raw(payload))
    event_type = payload.get("type")
    if event_type == "thread.started":
        parsed_id = payload.get("thread_id")
        return CodexEvent(
            thread_id=parsed_id if isinstance(parsed_id, str) else ""
        )
    if event_type == "item.started":
        # The live heartbeat: this fires when the tool call starts, not
        # when it finishes, so a ten-minute command is visible up front.
        item = payload.get("item")
        if isinstance(item, dict):
            return CodexEvent(text=_codex_item_summary(item, cwd), echo=True)
        return CodexEvent(text=json.dumps(payload, ensure_ascii=False))
    if event_type == "item.completed":
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            body = item.get("text")
            rendered = _jq_raw(body) if body is not None else ""
            return CodexEvent(text=rendered, echo=bool(rendered))
        return CodexEvent(text=json.dumps(_elide_bulk(payload), ensure_ascii=False))
    if event_type == "error":
        value = payload.get("message")
        rendered = (
            _jq_raw(value)
            if value is not None
            else json.dumps(payload, ensure_ascii=False)
        )
        return CodexEvent(text=rendered, echo=True, quota=True)
    if event_type == "turn.failed":
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message") is not None:
            rendered = _jq_raw(error["message"])
        elif error is not None:
            rendered = _jq_raw(error)
        else:
            rendered = json.dumps(payload, ensure_ascii=False)
        return CodexEvent(text=rendered, echo=True, quota=True)
    return CodexEvent(text=json.dumps(payload, ensure_ascii=False))


def _run_codex_json(
    argv: list[str], io: AgentIO, ref: AgentRef
) -> tuple[int, str, str, str]:
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
    quota: list[str] = []
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
            event = render_codex_event(raw_line, cwd)
            if event.thread_id:
                thread_id = event.thread_id
            if not event.text:
                continue
            rendered.append(event.text)
            rendered_file.write(event.text.rstrip("\n") + "\n")
            if event.quota:
                quota.append(event.text)
            if event.echo:
                _echo_agent(io, ref, event.text)
    rc = proc.wait()
    return rc, "\n".join(rendered), thread_id, "\n".join(quota)


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
    # Known edge, left as it is: claude's --allowedTools is variadic, so it
    # keeps collecting until the next dashed token. The workflow passes it
    # right before these arguments, so when MODEL_* is empty and a slot's
    # first token is a bare word, that word joins the allowlist instead of
    # standing on its own. A bare word is not a claude argument in the
    # first place, so nothing valid is lost.
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
    rc, out, reset_epoch = _run_claude_stream(argv, io, ref)
    _write_agent_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return AgentResult(rc, out, quota_text=out, quota_reset_epoch=reset_epoch)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        # Lenient divergence: bash's jq failure degraded to empty fields.
        return AgentResult(0, out, quota_text=out, quota_reset_epoch=reset_epoch)
    if payload is None:
        session.worker_session = "null"
        session.last_cost = ""
        return AgentResult(0, "", quota_text=out, quota_reset_epoch=reset_epoch)
    if not isinstance(payload, dict):
        session.worker_session = ""
        session.last_cost = ""
        return AgentResult(5, "", quota_text=out, quota_reset_epoch=reset_epoch)
    session.worker_session = _jq_raw(payload.get("session_id"))
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return AgentResult(
        0, _jq_coalesce_empty(payload, "result"), quota_text=out, quota_reset_epoch=reset_epoch
    )


def _reviewer_claude(
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
        "--json-schema",
        VERDICT_SCHEMA,
    ]
    # Slot arguments come last here for the same reason they do in the
    # worker: whatever the reserved list still lets through is the user's
    # to decide, and claude reads the last value of a repeated option.
    argv += _claude_common_args(ref, settings)
    rc, out, reset_epoch = _run_claude_stream(argv, io, ref)
    _write_agent_out(io, out)
    if rc != 0:
        print(out, file=sys.stderr)
        print(
            f"(claude exited with code {rc}; raw output is shown above)",
            file=sys.stderr,
        )
        return AgentResult(rc, out, quota_text=out, quota_reset_epoch=reset_epoch)
    try:
        payload = json.loads(out)
    except json.JSONDecodeError:
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return AgentResult(5, "", quota_text=out, quota_reset_epoch=reset_epoch)
    if payload is None:
        io.verdict_path.write_text(json.dumps(VERDICT_FALLBACK), encoding="utf-8")
        session.last_cost = ""
        return AgentResult(0, "", quota_text=out, quota_reset_epoch=reset_epoch)
    if not isinstance(payload, dict):
        io.verdict_path.write_text("", encoding="utf-8")
        session.last_cost = ""
        return AgentResult(5, "", quota_text=out, quota_reset_epoch=reset_epoch)
    structured_output = payload.get("structured_output")
    verdict = (
        VERDICT_FALLBACK
        if structured_output is None or structured_output is False
        else structured_output
    )
    io.verdict_path.write_text(json.dumps(verdict), encoding="utf-8")
    session.last_cost = _jq_coalesce_empty(payload, "total_cost_usd")
    return AgentResult(
        0, _jq_coalesce_empty(payload, "result"), quota_text=out, quota_reset_epoch=reset_epoch
    )


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
    rc, out, thread_id, quota_text = _run_codex_json(argv, io, ref)
    if thread_id:
        session.worker_session = thread_id
    elif not session.worker_session:
        io.echo(
            "(warning: codex did not report a thread ID; the next worker call "
            "will start a fresh session)"
        )
    session.last_cost = ""
    return AgentResult(rc, out, quota_text=quota_text)


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
    rc, out, _, quota_text = _run_codex_json(argv, io, ref)
    session.last_cost = ""
    return AgentResult(rc, out, quota_text=quota_text)


def _opencode_model_args(ref: AgentRef, settings: Settings) -> list[str]:
    args: list[str] = []
    model = agent_model(ref, settings)
    if model:
        args += ["-m", model]
    args += agent_args(ref, settings)
    return args


@dataclass
class OpenCodeEvent:
    """What one opencode --format json line contributes."""

    echo: list[str] = field(default_factory=list)
    session_id: str = ""
    cost: float | None = None
    quota: str = ""


def _opencode_session_id(payload: dict[str, object]) -> str:
    value = payload.get("sessionID")
    return value if isinstance(value, str) else ""


def _opencode_part(payload: dict[str, object]) -> dict[str, object]:
    part = payload.get("part")
    return part if isinstance(part, dict) else {}


def _opencode_tool_detail(state: dict[str, object]) -> str:
    payload = state.get("input")
    if isinstance(payload, dict):
        for key in _TOOL_ARG_KEYS:
            value = payload.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                detail = str(value).split("\n", 1)[0].strip()
                if detail:
                    return detail
    # opencode titles a call with what it acted on, which is the best
    # remaining answer for a tool whose input this list does not name.
    title = state.get("title")
    if isinstance(title, str):
        return title.split("\n", 1)[0].strip()
    return ""


def _opencode_tool_summary(part: dict[str, object]) -> str:
    """One line naming an opencode tool call and the thing it acts on.

    opencode emits this only once the call has finished, so unlike the
    codex heartbeat the outcome is already known here and a failure is
    worth marking.
    """
    name = part.get("tool")
    label = name if isinstance(name, str) and name else "tool"
    raw_state = part.get("state")
    state = raw_state if isinstance(raw_state, dict) else {}
    detail = _opencode_tool_detail(state)
    if len(detail) > _TOOL_ARG_LIMIT:
        detail = detail[:_TOOL_ARG_LIMIT] + "..."
    summary = f" . {label} {detail}".rstrip()
    return f"{summary} (failed)" if state.get("status") == "error" else summary


def _opencode_error_status(error: dict[str, object]) -> str:
    """The HTTP status an opencode error reports, worded for the detector.

    opencode passes the provider's own error through, and a 429 does not
    always say "rate limit": Gemini says "Resource has been exhausted".
    The status is the one part of the payload every provider agrees on,
    so it travels with the message instead of being dropped.
    """
    data = error.get("data")
    if not isinstance(data, dict):
        return ""
    status = data.get("statusCode")
    if isinstance(status, bool) or not isinstance(status, (int, float)):
        return ""
    return f"status {int(status)}"


def _opencode_error_body(error: dict[str, object]) -> str:
    data = error.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str) and message.strip():
            return message
    message = error.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return json.dumps(error, ensure_ascii=False)


def _opencode_error_message(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return error
    if not isinstance(error, dict):
        return ""
    body = _opencode_error_body(error)
    status = _opencode_error_status(error)
    return f"{body} ({status})" if status else body


def render_opencode_event(line: str) -> OpenCodeEvent:
    """Render one opencode NDJSON line from `opencode run --format json`."""
    text = line.rstrip("\r\n")
    if not text.strip():
        return OpenCodeEvent()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # opencode merges its stderr into stdout and keeps its logs out of
        # this stream, so a non-JSON line is the CLI speaking -- including
        # how it reports a quota it could not even start the run with.
        return OpenCodeEvent(echo=[text], quota=text)
    if not isinstance(payload, dict):
        return OpenCodeEvent(echo=[text], quota=text)
    session_id = _opencode_session_id(payload)
    event_type = payload.get("type")
    if event_type == "text":
        body = _opencode_part(payload).get("text")
        if not isinstance(body, str):
            return OpenCodeEvent(session_id=session_id)
        return OpenCodeEvent(
            echo=[part for part in body.split("\n") if part.strip()],
            session_id=session_id,
        )
    if event_type == "tool_use":
        # opencode emits this when the call reaches a terminal state, not
        # when it starts, so a slow tool is silent until it returns. The
        # codex adapter reports the start instead; the difference is
        # documented in both READMEs.
        return OpenCodeEvent(
            echo=[_opencode_tool_summary(_opencode_part(payload))],
            session_id=session_id,
        )
    if event_type == "step_finish":
        cost = _opencode_part(payload).get("cost")
        if isinstance(cost, bool) or not isinstance(cost, (int, float)):
            return OpenCodeEvent(session_id=session_id)
        return OpenCodeEvent(session_id=session_id, cost=float(cost))
    if event_type == "error":
        message = _opencode_error_message(payload)
        return OpenCodeEvent(
            echo=[message] if message else [],
            session_id=session_id,
            quota=message,
        )
    return OpenCodeEvent(session_id=session_id)


def _format_opencode_cost(amounts: list[float]) -> str:
    """The run's total cost, or empty when the CLI reported none.

    A local model really costing nothing is not the same as a call that
    reported no cost at all, so a zero total still renders as "0" and
    only a missing report leaves the metrics column empty.
    """
    if not amounts:
        return ""
    return format(sum(amounts), ".10f").rstrip("0").rstrip(".") or "0"


def _run_opencode_json(
    argv: list[str], io: AgentIO, ref: AgentRef
) -> tuple[int, str, str, str, str]:
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
    quota: list[str] = []
    costs: list[float] = []
    session_id = ""
    assert proc.stdout is not None
    io.raw_out.parent.mkdir(parents=True, exist_ok=True)
    io.agent_out.parent.mkdir(parents=True, exist_ok=True)
    with (
        io.raw_out.open("w", encoding="utf-8") as raw_file,
        io.agent_out.open("w", encoding="utf-8") as rendered_file,
    ):
        for raw_line in proc.stdout:
            raw_file.write(raw_line)
            event = render_opencode_event(raw_line)
            if event.session_id:
                session_id = event.session_id
            if event.cost is not None:
                costs.append(event.cost)
            if event.quota:
                quota.append(event.quota)
            for text in event.echo:
                rendered.append(text)
                rendered_file.write(text + "\n")
                _echo_agent(io, ref, text)
    rc = proc.wait()
    return (
        rc,
        "\n".join(rendered),
        session_id,
        "\n".join(quota),
        _format_opencode_cost(costs),
    )


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
    return AgentResult(rc, out, quota_text=out)


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
    return AgentResult(rc, out, quota_text=out)


def _worker_opencode(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    argv = [
        _resolve_argv0("opencode"),
        "run",
        "--format",
        "json",
        "--auto",
        *_opencode_model_args(ref, settings),
    ]
    if session.worker_session:
        argv += ["--session", session.worker_session]
    argv.append(prompt)
    rc, out, session_id, quota_text, cost = _run_opencode_json(argv, io, ref)
    if session_id:
        session.worker_session = session_id
    elif not session.worker_session:
        io.echo(
            "(warning: opencode did not report a session ID; the next worker "
            "call will start a fresh session)"
        )
    session.last_cost = cost
    return AgentResult(rc, out, quota_text=quota_text)


def _reviewer_opencode(
    ref: AgentRef,
    prompt: str,
    settings: Settings,
    session: AgentSession,
    io: AgentIO,
) -> AgentResult:
    argv = [
        _resolve_argv0("opencode"),
        "run",
        "--format",
        "json",
        "--auto",
        *_opencode_model_args(ref, settings),
        prompt,
    ]
    rc, out, _, quota_text, cost = _run_opencode_json(argv, io, ref)
    session.last_cost = cost
    return AgentResult(rc, out, quota_text=quota_text)


def _run_generic(
    ref: AgentRef, prompt: str, settings: Settings, io: AgentIO
) -> AgentResult:
    argv = [
        _resolve_argv0(ref.name),
        *agent_args(ref, settings),
        prompt,
    ]
    rc, out = _run_streaming(argv, io, ref)
    return AgentResult(rc, out, quota_text=out)


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
    if ref.name == "opencode":
        return _worker_opencode(ref, prompt, settings, session, io)
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
    if ref.name == "opencode":
        return _reviewer_opencode(ref, prompt, settings, session, io)
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
