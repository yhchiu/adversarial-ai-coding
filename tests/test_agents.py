"""Ports tests/helpers.test.sh:63-123 (engine helpers and adapters)."""

import json
import sys
from pathlib import Path

import pytest

from adversarial_ai_coding import agents
from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.agents import (
    VERDICT_SCHEMA,
    AgentIO,
    AgentRef,
    AgentResult,
    AgentSession,
    agent_model as _agent_model,
    generic_agent_args as _generic_agent_args,
    is_builtin_agent,
    notify,
    resolve_model_args as _resolve_model_args,
    run_reviewer as _run_reviewer,
    run_worker as _run_worker,
    validate_agents,
)
from adversarial_ai_coding.ratelimit import is_rate_limited


def make(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def ref_for_name(name, settings):
    slot = "A" if name == settings.agent_a else "B"
    return AgentRef(slot, name)


def agent_model(name, settings):
    return _agent_model(ref_for_name(name, settings), settings)


def resolve_model_args(name, settings):
    return _resolve_model_args(ref_for_name(name, settings), settings)


def generic_agent_args(name, settings):
    return _generic_agent_args(ref_for_name(name, settings), settings)


def run_worker(name, prompt, settings, session, io):
    return _run_worker(ref_for_name(name, settings), prompt, settings, session, io)


def run_reviewer(name, prompt, settings, session, io):
    return _run_reviewer(ref_for_name(name, settings), prompt, settings, session, io)


def test_is_builtin_agent():
    assert is_builtin_agent("claude")
    assert is_builtin_agent("codex")
    assert is_builtin_agent("agy")
    assert is_builtin_agent("opencode")
    assert not is_builtin_agent("custom-agent")


def test_agent_ref_two_argument_construction_defaults_base_slot():
    ref = AgentRef("A", "claude")

    assert getattr(ref, "base_slot", None) == ""


def test_agent_session_two_argument_construction_preserves_last_cost():
    session = AgentSession("worker-id", "1.25")

    assert session.worker_session == "worker-id"
    assert session.last_cost == "1.25"
    assert session.owner is None


def test_impl_ref_with_no_impl_settings_returns_exact_owner_ref():
    owner = AgentRef("A", "claude")

    assert agents.impl_ref(owner, make()) is owner


@pytest.mark.parametrize(
    ("owner", "env", "expected"),
    [
        (
            AgentRef("A", "claude"),
            {"IMPL_MODEL": "sonnet"},
            AgentRef("I", "claude", base_slot="A"),
        ),
        (
            AgentRef("B", "codex"),
            {"IMPL_ARGS": "--search"},
            AgentRef("I", "codex", base_slot="B"),
        ),
        (
            AgentRef("A", "claude"),
            {"IMPL_AGENT": "codex"},
            AgentRef("I", "codex"),
        ),
        (
            AgentRef("A", "claude"),
            {"IMPL_AGENT": "claude"},
            AgentRef("I", "claude", base_slot="A"),
        ),
    ],
)
def test_impl_ref_resolves_name_and_model_base(owner, env, expected):
    assert agents.impl_ref(owner, make(env)) == expected


@pytest.mark.parametrize(
    ("owner", "env", "expected"),
    [
        (
            AgentRef("A", "claude"),
            {"MODEL_A": "opus", "IMPL_ARGS": "--search"},
            "opus",
        ),
        (
            AgentRef("B", "codex"),
            {
                "AGENT_B": "codex",
                "MODEL_B": "gpt-review",
                "IMPL_AGENT": "codex",
            },
            "gpt-review",
        ),
        (
            AgentRef("A", "claude"),
            {
                "MODEL_A": "opus",
                "IMPL_AGENT": "codex",
                "IMPL_MODEL": "gpt-impl",
            },
            "gpt-impl",
        ),
        (
            AgentRef("A", "claude"),
            {"MODEL_A": "opus", "IMPL_AGENT": "codex"},
            "",
        ),
    ],
)
def test_implementation_agent_model_is_explicit_or_safely_inherited(
    owner, env, expected
):
    settings = make(env)

    assert _agent_model(agents.impl_ref(owner, settings), settings) == expected


def test_implementation_agent_model_rejects_mismatched_manual_base_ref():
    settings = make({"AGENT_A": "claude", "MODEL_A": "opus"})
    malformed = AgentRef("I", "codex", base_slot="A")

    assert _agent_model(malformed, settings) == ""


def test_implementation_agent_model_rejects_unknown_manual_base_slot():
    settings = make({"AGENT_A": "claude", "MODEL_A": "opus"})
    malformed = AgentRef("I", "claude", base_slot="unknown")

    assert _agent_model(malformed, settings) == ""


def test_custom_implementation_agent_ignores_impl_model():
    settings = make({"IMPL_AGENT": "wrapper", "IMPL_MODEL": "ignored"})

    assert _agent_model(agents.impl_ref(AgentRef("A", "claude"), settings), settings) == ""


def test_agent_model_slot_a_uses_model_a():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex", "MODEL_A": "haiku", "MODEL_B": "mini"})
    assert agent_model("claude", s) == "haiku"
    assert agent_model("codex", s) == "mini"


def test_agent_model_uses_slot_when_both_slots_share_agent_name():
    s = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "MODEL_A": "gpt-a",
            "MODEL_B": "gpt-b",
        }
    )

    assert agents.agent_model(agents.AgentRef("A", "codex"), s) == "gpt-a"
    assert agents.agent_model(agents.AgentRef("B", "codex"), s) == "gpt-b"


def test_custom_agent_args_use_slot_when_names_match():
    s = make(
        {
            "AGENT_A": "wrapper",
            "AGENT_B": "wrapper",
            "AGENT_A_ARGS": "--profile a",
            "AGENT_B_ARGS": "--profile b",
        }
    )

    assert agents.resolve_model_args(agents.AgentRef("A", "wrapper"), s) == "--profile a"
    assert agents.resolve_model_args(agents.AgentRef("B", "wrapper"), s) == "--profile b"


@pytest.mark.parametrize(
    ("env", "ref", "expected_tokens", "expected_raw"),
    [
        (
            {
                "AGENT_A": "claude",
                "AGENT_A_ARGS": '--slot "a words"',
            },
            AgentRef("A", "claude"),
            ["--slot", "a words"],
            '--slot "a words"',
        ),
        (
            {
                "AGENT_B": "codex",
                "AGENT_B_ARGS": "-c model_reasoning_effort=high",
            },
            AgentRef("B", "codex"),
            ["-c", "model_reasoning_effort=high"],
            "-c model_reasoning_effort=high",
        ),
        (
            {
                "AGENT_A": "agy",
                "AGENT_A_ARGS": '--slot "a words"',
            },
            AgentRef("A", "agy"),
            ["--slot", "a words"],
            '--slot "a words"',
        ),
        (
            {
                "AGENT_B": "opencode",
                "AGENT_B_ARGS": '--title "slot words"',
            },
            AgentRef("B", "opencode"),
            ["--title", "slot words"],
            '--title "slot words"',
        ),
    ],
)
def test_builtin_slot_args_resolve_for_each_adapter(
    env, ref, expected_tokens, expected_raw
):
    settings = make(env)

    assert agents.agent_args(ref, settings) == expected_tokens
    assert agents.resolve_model_args(ref, settings) == expected_raw


def test_same_builtin_cli_keeps_a_and_b_slot_args_isolated():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "AGENT_A_ARGS": "-c model_reasoning_effort=low",
            "AGENT_B_ARGS": "-c model_reasoning_effort=high",
        }
    )
    ref_a = AgentRef("A", "codex")
    ref_b = AgentRef("B", "codex")

    assert agents.agent_args(ref_a, settings) == [
        "-c",
        "model_reasoning_effort=low",
    ]
    assert agents.agent_args(ref_b, settings) == [
        "-c",
        "model_reasoning_effort=high",
    ]
    assert "model_reasoning_effort=high" not in agents.agent_args(ref_a, settings)
    assert "model_reasoning_effort=low" not in agents.agent_args(ref_b, settings)


def test_independent_impl_slot_uses_only_impl_args():
    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_A_ARGS": '--slot "owner words"',
            "IMPL_ARGS": '--slot "impl words"',
        }
    )
    owner = AgentRef("A", "claude")
    implementation = agents.impl_ref(owner, settings)

    assert implementation.slot == "I"
    assert agents.agent_args(implementation, settings) == ["--slot", "impl words"]
    assert agents.resolve_model_args(implementation, settings) == '--slot "impl words"'
    assert agents.agent_args(owner, settings) == ["--slot", "owner words"]


def test_impl_model_only_does_not_inherit_owner_or_adapter_args():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_A_ARGS": "-c model_reasoning_effort=low",
            "IMPL_MODEL": "gpt-impl",
        }
    )
    implementation = agents.impl_ref(AgentRef("A", "codex"), settings)

    assert implementation == AgentRef("I", "codex", base_slot="A")
    assert agents.agent_model(implementation, settings) == "gpt-impl"
    assert agents.agent_args(implementation, settings) == []
    assert agents.resolve_model_args(implementation, settings) == ""


def test_empty_impl_settings_keep_owner_slot_arguments():
    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_A_ARGS": '--slot "owner words"',
        }
    )
    owner = AgentRef("A", "claude")

    assert agents.impl_ref(owner, settings) is owner
    assert agents.agent_args(owner, settings) == ["--slot", "owner words"]


@pytest.mark.parametrize(
    ("ref", "env", "expected_tokens", "expected_raw"),
    [
        (
            AgentRef("A", "claude"),
            {"AGENT_A_ARGS": '--append-system-prompt "claude words"'},
            ["--append-system-prompt", "claude words"],
            '--append-system-prompt "claude words"',
        ),
        (
            AgentRef("B", "codex"),
            {"AGENT_B_ARGS": "-c model_reasoning_effort=low"},
            ["-c", "model_reasoning_effort=low"],
            "-c model_reasoning_effort=low",
        ),
        (
            AgentRef("A", "agy"),
            {"AGENT_A_ARGS": '--append-system-prompt "agy words"'},
            ["--append-system-prompt", "agy words"],
            '--append-system-prompt "agy words"',
        ),
        (
            AgentRef("A", "opencode"),
            {"AGENT_A_ARGS": "--variant high"},
            ["--variant", "high"],
            "--variant high",
        ),
        (
            AgentRef("A", "worker-wrapper"),
            {
                "AGENT_A": "worker-wrapper",
                "AGENT_A_ARGS": '--profile "worker words"',
            },
            ["--profile", "worker words"],
            '--profile "worker words"',
        ),
        (
            AgentRef("B", "review-wrapper"),
            {
                "AGENT_B": "review-wrapper",
                "AGENT_B_ARGS": '--profile "review words"',
            },
            ["--profile", "review words"],
            '--profile "review words"',
        ),
        (
            AgentRef("I", "claude", base_slot="A"),
            {
                "AGENT_A_ARGS": '--slot "owner words"',
                "IMPL_ARGS": '--permission-mode "impl mode"',
            },
            ["--permission-mode", "impl mode"],
            '--permission-mode "impl mode"',
        ),
        (
            AgentRef("I", "impl-wrapper"),
            {
                "IMPL_AGENT": "impl-wrapper",
                "IMPL_ARGS": '--profile "impl words"',
            },
            ["--profile", "impl words"],
            '--profile "impl words"',
        ),
        (AgentRef("A", "claude"), {}, [], ""),
    ],
)
def test_slot_args_resolve_only_the_ref_slot(
    ref, env, expected_tokens, expected_raw
):
    settings = make(env)

    assert agents.agent_args(ref, settings) == expected_tokens
    assert agents.resolve_model_args(ref, settings) == expected_raw


def test_agent_model_unset_is_empty_for_cli_default():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    assert agent_model("claude", s) == ""


def test_agent_model_custom_agent_ignores_model_a():
    s = make({"AGENT_A": "custom-agent", "AGENT_B": "codex", "MODEL_A": "ignored",
              "AGENT_A_ARGS": "--model custom"})
    assert agent_model("custom-agent", s) == ""


def test_resolve_model_args_builtin_uses_cli_args():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex",
              "AGENT_A_ARGS": "--fast", "AGENT_B_ARGS": "-c model_reasoning_effort=low"})
    assert resolve_model_args("claude", s) == "--fast"
    assert resolve_model_args("codex", s) == "-c model_reasoning_effort=low"


def test_resolve_model_args_custom_agent_uses_slot_args():
    s = make({"AGENT_A": "custom-agent", "AGENT_B": "codex",
              "AGENT_A_ARGS": "--model custom --flag"})
    assert resolve_model_args("custom-agent", s) == "--model custom --flag"
    assert generic_agent_args("custom-agent", s) == "--model custom --flag"


def test_resolve_model_args_unknown_agent_is_empty():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    assert resolve_model_args("stranger", s) == ""
    assert generic_agent_args("stranger", s) == ""


def test_validate_agents_missing_command():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    with pytest.raises(SettingsError, match="Missing required command:claude"):
        validate_agents(s, which=lambda name: None)


def test_validate_agents_same_codex_agent_allowed():
    s = make({"AGENT_A": "codex", "AGENT_B": "codex"})
    validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_same_custom_agent_rejected():
    s = make({"AGENT_A": "wrapper", "AGENT_B": "wrapper"})
    with pytest.raises(SettingsError, match="custom agent command wrapper"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_both_claude_is_allowed():
    s = make({"AGENT_A": "claude", "AGENT_B": "claude"})
    validate_agents(s, which=lambda name: "C:/fake/" + name)  # must not raise


@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("AGENT_A_ARGS", {}),
        ("AGENT_B_ARGS", {}),
        ("AGENT_A_ARGS", {"AGENT_A": "agy"}),
        ("AGENT_A_ARGS", {"AGENT_A": "opencode"}),
        ("AGENT_A_ARGS", {"AGENT_A": "custom-a"}),
        ("AGENT_B_ARGS", {"AGENT_B": "custom-b"}),
        ("IMPL_ARGS", {}),
        ("IMPL_ARGS", {"IMPL_AGENT": "impl-wrapper"}),
    ],
)
def test_validate_agents_rejects_unclosed_quotes(key, agent_env):
    s = make({**agent_env, key: '--flag "unterminated'})

    with pytest.raises(SettingsError, match=rf"{key}.*quoting"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (
            "AGENT_B_ARGS",
            '--config developer_instructions="mention --sandbox safely"',
        ),
        ("AGENT_A_ARGS", '--append-system-prompt "mention --continue safely"'),
        ("AGENT_A_ARGS", '--title "mention --session safely"'),
    ],
)
def test_validate_agents_ignores_reserved_words_inside_quoted_values(key, value):
    s = make({key: value})

    validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "-c",
        "--continue",
        "--continue=true",
        "-r previous-session",
        "--resume previous-session",
        "--resume=previous-session",
        "--session-id session-id",
        "--session-id=session-id",
        "--fork-session",
        "--fork-session=true",
        "--no-session-persistence",
        "--no-session-persistence=true",
        "--from-pr 123",
        "--from-pr=123",
        "--output-format text",
        "--output-format=text",
        "--verbose",
        "--verbose=true",
        "--json-schema '{}'",
        "--json-schema={}",
    ],
)
def test_validate_agents_rejects_claude_workflow_owned_args(value):
    s = make({"AGENT_A_ARGS": value})

    with pytest.raises(SettingsError, match="AGENT_A_ARGS.*workflow-owned"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("AGENT_A_ARGS", {}),
        ("AGENT_B_ARGS", {}),
        ("AGENT_A_ARGS", {"AGENT_A": "agy"}),
        ("AGENT_A_ARGS", {"AGENT_A": "opencode"}),
    ],
)
@pytest.mark.parametrize("value", ["--model pro", "--model=pro", "-m pro", "-m=pro"])
def test_validate_agents_rejects_builtin_model_args(key, agent_env, value):
    s = make({**agent_env, key: value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(s, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        f"{key} cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


@pytest.mark.parametrize(
    "value",
    [
        "-c model=gpt-5",
        "-c=model=gpt-5",
        "--config model=gpt-5",
        "--config=model=gpt-5",
    ],
)
def test_validate_agents_rejects_codex_model_config(value):
    s = make({"AGENT_B_ARGS": value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(s, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        "AGENT_B_ARGS cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


@pytest.mark.parametrize(
    "value",
    [
        "-c model_reasoning_effort=low",
        "-c=model_reasoning_effort=low",
        "--config model_reasoning_effort=low",
        "--config=model_reasoning_effort=low",
    ],
)
def test_validate_agents_allows_non_model_codex_config(value):
    s = make({"AGENT_B_ARGS": value})

    validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "--json",
        "resume thread-id",
        "--sandbox workspace-write",
        "--sandbox=workspace-write",
        "-s workspace-write",
        "-s=workspace-write",
        "-c sandbox_mode=workspace-write",
        "--config sandbox_mode=workspace-write",
        "--config=sandbox_mode=workspace-write",
    ],
)
def test_validate_agents_rejects_codex_workflow_owned_args(value):
    s = make({"AGENT_B_ARGS": value})

    with pytest.raises(SettingsError):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-approvals-and-sandbox=true",
        "--yolo",
        "--yolo=true",
        "--ephemeral",
        "--ephemeral=true",
        "-sworkspace-write",
        "-cmodel=gpt-5",
        "-c model = gpt-5",
        "-csandbox_mode=workspace-write",
        "-c sandbox_mode = workspace-write",
    ],
)
def test_validate_agents_rejects_extended_codex_workflow_owned_args(value):
    settings = make({"AGENT_B_ARGS": value})

    with pytest.raises(SettingsError, match="AGENT_B_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("value", ["-mgpt-5", "-m=gpt-5"])
@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("AGENT_A_ARGS", {}),
        ("AGENT_B_ARGS", {}),
        ("AGENT_A_ARGS", {"AGENT_A": "agy"}),
        ("AGENT_A_ARGS", {"AGENT_A": "opencode"}),
    ],
)
def test_validate_agents_rejects_attached_builtin_model_args(
    key, agent_env, value
):
    settings = make({**agent_env, key: value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(settings, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        f"{key} cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


@pytest.mark.parametrize(
    "value",
    [
        "--yolo",
        "--ephemeral",
        "-sworkspace-write",
        "-cmodel=gpt-5",
        "-csandbox_mode=workspace-write",
        "-mgpt-5",
    ],
)
def test_validate_agents_rejects_extended_codex_impl_args(value):
    settings = make({"IMPL_AGENT": "codex", "IMPL_ARGS": value})

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "-cmodel_reasoning_effort=low",
        "-c model_reasoning_effort = low",
        "--config 'model_reasoning_effort = low'",
    ],
)
def test_validate_agents_allows_spaced_or_attached_reasoning_config(value):
    settings = make({"AGENT_B_ARGS": value})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_keeps_extended_codex_rules_adapter_specific():
    settings = make({"IMPL_AGENT": "claude", "IMPL_ARGS": "--yolo --ephemeral"})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_keeps_custom_impl_args_unmodified():
    settings = make(
        {
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_ARGS": "-mgpt-5 -snone -cmodel=x --yolo --ephemeral",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "--log-file output.log",
        "--log-file=output.log",
        "--continue",
        "--continue=true",
        # -c is agy's documented short alias for --continue.
        "-c",
        "-c=true",
        "--conversation conversation-id",
        "--conversation=conversation-id",
        # The Go flag package reads one or two dashes as the same flag.
        "-log-file output.log",
        "-continue",
        "-conversation conversation-id",
    ],
)
def test_validate_agents_rejects_agy_session_control_args(value):
    s = make({"AGENT_A": "agy", "AGENT_A_ARGS": value})

    with pytest.raises(SettingsError, match="session-control"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        # agy carries the prompt in --print, and its parser lets a later
        # value replace an earlier one, so each of these spellings would
        # discard the workflow prompt instead of adding to it.
        "--print hijacked",
        "--print=hijacked",
        "-print hijacked",
        "-p hijacked",
        "--prompt hijacked",
        "-prompt hijacked",
        "--prompt-interactive hijacked",
        "-i hijacked",
        "--print-timeout 1s",
        "--output-format json",
        "--json-schema schema.json",
    ],
)
def test_validate_agents_rejects_agy_prompt_and_output_args(value):
    s = make({"AGENT_A": "agy", "AGENT_A_ARGS": value})

    with pytest.raises(SettingsError, match="workflow-owned"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        # Reserved names must not swallow the agy flags that merely start
        # with the same letters, in either dash spelling.
        "--project project-id",
        "-project project-id",
        "--new-project",
        "--prompt-file request.md",
        "--agent reviewer",
        "--add-dir ../shared",
        "--mode plan",
        "--effort=low",
    ],
)
def test_validate_agents_allows_agy_args_that_only_share_a_prefix(value):
    s = make({"AGENT_A": "agy", "AGENT_A_ARGS": value})

    validate_agents(s, which=lambda name: "C:/fake/" + name)  # must not raise


@pytest.mark.parametrize(
    "value",
    [
        "--format json",
        "--format=json",
        "--session ses_abc",
        "--session=ses_abc",
        "-s ses_abc",
        "-s=ses_abc",
        "-sese_abc",
        "--continue",
        "--continue=true",
        "-c",
        "--fork",
        "--attach http://localhost:4096",
        "--attach=http://localhost:4096",
        "--auto",
        "--share",
        # --command runs a stored command instead of the workflow prompt.
        "--command review",
        "--command=review",
        "--dir /tmp",
    ],
)
def test_validate_agents_rejects_opencode_workflow_owned_args(value):
    s = make({"AGENT_A": "opencode", "AGENT_A_ARGS": value})

    with pytest.raises(SettingsError, match="AGENT_A_ARGS"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_allows_opencode_variant_and_quoted_session_mention():
    # Only flags `opencode run` really owns are reserved: --file adds context
    # to the message the workflow still writes, so it stays the user's.
    s = make(
        {
            "AGENT_A": "opencode",
            "AGENT_A_ARGS": '--variant high --agent build --thinking --file notes.md --title "mention --session safely"',
        }
    )

    validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "agent_env", "value"),
    [
        ("AGENT_A_ARGS", {}, "--json"),
        ("AGENT_B_ARGS", {}, "--continue"),
        ("AGENT_A_ARGS", {"AGENT_A": "agy"}, "--sandbox workspace-write"),
        ("AGENT_A_ARGS", {"AGENT_A": "opencode"}, "--json"),
    ],
)
def test_validate_agents_allows_flags_reserved_by_other_builtin_adapters(
    key, agent_env, value
):
    s = make({**agent_env, key: value})

    validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_allows_workflow_tokens_in_custom_agent_args():
    s = make(
        {
            "AGENT_A": "custom-a",
            "AGENT_B": "custom-b",
            "AGENT_A_ARGS": "--model pro resume --json",
            "AGENT_B_ARGS": "-m reviewer --conversation=id --output-format=json",
        }
    )

    validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("slot", ["A", "B"])
@pytest.mark.parametrize(
    ("adapter", "value"),
    [
        ("claude", "--continue"),
        ("claude", "--output-format=text"),
        ("claude", "--model pro"),
        ("codex", "resume thread-id"),
        ("codex", "--sandbox=workspace-write"),
        ("codex", "-c model=gpt-5"),
        ("agy", "--conversation=conversation-id"),
        ("agy", "--log-file output.log"),
        ("agy", "-m pro"),
        ("opencode", "--session=ses_abc"),
        ("opencode", "--auto"),
        ("opencode", "--model pro"),
    ],
)
def test_validate_agents_rejects_reserved_flags_in_builtin_slot_args(
    slot, adapter, value
):
    variable = f"AGENT_{slot}_ARGS"
    settings = make({f"AGENT_{slot}": adapter, variable: value})

    with pytest.raises(SettingsError, match=variable):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("slot", ["A", "B"])
@pytest.mark.parametrize(
    ("adapter", "value"),
    [
        ("claude", "--json"),
        ("codex", "--continue"),
        ("agy", "--sandbox workspace-write"),
        ("opencode", "--json"),
    ],
)
def test_validate_agents_allows_other_adapter_flags_in_builtin_slot_args(
    slot, adapter, value
):
    settings = make({f"AGENT_{slot}": adapter, f"AGENT_{slot}_ARGS": value})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("adapter", "value"),
    [
        ("claude", "--continue"),
        ("claude", "--output-format=text"),
        ("codex", "resume thread-id"),
        ("codex", "--sandbox=workspace-write"),
        ("codex", "-c sandbox_mode=workspace-write"),
        ("agy", "--conversation=conversation-id"),
        ("agy", "--log-file output.log"),
        ("opencode", "--session=ses_abc"),
        ("opencode", "--auto"),
        ("opencode", "--format=json"),
    ],
)
def test_validate_agents_applies_explicit_impl_adapter_reserved_rules(
    adapter, value
):
    settings = make({"IMPL_AGENT": adapter, "IMPL_ARGS": value})

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("adapter", ["claude", "codex", "agy", "opencode"])
@pytest.mark.parametrize("value", ["--model impl", "-m=impl"])
def test_validate_agents_rejects_impl_model_flags_for_builtin_adapters(
    adapter, value
):
    settings = make({"IMPL_AGENT": adapter, "IMPL_ARGS": value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(settings, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        "IMPL_ARGS cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


@pytest.mark.parametrize(
    ("adapter", "value"),
    [
        ("claude", "--sandbox=workspace-write"),
        ("codex", "--continue"),
        ("agy", "--json"),
        ("opencode", "--json"),
    ],
)
def test_validate_agents_does_not_union_impl_adapter_reserved_rules(
    adapter, value
):
    settings = make({"IMPL_AGENT": adapter, "IMPL_ARGS": value})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_allows_workflow_tokens_for_custom_impl_agent():
    settings = make(
        {
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_ARGS": "--model pro resume --json --conversation=id",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_checks_impl_args_against_default_owner_adapter():
    settings = make({"AGENT_A": "claude", "IMPL_ARGS": "--continue"})

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_defers_dual_spec_impl_args_when_adapters_differ():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--continue",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_still_quotes_impl_args_when_dual_spec_adapters_differ():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": '--flag "unterminated',
        }
    )

    with pytest.raises(SettingsError, match=r"IMPL_ARGS.*quoting"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_checks_dual_spec_impl_args_when_adapters_match():
    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_B": "claude",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--continue",
        }
    )

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_uses_only_agent_a_candidate_without_dual_spec():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "IMPL_ARGS": "--continue",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_impl_ref_revalidates_impl_args_for_resolved_runtime_adapter():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "IMPL_ARGS": "--continue",
        }
    )
    validate_agents(settings, which=lambda name: "C:/fake/" + name)

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        agents.impl_ref(AgentRef("B", "claude"), settings)


def test_impl_ref_revalidates_deferred_dual_spec_impl_args_against_owner():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--continue",
        }
    )
    validate_agents(settings, which=lambda name: "C:/fake/" + name)

    agents.impl_ref(AgentRef("A", "codex"), settings)
    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        agents.impl_ref(AgentRef("B", "claude"), settings)


def test_validate_agents_requires_explicit_impl_agent_on_path():
    settings = make({"IMPL_AGENT": "impl-wrapper"})

    def which(name):
        return None if name == "impl-wrapper" else "C:/fake/" + name

    with pytest.raises(SettingsError, match="Missing required command:impl-wrapper"):
        validate_agents(settings, which=which)


def test_validate_agents_allows_builtin_impl_command_shared_with_slot_b():
    settings = make(
        {"AGENT_A": "claude", "AGENT_B": "codex", "IMPL_AGENT": "codex"}
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("impl_agent", ["worker-wrapper", "review-wrapper"])
def test_validate_agents_rejects_impl_command_shared_with_custom_slot(
    impl_agent,
):
    settings = make(
        {
            "AGENT_A": "worker-wrapper",
            "AGENT_B": "review-wrapper",
            "IMPL_AGENT": impl_agent,
        }
    )

    with pytest.raises(
        SettingsError, match=rf"custom agent command {impl_agent}"
    ):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_requires_explicit_impl_wrapper_for_custom_owner():
    settings = make(
        {
            "AGENT_A": "worker-wrapper",
            "AGENT_B": "codex",
            "IMPL_ARGS": "--fast",
        }
    )

    with pytest.raises(SettingsError, match=r"custom agent command worker-wrapper"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_defers_mixed_dual_spec_custom_impl_conflict():
    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_B": "review-wrapper",
            "DUAL_SPEC": "1",
            "IMPL_MODEL": "impl-model",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_impl_ref_checks_mixed_dual_spec_custom_conflict_for_selected_owner():
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "review-wrapper",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--continue",
        }
    )
    validate_agents(settings, which=lambda name: "C:/fake/" + name)

    assert agents.impl_ref(AgentRef("A", "codex"), settings) == AgentRef(
        "I", "codex", base_slot="A"
    )
    with pytest.raises(SettingsError, match=r"custom agent command review-wrapper"):
        agents.impl_ref(AgentRef("B", "review-wrapper"), settings)


def test_validate_agents_keeps_zero_impl_custom_slots_valid():
    settings = make(
        {"AGENT_A": "worker-wrapper", "AGENT_B": "review-wrapper"}
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_allows_distinct_custom_impl_wrapper():
    settings = make(
        {
            "AGENT_A": "worker-wrapper",
            "AGENT_B": "review-wrapper",
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_ARGS": "--model custom-model resume --json",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def make_io(tmp_path, lines=None):
    sink = [] if lines is None else lines
    return AgentIO(
        agent_out=tmp_path / "agent-out.txt",
        raw_out=tmp_path / "agent-raw.txt",
        verdict_path=tmp_path / "verdict.json",
        echo=sink.append,
    ), sink


@pytest.mark.parametrize(
    ("builder", "ref", "expected"),
    [
        (
            agents._claude_common_args,
            AgentRef("A", "claude"),
            ["--model", "model-a", "--shared", "two words"],
        ),
        (
            agents._codex_model_args,
            AgentRef("B", "codex"),
            ["-c", 'model="model-b"', "--shared", "two words"],
        ),
        (
            agents._agy_model_args,
            AgentRef("B", "agy"),
            ["--model", "model-b", "--shared", "two words"],
        ),
        (
            agents._opencode_model_args,
            AgentRef("A", "opencode"),
            ["-m", "model-a", "--shared", "two words"],
        ),
    ],
)
def test_builtin_model_arg_builders_append_shared_agent_args(
    monkeypatch, builder, ref, expected
):
    monkeypatch.setattr(
        agents, "agent_args", lambda ref, settings: ["--shared", "two words"]
    )
    settings = make({"MODEL_A": "model-a", "MODEL_B": "model-b"})

    assert builder(ref, settings) == expected


def test_generic_runner_places_shared_agent_args_before_prompt(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        agents, "agent_args", lambda ref, settings: ["--shared", "two words"]
    )
    monkeypatch.setattr(agents, "_resolve_argv0", lambda name: name)
    monkeypatch.setattr(
        agents,
        "_run_streaming",
        lambda argv, io, ref: (seen.update(argv=argv), (0, "ok"))[1],
    )
    io, _ = make_io(tmp_path)

    agents._run_generic(AgentRef("A", "wrapper"), "prompt", make(), io)

    assert seen["argv"] == ["wrapper", "--shared", "two words", "prompt"]


def _assert_tokens_in_order(argv, tokens):
    index = 0
    for token in tokens:
        assert token in argv[index:], (token, argv)
        index = argv.index(token, index) + 1


def test_claude_a_and_b_keep_slot_args_in_fresh_and_resume_argv(monkeypatch, tmp_path):
    calls = []
    session_ids = iter(["claude-a-1", "claude-a-2", "claude-b-1", "claude-b-2"])

    def fake_run(argv, io, ref):
        calls.append((ref.slot, argv))
        return 0, json.dumps({"session_id": next(session_ids), "result": "ok"}), None

    monkeypatch.setattr(agents, "_run_claude_stream", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_B": "claude",
            "AGENT_A_ARGS": '--slot "a words"',
            "AGENT_B_ARGS": '--slot "b words"',
        }
    )
    io, _ = make_io(tmp_path)
    session_a = AgentSession()
    session_b = AgentSession()

    agents.run_worker(AgentRef("A", "claude"), "a-fresh", settings, session_a, io)
    agents.run_worker(AgentRef("A", "claude"), "a-resume", settings, session_a, io)
    agents.run_worker(AgentRef("B", "claude"), "b-fresh", settings, session_b, io)
    agents.run_worker(AgentRef("B", "claude"), "b-resume", settings, session_b, io)

    a_fresh, a_resume, b_fresh, b_resume = [argv for _, argv in calls]
    a_tokens = ["--slot", "a words"]
    b_tokens = ["--slot", "b words"]
    for argv in (a_fresh, a_resume):
        _assert_tokens_in_order(argv, a_tokens)
        assert "b words" not in argv
    for argv in (b_fresh, b_resume):
        _assert_tokens_in_order(argv, b_tokens)
        assert "a words" not in argv
    assert "--resume" not in a_fresh
    assert "--resume" not in b_fresh
    assert a_resume[-2:] == ["--resume", "claude-a-1"]
    assert b_resume[-2:] == ["--resume", "claude-b-1"]


def test_codex_a_and_b_keep_slot_args_in_fresh_and_resume_argv(monkeypatch, tmp_path):
    calls = []
    thread_ids = iter(["codex-a-1", "codex-a-1", "codex-b-1", "codex-b-1"])

    def fake_run(argv, io, ref):
        calls.append((ref.slot, argv))
        return 0, "ok", next(thread_ids), ""

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "AGENT_A_ARGS": '--slot "a words"',
            "AGENT_B_ARGS": '--slot "b words"',
        }
    )
    io, _ = make_io(tmp_path)
    session_a = AgentSession()
    session_b = AgentSession()

    agents.run_worker(AgentRef("A", "codex"), "a-fresh", settings, session_a, io)
    agents.run_worker(AgentRef("A", "codex"), "a-resume", settings, session_a, io)
    agents.run_worker(AgentRef("B", "codex"), "b-fresh", settings, session_b, io)
    agents.run_worker(AgentRef("B", "codex"), "b-resume", settings, session_b, io)

    a_fresh, a_resume, b_fresh, b_resume = [argv for _, argv in calls]
    a_tokens = ["--slot", "a words"]
    b_tokens = ["--slot", "b words"]
    for argv in (a_fresh, a_resume):
        _assert_tokens_in_order(argv, a_tokens)
        assert "b words" not in argv
    for argv in (b_fresh, b_resume):
        _assert_tokens_in_order(argv, b_tokens)
        assert "a words" not in argv
    assert "resume" not in a_fresh
    assert "resume" not in b_fresh
    assert a_resume[-2:] == ["codex-a-1", "a-resume"]
    assert b_resume[-2:] == ["codex-b-1", "b-resume"]


def test_agy_a_and_b_keep_slot_args_in_fresh_and_resume_argv(monkeypatch, tmp_path):
    calls = []
    conversation_ids = {
        "A": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "B": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    }

    def fake_run(argv, io, ref):
        calls.append((ref.slot, argv))
        log_path = Path(argv[argv.index("--log-file") + 1])
        log_path.write_text(
            f"Created conversation {conversation_ids[ref.slot]}\n", encoding="utf-8"
        )
        return 0, "ok"

    monkeypatch.setattr(agents, "_run_streaming", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "agy",
            "AGENT_B": "agy",
            "AGENT_A_ARGS": '--slot "a words"',
            "AGENT_B_ARGS": '--slot "b words"',
        }
    )
    io, _ = make_io(tmp_path)
    session_a = AgentSession()
    session_b = AgentSession()

    agents.run_worker(AgentRef("A", "agy"), "a-fresh", settings, session_a, io)
    agents.run_worker(AgentRef("A", "agy"), "a-resume", settings, session_a, io)
    agents.run_worker(AgentRef("B", "agy"), "b-fresh", settings, session_b, io)
    agents.run_worker(AgentRef("B", "agy"), "b-resume", settings, session_b, io)

    a_fresh, a_resume, b_fresh, b_resume = [argv for _, argv in calls]
    a_tokens = ["--slot", "a words"]
    b_tokens = ["--slot", "b words"]
    for argv in (a_fresh, a_resume):
        _assert_tokens_in_order(argv, a_tokens)
        assert "b words" not in argv
    for argv in (b_fresh, b_resume):
        _assert_tokens_in_order(argv, b_tokens)
        assert "a words" not in argv
    assert "--conversation" not in a_fresh
    assert "--conversation" not in b_fresh
    assert a_resume[-2:] == ["--conversation", conversation_ids["A"]]
    assert b_resume[-2:] == ["--conversation", conversation_ids["B"]]


def test_opencode_a_and_b_keep_slot_args_in_fresh_and_resume_argv(monkeypatch, tmp_path):
    calls = []
    session_ids = iter(["ses_a_1", "ses_a_1", "ses_b_1", "ses_b_1"])

    def fake_run(argv, io, ref):
        calls.append((ref.slot, argv))
        return 0, "ok", next(session_ids), "", "0.01"

    monkeypatch.setattr(agents, "_run_opencode_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "opencode",
            "AGENT_B": "opencode",
            "AGENT_A_ARGS": '--slot "a words"',
            "AGENT_B_ARGS": '--slot "b words"',
        }
    )
    io, _ = make_io(tmp_path)
    session_a = AgentSession()
    session_b = AgentSession()

    agents.run_worker(AgentRef("A", "opencode"), "a-fresh", settings, session_a, io)
    agents.run_worker(AgentRef("A", "opencode"), "a-resume", settings, session_a, io)
    agents.run_worker(AgentRef("B", "opencode"), "b-fresh", settings, session_b, io)
    agents.run_worker(AgentRef("B", "opencode"), "b-resume", settings, session_b, io)

    a_fresh, a_resume, b_fresh, b_resume = [argv for _, argv in calls]
    a_tokens = ["--slot", "a words"]
    b_tokens = ["--slot", "b words"]
    for argv in (a_fresh, a_resume):
        _assert_tokens_in_order(argv, a_tokens)
        assert "b words" not in argv
    for argv in (b_fresh, b_resume):
        _assert_tokens_in_order(argv, b_tokens)
        assert "a words" not in argv
    assert "--session" not in a_fresh
    assert "--session" not in b_fresh
    assert a_resume[-3:-1] == ["--session", "ses_a_1"]
    assert b_resume[-3:-1] == ["--session", "ses_b_1"]


def test_claude_implementation_worker_orders_fresh_and_resume_argv(
    monkeypatch, tmp_path
):
    calls = []
    session_ids = iter(["claude-session-1", "claude-session-2"])

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, json.dumps({"session_id": next(session_ids), "result": "ok"}), None

    monkeypatch.setattr(agents, "_run_claude_stream", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "claude",
            "MODEL_A": "owner-model",
            "AGENT_A_ARGS": '--owner-claude "owner words"',
            "IMPL_MODEL": "impl-model",
            "IMPL_ARGS": '--impl-claude "impl words"',
        }
    )
    ref = agents.impl_ref(AgentRef("A", "claude"), settings)
    io, _ = make_io(tmp_path)
    session = AgentSession()

    agents.run_worker(ref, "fresh prompt", settings, session, io)
    agents.run_worker(ref, "resume prompt", settings, session, io)

    expected_args = [
        "--model",
        "impl-model",
        "--impl-claude",
        "impl words",
    ]
    for argv in calls:
        model_index = argv.index("--model")
        assert argv[model_index : model_index + len(expected_args)] == expected_args
    assert "--resume" not in calls[0]
    assert calls[1][-2:] == ["--resume", "claude-session-1"]


def test_codex_implementation_worker_orders_fresh_and_resume_argv(
    monkeypatch, tmp_path
):
    calls = []
    thread_ids = iter(["codex-thread-1", "codex-thread-1"])

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, "ok", next(thread_ids), ""

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "codex",
            "MODEL_A": "owner-model",
            "AGENT_A_ARGS": '--owner-codex "owner words"',
            "IMPL_MODEL": "impl-model",
            "IMPL_ARGS": '--impl-codex "impl words"',
        }
    )
    ref = agents.impl_ref(AgentRef("A", "codex"), settings)
    io, _ = make_io(tmp_path)
    session = AgentSession()

    agents.run_worker(ref, "fresh prompt", settings, session, io)
    agents.run_worker(ref, "resume prompt", settings, session, io)

    model_and_args = [
        "-c",
        'model="impl-model"',
        "--impl-codex",
        "impl words",
    ]
    assert calls[0] == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "workspace-write",
        *model_and_args,
        "fresh prompt",
    ]
    assert calls[1] == [
        "codex",
        "exec",
        "resume",
        "--json",
        "-c",
        'sandbox_mode="workspace-write"',
        *model_and_args,
        "codex-thread-1",
        "resume prompt",
    ]


def test_agy_implementation_worker_orders_fresh_and_resume_argv(
    monkeypatch, tmp_path
):
    calls = []
    conversation_id = "66666666-6666-4666-8666-666666666666"

    def fake_run(argv, io, ref):
        calls.append(argv)
        log_path = Path(argv[argv.index("--log-file") + 1])
        log_path.write_text(
            f"Created conversation {conversation_id}\n", encoding="utf-8"
        )
        return 0, "ok"

    monkeypatch.setattr(agents, "_run_streaming", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "agy",
            "MODEL_A": "owner-model",
            "AGENT_A_ARGS": '--owner-agy "owner words"',
            "IMPL_MODEL": "impl-model",
            "IMPL_ARGS": '--impl-agy "impl words"',
        }
    )
    ref = agents.impl_ref(AgentRef("A", "agy"), settings)
    io, _ = make_io(tmp_path)
    session = AgentSession()

    agents.run_worker(ref, "fresh prompt", settings, session, io)
    agents.run_worker(ref, "resume prompt", settings, session, io)

    expected_args = [
        "--model",
        "impl-model",
        "--impl-agy",
        "impl words",
    ]
    for argv in calls:
        model_index = argv.index("--model")
        assert argv[model_index : model_index + len(expected_args)] == expected_args
    assert "--conversation" not in calls[0]
    assert calls[1][-2:] == ["--conversation", conversation_id]


def test_opencode_implementation_worker_orders_fresh_and_resume_argv(
    monkeypatch, tmp_path
):
    calls = []
    session_ids = iter(["ses_worker_1", "ses_worker_1"])

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, "ok", next(session_ids), "", "0.01"

    monkeypatch.setattr(agents, "_run_opencode_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "opencode",
            "MODEL_A": "owner-model",
            "AGENT_A_ARGS": '--title "owner words"',
            "IMPL_MODEL": "xai/grok-4.6",
            "IMPL_ARGS": '--agent build --title "impl words"',
        }
    )
    ref = agents.impl_ref(AgentRef("A", "opencode"), settings)
    io, _ = make_io(tmp_path)
    session = AgentSession()

    agents.run_worker(ref, "fresh prompt", settings, session, io)
    agents.run_worker(ref, "resume prompt", settings, session, io)

    model_and_args = [
        "-m",
        "xai/grok-4.6",
        "--agent",
        "build",
        "--title",
        "impl words",
    ]
    assert calls[0] == [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        *model_and_args,
        "fresh prompt",
    ]
    assert calls[1] == [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        *model_and_args,
        "--session",
        "ses_worker_1",
        "resume prompt",
    ]


def test_custom_implementation_worker_uses_impl_args_exactly_once(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        agents,
        "_run_streaming",
        lambda argv, io, ref: (calls.append(argv), (0, "ok"))[1],
    )
    settings = make(
        {
            "AGENT_A": "owner-wrapper",
            "AGENT_A_ARGS": "--owner-only",
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_ARGS": '--profile "impl words" --last',
        }
    )
    ref = agents.impl_ref(AgentRef("A", "owner-wrapper"), settings)
    io, _ = make_io(tmp_path)

    agents.run_worker(ref, "prompt", settings, AgentSession(), io)

    assert calls == [
        ["impl-wrapper", "--profile", "impl words", "--last", "prompt"]
    ]
    assert agents.resolve_model_args(ref, settings) == '--profile "impl words" --last'


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
        "print('custom agent ran')\n",
        encoding="utf-8",
    )
    s = Settings.from_env(
        {
            "AGENT_A": sys.executable,
            "AGENT_A_ARGS": f"'{fake}' --flag \"two words\"",
            "AGENT_B": "codex",
        },
        run_id="r",
    )
    io, sink = make_io(tmp_path)
    session = AgentSession()
    result = run_worker(sys.executable, "hello prompt", s, session, io)
    assert result.rc == 0
    captured = capture.read_text(encoding="utf-8")
    # The interpreter consumes fake.py as sys.argv[0]. The custom agent sees
    # the POSIX-quoted slot args followed by the prompt as the final arg.
    assert "argc=3" in captured
    assert "arg1=--flag" in captured
    assert "arg2=two words" in captured
    assert "arg3=hello prompt" in captured
    assert io.agent_out.read_text(encoding="utf-8").strip() == "custom agent ran"
    # A custom agent configured as a full path is prefixed by its file name.
    prefix = f"[A {Path(sys.executable).name}] "
    assert sink and sink[-1].strip() == prefix + "custom agent ran"


def test_generic_worker_resolves_argv0_with_shutil_which(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(agents.shutil, "which", lambda name: "C:/resolved/fake.cmd")
    monkeypatch.setattr(
        agents,
        "_run_streaming",
        lambda argv, io, ref: (calls.append(argv), (0, "ok"))[1],
    )
    settings = Settings.from_env(
        {"AGENT_A": "fake", "AGENT_B": "codex"}, run_id="r"
    )
    io, _ = make_io(tmp_path)
    run_worker("fake", "prompt", settings, AgentSession(), io)
    assert calls[0][0] == "C:/resolved/fake.cmd"


def test_non_codex_worker_removes_stale_cli_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_streaming", lambda argv, io, ref: (0, "ok"))
    s = Settings.from_env(
        {"AGENT_A": "custom-agent", "AGENT_B": "codex"}, run_id="r"
    )
    io, _ = make_io(tmp_path)
    io.raw_out.write_text("stale codex jsonl\n", encoding="utf-8")

    run_worker("custom-agent", "prompt", s, AgentSession(), io)

    assert not io.raw_out.exists()


def test_streaming_prefixes_the_echo_but_not_the_file(tmp_path):
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "print('### Summary')\nprint('second line')\n", encoding="utf-8"
    )
    io, echoed = make_io(tmp_path)

    rc, out = agents._run_streaming(
        [sys.executable, str(emitter)], io, AgentRef("B", "agy")
    )

    assert rc == 0
    assert echoed == ["[B agy] ### Summary", "[B agy] second line"]
    assert out == "### Summary\nsecond line"
    assert io.agent_out.read_text(encoding="utf-8") == "### Summary\nsecond line\n"


def test_agent_prefix_uses_slot_and_adapter_name():
    assert agents.agent_prefix(AgentRef("A", "claude")) == "[A claude] "
    assert agents.agent_prefix(AgentRef("I", "custom-impl")) == "[I custom-impl] "
    assert (
        agents.agent_prefix(AgentRef("B", "C:/tools/my wrapper.cmd"))
        == "[B my wrapper.cmd] "
    )


CLAUDE_ASSISTANT = {
    "type": "assistant",
    "message": {
        "content": [
            {"type": "text", "text": "Reading the runner first.\n\nThen editing."},
            {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "src/adversarial_ai_coding/agents.py"},
            },
        ]
    },
}


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            CLAUDE_ASSISTANT,
            [
                "Reading the runner first.",
                "Then editing.",
                " . Read src/adversarial_ai_coding/agents.py",
            ],
        ),
        ({"type": "system", "subtype": "init", "session_id": "s"}, []),
        ({"type": "user", "message": {"content": []}}, []),
        ({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}, []),
        ({"type": "assistant", "message": {"content": "bare string body"}},
         ["bare string body"]),
        ({"type": "assistant", "message": {"content": [{"type": "thinking"}]}}, []),
        ({"type": "assistant", "message": "malformed"}, []),
    ],
)
def test_render_claude_event_echo_lines(event, expected):
    rendered = agents.render_claude_event(json.dumps(event))

    assert rendered.echo == expected
    assert rendered.envelope == ""
    assert rendered.reset_epoch is None


def test_render_claude_event_returns_the_result_envelope():
    line = json.dumps({"type": "result", "session_id": "s1", "result": "done"})

    rendered = agents.render_claude_event(line)

    assert rendered.echo == []
    assert json.loads(rendered.envelope) == json.loads(line)


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        ({"status": "allowed", "resetsAt": 1785355800}, 1785355800),
        ({"resetsAt": 1785355800.0}, 1785355800),
        ({"status": "allowed"}, None),
        ({"resetsAt": None}, None),
        ({"resetsAt": True}, None),
        ({"resetsAt": "soon"}, None),
        ("not an object", None),
    ],
)
def test_render_claude_event_reads_the_reported_reset_time(info, expected):
    # The event fires on ordinary successful calls too, so it only ever
    # supplies a wait; it never says the call was limited.
    line = json.dumps({"type": "rate_limit_event", "rate_limit_info": info})

    rendered = agents.render_claude_event(line)

    assert rendered.reset_epoch == expected
    assert rendered.echo == []
    assert rendered.envelope == ""


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Error: something broke", ["Error: something broke"]),
        ('"a bare json string"', ['"a bare json string"']),
        ("   ", []),
        ("", []),
    ],
)
def test_render_claude_event_passes_through_non_object_lines(line, expected):
    assert agents.render_claude_event(line) == agents.ClaudeEvent(echo=expected)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"file_path": "src/a.py"}, " . Edit src/a.py"),
        ({"command": "pytest -q\nsecond line"}, " . Edit pytest -q"),
        ({"pattern": "_run_streaming", "path": "src"}, " . Edit _run_streaming"),
        ({"url": "https://example.com"}, " . Edit https://example.com"),
        ({}, " . Edit"),
        ({"unknown_field": "ignored"}, " . Edit"),
        ({"file_path": ""}, " . Edit"),
        ({"file_path": True}, " . Edit"),
        ({"content": "x" * 500}, " . Edit"),
        ({"command": "x" * 150}, " . Edit " + "x" * 100 + "..."),
    ],
)
def test_render_claude_event_tool_summary_picks_one_identifying_argument(
    payload, expected
):
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "Edit", "input": payload}]
        },
    }

    assert agents.render_claude_event(json.dumps(event)).echo == [expected]


def _claude_emitter(tmp_path, lines):
    script = tmp_path / "emit_claude.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write(sys.argv[1])\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script), "".join(f"{line}\n" for line in lines)]


def test_claude_stream_writes_raw_and_returns_the_result_envelope(tmp_path):
    result = json.dumps({"type": "result", "session_id": "s1", "result": "done"})
    limit = json.dumps(
        {"type": "rate_limit_event", "rate_limit_info": {"resetsAt": 1785355800}}
    )
    lines = [json.dumps(CLAUDE_ASSISTANT), limit, result]
    io, echoed = make_io(tmp_path)

    rc, envelope, reset_epoch = agents._run_claude_stream(
        _claude_emitter(tmp_path, lines), io, AgentRef("A", "claude")
    )

    assert rc == 0
    assert json.loads(envelope) == json.loads(result)
    assert reset_epoch == 1785355800
    assert io.raw_out.read_text(encoding="utf-8") == "".join(f"{x}\n" for x in lines)
    assert echoed == [
        "[A claude] Reading the runner first.",
        "[A claude] Then editing.",
        "[A claude]  . Read src/adversarial_ai_coding/agents.py",
    ]


def test_claude_stream_without_result_event_falls_back_to_the_raw_stream(tmp_path):
    # A quota abort leaves no result event. ratelimit.py reads only
    # agent_out, so the raw text has to survive or retries stop working.
    lines = ['{"type":"system","subtype":"init"}', "You've hit your usage limit."]
    io, _ = make_io(tmp_path)

    rc, envelope, reset_epoch = agents._run_claude_stream(
        _claude_emitter(tmp_path, lines), io, AgentRef("A", "claude")
    )

    assert rc == 0
    assert envelope == "\n".join(lines)
    assert reset_epoch is None


def test_claude_worker_quota_stream_reaches_the_quota_channel(monkeypatch, tmp_path):
    # A quota abort leaves no result event, so the envelope is the raw
    # stream. That text has to arrive as quota_text or retries stop working.
    from adversarial_ai_coding.ratelimit import is_rate_limited

    fallback = (
        '{"type":"system","subtype":"init"}\n'
        "You've hit your usage limit. Please try again in 20s."
    )
    monkeypatch.setattr(
        agents, "_run_claude_stream", lambda argv, io, ref: (2, fallback, None)
    )
    io, _ = make_io(tmp_path)

    result = run_worker(
        "claude", "p", Settings.from_env({}, run_id="r"), AgentSession(), io
    )

    assert result.rc == 2
    assert is_rate_limited(result.quota_text)


@pytest.mark.parametrize("role", ["worker", "reviewer"])
def test_claude_adapters_pass_the_reported_reset_time_through(
    monkeypatch, tmp_path, role
):
    envelope = json.dumps({"api_error_status": 429, "result": ""})
    monkeypatch.setattr(
        agents,
        "_run_claude_stream",
        lambda argv, io, ref: (1, envelope, 1785355800),
    )
    io, _ = make_io(tmp_path)
    call = run_worker if role == "worker" else run_reviewer

    result = call("claude", "p", Settings.from_env({}, run_id="r"), AgentSession(), io)

    assert result.quota_reset_epoch == 1785355800
    assert result.quota_text == envelope


CODEX_TOOL_FIXTURE = Path(__file__).parent / "fixtures" / "codex_exec_tool_activity.jsonl"

POWERSHELL = (
    '"C:\\\\WINDOWS\\\\System32\\\\WindowsPowerShell\\\\v1.0\\\\powershell.exe"'
)


def _codex_started(item):
    return json.dumps({"type": "item.started", "item": item})


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (f"{POWERSHELL} -Command 'git add -A'", " . run git add -A"),
        ('/bin/bash -c "pytest -q"', " . run pytest -q"),
        ("cmd.exe /c dir", " . run dir"),
        # Not a shell: stripping here would maul the command.
        ("git -c user.name=x commit -m hi", " . run git -c user.name=x commit -m hi"),
        ("pytest -q", " . run pytest -q"),
        (f"{POWERSHELL} -Command 'x{'y' * 200}'", " . run x" + "y" * 99 + "..."),
    ],
)
def test_render_codex_event_command_summary_strips_the_shell_wrapper(
    command, expected
):
    line = _codex_started({"id": "i", "type": "command_execution", "command": command})

    event = agents.render_codex_event(line)

    assert event == agents.CodexEvent(text=expected, echo=True)


def test_render_codex_event_file_change_shows_kind_and_relative_path(tmp_path):
    changed = tmp_path / "src" / "demo.py"
    line = _codex_started(
        {
            "id": "i",
            "type": "file_change",
            "changes": [
                {"path": str(changed), "kind": "add"},
                {"path": str(tmp_path / "src" / "other.py"), "kind": "update"},
            ],
        }
    )

    event = agents.render_codex_event(line, tmp_path)

    assert event.text == f" . edit {Path('src') / 'demo.py'} (add) +1 more"
    assert event.echo is True
    # A tool call is never a quota signal, whatever it printed.
    assert event.quota is False


def test_render_codex_event_keeps_paths_outside_the_workspace_absolute(tmp_path):
    outside = tmp_path.parent / "elsewhere.py"
    line = _codex_started(
        {"id": "i", "type": "file_change", "changes": [{"path": str(outside)}]}
    )

    assert agents.render_codex_event(line, tmp_path).text == f" . edit {outside}"


def test_render_codex_event_names_unknown_item_types(tmp_path):
    line = _codex_started({"id": "i", "type": "web_search", "query": "anything"})

    assert agents.render_codex_event(line, tmp_path) == agents.CodexEvent(
        text=" . web_search", echo=True
    )


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            '{"type":"thread.started","thread_id":"t-1"}',
            agents.CodexEvent(thread_id="t-1"),
        ),
        (
            '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}',
            agents.CodexEvent(text="hi", echo=True),
        ),
        # The three channels codex speaks through itself, and the only ones
        # quota detection is allowed to read.
        (
            '{"type":"error","message":"boom"}',
            agents.CodexEvent(text="boom", echo=True, quota=True),
        ),
        (
            '{"type":"turn.failed","error":{"message":"detail"}}',
            agents.CodexEvent(text="detail", echo=True, quota=True),
        ),
        (
            "not json at all",
            agents.CodexEvent(text="not json at all", echo=True, quota=True),
        ),
    ],
)
def test_render_codex_event_keeps_the_existing_event_handling(line, expected):
    assert agents.render_codex_event(line) == expected


def test_render_codex_event_elides_captured_command_output(tmp_path):
    output = "x" * 2483
    line = json.dumps(
        {
            "type": "item.completed",
            "item": {
                "id": "i",
                "type": "command_execution",
                "command": "pytest -q",
                "aggregated_output": output,
                "exit_code": 1,
                "status": "completed",
            },
        }
    )

    recorded = json.loads(agents.render_codex_event(line).text)

    # What the call was and how it ended survives; the captured output does not.
    item = recorded["item"]
    assert item["command"] == "pytest -q"
    assert (item["exit_code"], item["status"]) == (1, "completed")
    assert item["aggregated_output"] == (
        "(2483 characters elided; see the .cli.raw artifact)"
    )
    assert output not in agents.render_codex_event(line).text


@pytest.mark.parametrize(
    "item",
    [
        {"type": "command_execution", "aggregated_output": "", "exit_code": 0},
        {"type": "file_change", "changes": [{"path": "a.py", "kind": "add"}]},
        {"type": "command_execution", "aggregated_output": None},
    ],
    ids=["empty-output", "no-such-field", "null-output"],
)
def test_render_codex_event_leaves_payloads_without_bulk_untouched(item):
    line = json.dumps({"type": "item.completed", "item": item})

    assert json.loads(agents.render_codex_event(line).text) == json.loads(line)


@pytest.mark.parametrize(
    "line",
    [
        '{"type":"turn.completed","usage":{"input_tokens":1}}',
        '{"type":"item.completed","item":{"type":"command_execution","exit_code":0}}',
        '{"type":"future.event","details":{"message":"kept for diagnosis"}}',
    ],
)
def test_render_codex_event_records_other_events_without_echoing(line):
    event = agents.render_codex_event(line)

    assert json.loads(event.text) == json.loads(line)
    assert event.echo is False
    assert event.quota is False


def test_codex_stream_echoes_tool_activity_and_writes_summaries(tmp_path):
    emitter = tmp_path / "emit_codex.py"
    emitter.write_text(
        "import pathlib, sys\n"
        "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    io, echoed = make_io(tmp_path)

    rc, rendered, thread_id, quota_text = agents._run_codex_json(
        [sys.executable, str(emitter), str(CODEX_TOOL_FIXTURE)],
        io,
        AgentRef("B", "codex"),
    )

    assert rc == 0
    assert thread_id == "22222222-2222-4222-8222-222222222222"
    # The fixture's commands ran clean, so nothing feeds quota detection.
    assert quota_text == ""
    assert echoed == [
        "[B codex] Creating the file now.",
        "[B codex]  . edit src/demo.py (add) +1 more",
        "[B codex]  . run git add -A",
        "[B codex]  . web_search",
        "[B codex] DONE",
    ]
    # The summary replaces what used to be a raw JSON dump of item.started.
    agent_out = io.agent_out.read_text(encoding="utf-8")
    assert " . run git add -A" in agent_out
    assert "in_progress" not in agent_out
    assert "[B codex]" not in agent_out
    # item.completed still records its payload, minus the captured output.
    assert '"exit_code": 0' in rendered
    assert "nothing to commit, working tree clean" not in rendered
    assert "characters elided" in rendered
    # raw_out keeps the event verbatim, elision included nowhere.
    raw = io.raw_out.read_text(encoding="utf-8")
    assert raw == CODEX_TOOL_FIXTURE.read_text(encoding="utf-8")
    assert "nothing to commit, working tree clean" in raw


# The reason the quota channel was narrowed. This repo's own test file name
# matches the rate-limit regex, so a codex run that executes pytest used to
# look quota limited whenever it also exited non-zero, costing hours of
# pointless backoff.
PYTEST_OUTPUT_LOOKS_LIKE_A_RATE_LIMIT = (
    "tests/test_ratelimit_parsing.py .......... [100%]\n"
    "src/adversarial_ai_coding/ratelimit.py:26: _RATE_LIMIT = re.compile("
)


def test_command_output_that_matches_the_regex_is_not_a_quota_signal(tmp_path):
    from adversarial_ai_coding.ratelimit import is_rate_limited

    lines = [
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "i1",
                    "type": "command_execution",
                    "command": "pytest -q",
                    "aggregated_output": PYTEST_OUTPUT_LOOKS_LIKE_A_RATE_LIMIT,
                    "exit_code": 1,
                    "status": "completed",
                },
            }
        )
    ]
    io, _ = make_io(tmp_path)

    _, _, _, quota_text = agents._run_codex_json(
        _claude_emitter(tmp_path, lines), io, AgentRef("B", "codex")
    )

    assert quota_text == ""
    assert not is_rate_limited(quota_text)
    # Proof the sample really does trip the regex, so the test cannot pass
    # just because the wording drifted.
    assert is_rate_limited(PYTEST_OUTPUT_LOOKS_LIKE_A_RATE_LIMIT)
    # And the output itself is still recoverable from the raw artifact.
    recorded = json.loads(io.raw_out.read_text(encoding="utf-8"))
    assert (
        recorded["item"]["aggregated_output"]
        == PYTEST_OUTPUT_LOOKS_LIKE_A_RATE_LIMIT
    )


def test_real_quota_error_still_reaches_the_quota_channel(tmp_path):
    from adversarial_ai_coding.ratelimit import is_rate_limited, parse_reset_wait

    lines = [
        json.dumps(
            {
                "type": "error",
                "message": "You've hit your usage limit. Please try again in 20s.",
            }
        )
    ]
    io, _ = make_io(tmp_path)

    _, _, _, quota_text = agents._run_codex_json(
        _claude_emitter(tmp_path, lines), io, AgentRef("B", "codex")
    )

    assert is_rate_limited(quota_text)
    assert parse_reset_wait(quota_text, now=0) == 20 + 30


def test_claude_worker_parses_json_and_tracks_session(monkeypatch, tmp_path):
    payload = json.dumps(
        {"session_id": "sess-1", "total_cost_usd": 0.42, "result": "did the work"}
    )
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload, None))
    s = Settings.from_env({"TOOLS": "Bash(git *)"}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession()
    result = run_worker("claude", "prompt text", s, session, io)
    assert result.rc == 0
    assert result.text == "did the work"
    assert session.worker_session == "sess-1"
    assert session.last_cost == "0.42"
    assert json.loads(io.agent_out.read_text(encoding="utf-8")) == json.loads(payload)


def test_claude_worker_resumes_session_and_builds_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, io, ref):
        seen["argv"] = argv
        return (0, json.dumps({"session_id": "s2", "result": "ok"}), None)

    monkeypatch.setattr(agents, "_run_claude_stream", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_A": "haiku",
            "AGENT_A_ARGS": '--append-system-prompt "two words"',
            "TOOLS": "Bash(git *)",
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession(
        worker_session="prev-session", owner=AgentRef("A", "claude")
    )
    run_worker("claude", "the prompt", s, session, io)
    argv = seen["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == "the prompt"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Bash(git *)"
    assert argv[argv.index("--model") + 1] == "haiku"
    prompt_index = argv.index("--append-system-prompt")
    assert argv[prompt_index + 1] == "two words"
    assert argv[argv.index("--resume") + 1] == "prev-session"


def test_claude_worker_failure_writes_agent_out_and_keeps_rc(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (2, "quota text", None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_worker("claude", "p", s, AgentSession(), io)
    assert result.rc == 2
    assert io.agent_out.read_text(encoding="utf-8").strip() == "quota text"
    err = capsys.readouterr().err
    assert "quota text" in err
    assert "claude exited with code 2" in err


def test_claude_worker_invalid_json_success_keeps_session(monkeypatch, tmp_path):
    # Deliberate lenient divergence: bash's jq failures inside agent_call's
    # condition context degraded to empty values rather than aborting.
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "not json at all", None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="keep-me", owner=AgentRef("A", "claude"))
    result = run_worker("claude", "p", s, session, io)
    assert result.rc == 0
    assert result.text == "not json at all"
    assert session.worker_session == "keep-me"


def test_claude_worker_top_level_null_matches_bash(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "null", None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(
        worker_session="old-session",
        last_cost="old-cost",
        owner=AgentRef("A", "claude"),
    )

    result = run_worker("claude", "p", s, session, io)

    assert result == AgentResult(rc=0, text="")
    assert session == AgentSession(
        worker_session="null", last_cost="", owner=AgentRef("A", "claude")
    )
    assert io.agent_out.read_text(encoding="utf-8") == "null\n"


@pytest.mark.parametrize("payload", [[], "text", 0, True])
def test_claude_worker_non_object_json_matches_bash_jq_failure(
    monkeypatch, tmp_path, payload
):
    raw = json.dumps(payload)
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, raw, None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(
        worker_session="old-session",
        last_cost="old-cost",
        owner=AgentRef("A", "claude"),
    )

    result = run_worker("claude", "p", s, session, io)

    assert result == AgentResult(rc=5, text="")
    assert session == AgentSession(
        worker_session="", last_cost="", owner=AgentRef("A", "claude")
    )
    assert io.agent_out.read_text(encoding="utf-8") == raw + "\n"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "null"),
        ({"session_id": None}, "null"),
        ({"session_id": False}, "false"),
        ({"session_id": 0}, "0"),
        ({"session_id": True}, "true"),
        ({"session_id": "session"}, "session"),
        ({"session_id": {"part": 1}}, '{\n  "part": 1\n}'),
        ({"session_id": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_worker_session_id_uses_jq_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload), None)
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(
        worker_session="old-session", owner=AgentRef("A", "claude")
    )

    run_worker("claude", "p", s, session, io)

    assert session.worker_session == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"result": None}, ""),
        ({"result": False}, ""),
        ({"result": 0}, "0"),
        ({"result": True}, "true"),
        ({"result": "work"}, "work"),
        (
            {"result": {"done": [1, 2], "label": "完成"}},
            '{\n  "done": [\n    1,\n    2\n  ],\n  "label": "完成"\n}',
        ),
        ({"result": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_worker_result_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload), None)
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)

    result = run_worker("claude", "p", s, AgentSession(), io)

    assert result == AgentResult(rc=0, text=expected)


def test_codex_worker_fresh_then_resume_argv(monkeypatch, tmp_path):
    calls = []
    ids = iter(["thread-1", "thread-1"])

    def fake_stream(argv, io, ref):
        calls.append(argv)
        return (0, "codex output", next(ids), "")

    monkeypatch.setattr(agents, "_run_codex_json", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_B": "gpt-5.5",
            "AGENT_B": "codex",
            "AGENT_B_ARGS": (
                "-c model_reasoning_effort=low "
                "--config 'developer_instructions=\"two words\"'"
            ),
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession()
    run_worker("codex", "p1", s, session, io)
    assert session.worker_session == "thread-1"
    run_worker("codex", "p2", s, session, io)
    fresh, resumed = calls
    assert fresh[:5] == ["codex", "exec", "--json", "--sandbox", "workspace-write"]
    assert '-c' in fresh and 'model="gpt-5.5"' in fresh
    assert "model_reasoning_effort=low" in fresh
    assert 'developer_instructions="two words"' in fresh
    assert fresh[-1] == "p1"
    assert resumed[:4] == ["codex", "exec", "resume", "--json"]
    assert "--last" not in resumed
    assert 'sandbox_mode="workspace-write"' in resumed
    assert resumed[-2:] == ["thread-1", "p2"]


def test_agy_worker_conversation_flag(monkeypatch, tmp_path):
    calls = []

    def fake_stream(argv, io, ref):
        calls.append(argv)
        log_path = Path(argv[argv.index("--log-file") + 1])
        log_path.write_text(
            "Created conversation 66666666-6666-4666-8666-666666666666\n",
            encoding="utf-8",
        )
        return 0, "x"

    monkeypatch.setattr(agents, "_run_streaming", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "AGENT_A": "agy",
            "AGENT_B": "codex",
            "AGENT_A_ARGS": '--append-system-prompt "two words"',
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession()
    run_worker("agy", "p1", s, session, io)
    assert session.worker_session == "66666666-6666-4666-8666-666666666666"
    run_worker("agy", "p2", s, session, io)
    assert "--continue" not in calls[0] and "--continue" not in calls[1]
    assert "--conversation" not in calls[0]
    index = calls[1].index("--conversation")
    assert calls[1][index + 1] == "66666666-6666-4666-8666-666666666666"
    assert calls[0][:3] == ["agy", "--print", "p1"]
    assert "--dangerously-skip-permissions" in calls[0]
    prompt_index = calls[0].index("--append-system-prompt")
    assert calls[0][prompt_index + 1] == "two words"


def test_claude_reviewer_writes_verdict_from_structured_output(monkeypatch, tmp_path):
    payload = json.dumps({
        "structured_output": {"approved": True, "blockers": [], "suggestions": ["s1"]},
        "total_cost_usd": 0.1,
        "result": "review text",
    })
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload, None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result.text == "review text"
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is True and verdict["suggestions"] == ["s1"]


def test_claude_reviewer_invalid_json_matches_bash_jq_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "not json at all", None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")
    result = run_reviewer("claude", "p", s, session, io)
    assert result == AgentResult(rc=5, text="")
    assert session.last_cost == ""
    assert io.agent_out.read_text(encoding="utf-8") == "not json at all\n"
    assert io.verdict_path.exists()
    assert io.verdict_path.stat().st_size == 0


def test_claude_reviewer_top_level_null_matches_bash(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "null", None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    result = run_reviewer("claude", "p", s, session, io)

    assert result == AgentResult(rc=0, text="")
    assert session.last_cost == ""
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == {
        "approved": False,
        "blockers": ["reviewer did not produce a structured verdict"],
        "suggestions": [],
    }
    assert io.agent_out.read_text(encoding="utf-8") == "null\n"


@pytest.mark.parametrize("payload", [[], "text", 0, True])
def test_claude_reviewer_non_object_json_matches_bash_jq_failure(
    monkeypatch, tmp_path, payload
):
    raw = json.dumps(payload)
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, raw, None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    result = run_reviewer("claude", "p", s, session, io)

    assert result == AgentResult(rc=5, text="")
    assert session.last_cost == ""
    assert io.verdict_path.exists()
    assert io.verdict_path.stat().st_size == 0
    assert io.agent_out.read_text(encoding="utf-8") == raw + "\n"


@pytest.mark.parametrize("structured_output", [{}, [], "", 0])
def test_claude_reviewer_preserves_jq_coalesce_non_null_non_false_values(
    monkeypatch, tmp_path, structured_output
):
    payload = json.dumps({"structured_output": structured_output, "result": "review"})
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload, None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result == AgentResult(rc=0, text="review")
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == structured_output


def test_claude_reviewer_missing_structured_output_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream",
                        lambda argv, io, ref: (0, json.dumps({"result": "no verdict"}), None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, AgentSession(), io)
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is False
    assert verdict["blockers"] == ["reviewer did not produce a structured verdict"]


@pytest.mark.parametrize("structured_output", [None, False])
def test_claude_reviewer_null_or_false_structured_output_uses_fallback(
    monkeypatch, tmp_path, structured_output
):
    payload = json.dumps({"structured_output": structured_output, "result": "review"})
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload, None))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result == AgentResult(rc=0, text="review")
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == {
        "approved": False,
        "blockers": ["reviewer did not produce a structured verdict"],
        "suggestions": [],
    }


@pytest.mark.parametrize("role", ["worker", "reviewer"])
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"total_cost_usd": None}, ""),
        ({"total_cost_usd": False}, ""),
        ({"total_cost_usd": 0}, "0"),
        ({"total_cost_usd": True}, "true"),
        ({"total_cost_usd": "0.50"}, "0.50"),
        (
            {"total_cost_usd": {"a": 1, "b": [2]}},
            '{\n  "a": 1,\n  "b": [\n    2\n  ]\n}',
        ),
        ({"total_cost_usd": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_cost_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, role, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload), None)
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    if role == "worker":
        run_worker("claude", "p", s, session, io)
    else:
        run_reviewer("claude", "p", s, session, io)

    assert session.last_cost == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"result": None}, ""),
        ({"result": False}, ""),
        ({"result": 0}, "0"),
        ({"result": True}, "true"),
        ({"result": "review"}, "review"),
        ({"result": {"approved": True}}, '{\n  "approved": true\n}'),
        ({"result": ["review"]}, '[\n  "review"\n]'),
    ],
)
def test_claude_reviewer_result_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload), None)
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)

    result = run_reviewer("claude", "p", s, AgentSession(), io)

    assert result == AgentResult(rc=0, text=expected)


def test_claude_reviewer_argv_has_schema(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agents, "_run_claude_stream",
                        lambda argv, io, ref: (seen.update(argv=argv), (0, "{}", None))[1])
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, AgentSession(), io)
    argv = seen["argv"]
    assert argv[argv.index("--json-schema") + 1] == VERDICT_SCHEMA


def test_agy_reviewer_uses_30m_timeout_and_no_continue(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agents, "_run_streaming",
                        lambda argv, io, ref: (seen.update(argv=argv), (0, "x"))[1])
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env({"AGENT_A": "agy", "AGENT_B": "codex"}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer(
        "agy",
        "p",
        s,
        AgentSession(worker_session="continue", owner=AgentRef("A", "agy")),
        io,
    )
    argv = seen["argv"]
    assert "--print-timeout" in argv and "30m" in argv
    assert "--continue" not in argv  # reviewers always start fresh


OPENCODE_TEXT = {
    "type": "text",
    "sessionID": "ses_ffcabee46fferqp1FW1lyDROUA",
    "part": {"type": "text", "text": "OK"},
}
OPENCODE_TOOL = {
    "type": "tool_use",
    "sessionID": "ses_ffcaaebd8ffeje788qiYbvCRxS",
    "part": {
        "type": "tool",
        "tool": "read",
        "state": {
            "status": "completed",
            "input": {
                "filePath": r"C:\Project\adversarial-ai-coding\README.md",
                "limit": 1,
            },
            "output": "rate limit in tool output must not be quota",
            "title": "README.md",
        },
    },
}
OPENCODE_FINISH_TOOL = {
    "type": "step_finish",
    "sessionID": "ses_ffcaaebd8ffeje788qiYbvCRxS",
    "part": {"reason": "tool-calls", "cost": 0.015496},
}
OPENCODE_FINISH_STOP = {
    "type": "step_finish",
    "sessionID": "ses_ffcaaebd8ffeje788qiYbvCRxS",
    "part": {"reason": "stop", "cost": 0.004392},
}
OPENCODE_ERROR = {
    "type": "error",
    "sessionID": "ses_quota",
    "error": {
        "name": "APIError",
        "data": {
            "message": "Rate limit exceeded",
            "statusCode": 429,
            "isRetryable": True,
        },
    },
}
# opencode passes the provider's own wording through, and only some
# providers say "rate limit": this is what Gemini reports on a 429.
OPENCODE_ERROR_NO_LIMIT_WORDING = {
    "type": "error",
    "sessionID": "ses_quota",
    "error": {
        "name": "APIError",
        "data": {
            "message": "Resource has been exhausted (e.g. check quota).",
            "statusCode": 429,
        },
    },
}
OPENCODE_ERROR_UNSTRUCTURED = {
    "type": "error",
    "sessionID": "ses_quota",
    "error": {"name": "UnknownError", "data": {"statusCode": 429}},
}
OPENCODE_GROK_LIMIT_FIXTURE = (
    Path(__file__).parent / "fixtures" / "opencode_grok_spending_limit.jsonl"
)


def test_render_opencode_event_echoes_text_and_tool_file_path():
    text = agents.render_opencode_event(json.dumps(OPENCODE_TEXT))
    tool = agents.render_opencode_event(json.dumps(OPENCODE_TOOL))

    assert text.echo == ["OK"]
    assert text.session_id == "ses_ffcabee46fferqp1FW1lyDROUA"
    assert tool.echo == [r" . read C:\Project\adversarial-ai-coding\README.md"]
    assert tool.quota == ""


def test_render_opencode_event_sums_only_finish_cost_and_marks_error_quota():
    finish = agents.render_opencode_event(json.dumps(OPENCODE_FINISH_STOP))
    start = agents.render_opencode_event(
        json.dumps({"type": "step_start", "sessionID": "ses_x", "part": {}})
    )
    error = agents.render_opencode_event(json.dumps(OPENCODE_ERROR))

    assert finish.echo == []
    assert finish.cost == 0.004392
    assert start.echo == []
    assert start.cost is None
    assert "Rate limit exceeded" in error.quota
    assert error.echo == ["Rate limit exceeded (status 429)"]
    assert is_rate_limited(error.quota)


def test_render_opencode_event_keeps_the_reported_status_for_quota_detection():
    """A 429 must be detected from the status, not from the wording.

    Every provider reports the status; only some of them word a quota
    error as "rate limit". Dropping the status would leave a Gemini or
    Ollama run with no retry at all.
    """
    worded = agents.render_opencode_event(
        json.dumps(OPENCODE_ERROR_NO_LIMIT_WORDING)
    )
    unstructured = agents.render_opencode_event(
        json.dumps(OPENCODE_ERROR_UNSTRUCTURED)
    )
    other = agents.render_opencode_event(
        json.dumps(
            {
                "type": "error",
                "sessionID": "ses_x",
                "error": {"data": {"message": "model not found", "statusCode": 404}},
            }
        )
    )

    assert worded.quota == "Resource has been exhausted (e.g. check quota). (status 429)"
    assert is_rate_limited(worded.quota)
    # No message anywhere: the payload itself is the agent's own wording.
    assert "UnknownError" in unstructured.quota
    assert is_rate_limited(unstructured.quota)
    # The status travels with every error; only 429 means a quota wait.
    assert other.quota == "model not found (status 404)"
    assert not is_rate_limited(other.quota)


def test_opencode_grok_spending_limit_event_reaches_quota_detection():
    """Replay the redacted OpenCode/xAI event captured on 2026-08-27."""
    event = agents.render_opencode_event(
        OPENCODE_GROK_LIMIT_FIXTURE.read_text(encoding="utf-8")
    )

    assert event.session_id == "ses_redacted"
    assert event.quota.endswith("(status 403)")
    assert "personal-team-blocked:spending-limit" in event.quota
    assert event.echo == [event.quota]
    assert is_rate_limited(event.quota)


def test_render_opencode_event_marks_a_failed_tool_call():
    failed = json.loads(json.dumps(OPENCODE_TOOL))
    failed["part"]["tool"] = "bash"
    failed["part"]["state"] = {
        "status": "error",
        "input": {"command": "go build ./..."},
        "error": "exit status 1",
    }
    titled = {
        "type": "tool_use",
        "sessionID": "ses_x",
        "part": {"tool": "todowrite", "state": {"status": "completed", "title": "3 todos"}},
    }

    # opencode only reports a call once it is over, so the outcome is known.
    assert agents.render_opencode_event(json.dumps(failed)).echo == [
        " . bash go build ./... (failed)"
    ]
    # No input key this list names: the title is what the call acted on.
    assert agents.render_opencode_event(json.dumps(titled)).echo == [
        " . todowrite 3 todos"
    ]


def test_format_opencode_cost_separates_free_from_unreported():
    # A local model that really costs nothing must not read as "the CLI
    # told us nothing", which is what an empty metrics column means.
    assert agents._format_opencode_cost([]) == ""
    assert agents._format_opencode_cost([0.0, 0.0]) == "0"
    assert agents._format_opencode_cost([0.015496, 0.004392]) == "0.019888"


def test_render_opencode_event_passes_through_non_json(tmp_path):
    rendered = agents.render_opencode_event("not json")
    early = agents.render_opencode_event("Error: 429 Too Many Requests")

    assert rendered.echo == ["not json"]
    # A failure before the event stream starts is still the CLI itself, so
    # a quota it never got past reaches the retry loop (codex parity).
    assert rendered.quota == "not json"
    assert is_rate_limited(early.quota)


def test_opencode_stream_echoes_tools_sums_cost_and_keeps_quota_narrow(
    tmp_path,
):
    emitter = tmp_path / "emit.py"
    events = [
        {"type": "step_start", "sessionID": "ses_stream", "part": {}},
        OPENCODE_TOOL,
        OPENCODE_FINISH_TOOL,
        OPENCODE_TEXT,
        OPENCODE_FINISH_STOP,
    ]
    emitter.write_text(
        "import json\n"
        + "".join(f"print({json.dumps(json.dumps(event))})\n" for event in events),
        encoding="utf-8",
    )
    io, echoed = make_io(tmp_path)
    ref = AgentRef("A", "opencode")

    rc, text, session_id, quota, cost = agents._run_opencode_json(
        [sys.executable, str(emitter)], io, ref
    )

    assert rc == 0
    assert session_id == "ses_ffcaaebd8ffeje788qiYbvCRxS"
    assert text == (
        r" . read C:\Project\adversarial-ai-coding\README.md" + "\nOK"
    )
    assert quota == ""
    assert cost == "0.019888"
    assert echoed == [
        r"[A opencode]  . read C:\Project\adversarial-ai-coding\README.md",
        "[A opencode] OK",
    ]
    raw = io.raw_out.read_text(encoding="utf-8")
    assert '"type": "tool_use"' in raw or '"type":"tool_use"' in raw
    assert "rate limit in tool output" in raw
    assert "rate limit in tool output" not in quota
    # The readable artifact keeps the rendered lines and never the prefix.
    assert io.agent_out.read_text(encoding="utf-8") == text + "\n"
    assert "[A opencode]" not in io.agent_out.read_text(encoding="utf-8")


def test_opencode_error_event_reaches_the_quota_channel(tmp_path):
    emitter = tmp_path / "emit.py"
    emitter.write_text(
        "import json\n"
        f"print({json.dumps(json.dumps(OPENCODE_ERROR))})\n",
        encoding="utf-8",
    )
    io, _ = make_io(tmp_path)

    rc, text, session_id, quota, cost = agents._run_opencode_json(
        [sys.executable, str(emitter)], io, AgentRef("B", "opencode")
    )

    assert rc == 0
    assert session_id == "ses_quota"
    assert "Rate limit exceeded" in quota
    assert "Rate limit exceeded" in text
    assert cost == ""
    # What the retry loop actually asks of this channel.
    assert is_rate_limited(quota)


def test_opencode_worker_fresh_then_resume_argv(monkeypatch, tmp_path):
    calls = []
    ids = iter(["ses_1", "ses_1"])

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, "ok", next(ids), "", "0.02"

    monkeypatch.setattr(agents, "_run_opencode_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "opencode",
            "MODEL_A": "google/gemini-2.5-pro",
            "AGENT_A_ARGS": "--variant high",
        }
    )
    io, _ = make_io(tmp_path)
    session = AgentSession()

    run_worker("opencode", "p1", settings, session, io)
    run_worker("opencode", "p2", settings, session, io)

    assert calls[0] == [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        "-m",
        "google/gemini-2.5-pro",
        "--variant",
        "high",
        "p1",
    ]
    assert calls[1][-3:] == ["--session", "ses_1", "p2"]
    assert session.worker_session == "ses_1"
    assert session.last_cost == "0.02"


def test_opencode_worker_warns_when_fresh_call_omits_session(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        agents,
        "_run_opencode_json",
        lambda argv, io, ref: (0, "ok", "", "", ""),
    )
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    io, echoed = make_io(tmp_path)
    session = AgentSession()

    run_worker("opencode", "p", make({"AGENT_A": "opencode"}), session, io)

    assert session.worker_session == ""
    assert any("did not report a session ID" in line for line in echoed)


def test_opencode_reviewer_is_always_fresh(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(
        agents,
        "_run_opencode_json",
        lambda argv, io, ref: (
            seen.update(argv=argv),
            (0, "review", "ses_ignored", "", "0.01"),
        )[1],
    )
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make({"AGENT_B": "opencode", "MODEL_B": "xai/grok-4.6"})
    io, _ = make_io(tmp_path)

    result = run_reviewer(
        "opencode",
        "review prompt",
        settings,
        AgentSession(
            worker_session="ses_worker", owner=AgentRef("A", "opencode")
        ),
        io,
    )

    argv = seen["argv"]
    assert argv[:5] == ["opencode", "run", "--format", "json", "--auto"]
    assert argv[argv.index("-m") + 1] == "xai/grok-4.6"
    assert "--session" not in argv
    assert argv[-1] == "review prompt"
    assert result.text == "review"


def test_notify_noop_when_unset_and_warns_on_failure(tmp_path, capsys):
    s = Settings.from_env({}, run_id="r")
    notify(s, "hello")  # no NOTIFY_CMD: silent no-op
    s2 = Settings.from_env({"NOTIFY_CMD": "definitely-not-a-command-xyz"}, run_id="r")
    notify(s2, "hello")
    assert "notification command failed" in capsys.readouterr().err
