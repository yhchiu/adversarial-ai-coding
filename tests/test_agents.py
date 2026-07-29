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
    ("ref", "env", "expected"),
    [
        (
            AgentRef("A", "claude"),
            {"CLAUDE_ARGS": '--append-system-prompt "claude words"'},
            [("CLAUDE_ARGS", '--append-system-prompt "claude words"')],
        ),
        (
            AgentRef("B", "codex"),
            {"CODEX_ARGS": "-c model_reasoning_effort=low"},
            [("CODEX_ARGS", "-c model_reasoning_effort=low")],
        ),
        (
            AgentRef("A", "agy"),
            {"AGY_ARGS": '--append-system-prompt "agy words"'},
            [("AGY_ARGS", '--append-system-prompt "agy words"')],
        ),
        (
            AgentRef("A", "worker-wrapper"),
            {
                "AGENT_A": "worker-wrapper",
                "AGENT_A_ARGS": '--profile "worker words"',
            },
            [("AGENT_A_ARGS", '--profile "worker words"')],
        ),
        (
            AgentRef("B", "review-wrapper"),
            {
                "AGENT_B": "review-wrapper",
                "AGENT_B_ARGS": '--profile "review words"',
            },
            [("AGENT_B_ARGS", '--profile "review words"')],
        ),
        (
            AgentRef("I", "claude", base_slot="A"),
            {
                "CLAUDE_ARGS": '--append-system-prompt "base words"',
                "IMPL_ARGS": '--permission-mode "impl mode"',
            },
            [
                ("CLAUDE_ARGS", '--append-system-prompt "base words"'),
                ("IMPL_ARGS", '--permission-mode "impl mode"'),
            ],
        ),
        (
            AgentRef("I", "impl-wrapper"),
            {
                "IMPL_AGENT": "impl-wrapper",
                "IMPL_ARGS": '--profile "impl words"',
            },
            [("IMPL_ARGS", '--profile "impl words"')],
        ),
        (AgentRef("A", "claude"), {}, []),
    ],
)
def test_arg_sources_select_non_empty_adapter_args(ref, env, expected):
    assert agents._arg_sources(ref, make(env)) == expected


def test_shared_sources_keep_runtime_and_metadata_order(monkeypatch):
    sources = [
        ("CLAUDE_ARGS", '--first "two words"'),
        ("AGENT_A_ARGS", "--second final"),
    ]
    monkeypatch.setattr(agents, "_arg_sources", lambda ref, settings: sources)
    ref = AgentRef("A", "claude")
    settings = make()

    assert agents.agent_args(ref, settings) == [
        "--first",
        "two words",
        "--second",
        "final",
    ]
    assert agents.resolve_model_args(ref, settings) == (
        '--first "two words" --second final'
    )


def test_agent_args_attributes_quoting_error_to_each_source(monkeypatch):
    sources = [
        ("CLAUDE_ARGS", "--first valid"),
        ("AGENT_A_ARGS", '--second "unterminated'),
    ]
    monkeypatch.setattr(agents, "_arg_sources", lambda ref, settings: sources)

    with pytest.raises(SettingsError, match=r"AGENT_A_ARGS.*quoting"):
        agents.agent_args(AgentRef("A", "claude"), make())


def test_agent_model_unset_is_empty_for_cli_default():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    assert agent_model("claude", s) == ""


def test_agent_model_custom_agent_ignores_model_a():
    s = make({"AGENT_A": "custom-agent", "AGENT_B": "codex", "MODEL_A": "ignored",
              "AGENT_A_ARGS": "--model custom"})
    assert agent_model("custom-agent", s) == ""


def test_resolve_model_args_builtin_uses_cli_args():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex",
              "CLAUDE_ARGS": "--fast", "CODEX_ARGS": "-c model_reasoning_effort=low"})
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
        ("CLAUDE_ARGS", {}),
        ("CODEX_ARGS", {}),
        ("AGY_ARGS", {"AGENT_A": "agy"}),
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
            "CODEX_ARGS",
            '--config developer_instructions="mention --sandbox safely"',
        ),
        ("AGY_ARGS", '--append-system-prompt "mention --continue safely"'),
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
    s = make({"CLAUDE_ARGS": value})

    with pytest.raises(SettingsError, match="CLAUDE_ARGS.*workflow-owned"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("CLAUDE_ARGS", {}),
        ("CODEX_ARGS", {}),
        ("AGY_ARGS", {"AGENT_A": "agy"}),
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
    s = make({"CODEX_ARGS": value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(s, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        "CODEX_ARGS cannot set the model; "
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
    s = make({"CODEX_ARGS": value})

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
    s = make({"CODEX_ARGS": value})

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
    settings = make({"CODEX_ARGS": value})

    with pytest.raises(SettingsError, match="CODEX_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("value", ["-mgpt-5", "-m=gpt-5"])
@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("CLAUDE_ARGS", {}),
        ("CODEX_ARGS", {}),
        ("AGY_ARGS", {"AGENT_A": "agy"}),
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
    settings = make({"CODEX_ARGS": value})

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
        "--conversation conversation-id",
        "--conversation=conversation-id",
    ],
)
def test_validate_agents_rejects_agy_workflow_owned_args(value):
    s = make({"AGENT_A": "agy", "AGY_ARGS": value})

    with pytest.raises(SettingsError):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "agent_env", "value"),
    [
        ("CLAUDE_ARGS", {}, "--json"),
        ("CODEX_ARGS", {}, "--continue"),
        ("AGY_ARGS", {"AGENT_A": "agy"}, "--sandbox workspace-write"),
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
    ],
)
def test_validate_agents_applies_explicit_impl_adapter_reserved_rules(
    adapter, value
):
    settings = make({"IMPL_AGENT": adapter, "IMPL_ARGS": value})

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("adapter", ["claude", "codex", "agy"])
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


def test_validate_agents_checks_impl_args_against_each_dual_owner_candidate():
    settings = make(
        {
            "AGENT_A": "codex",
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


@pytest.mark.parametrize(
    ("env", "owner_name"),
    [
        (
            {
                "AGENT_A": "worker-wrapper",
                "AGENT_B": "codex",
                "IMPL_ARGS": "--fast",
            },
            "worker-wrapper",
        ),
        (
            {
                "AGENT_A": "claude",
                "AGENT_B": "review-wrapper",
                "DUAL_SPEC": "1",
                "IMPL_MODEL": "impl-model",
            },
            "review-wrapper",
        ),
    ],
)
def test_validate_agents_requires_explicit_impl_wrapper_for_custom_owner(
    env, owner_name
):
    settings = make(env)

    with pytest.raises(SettingsError, match=rf"custom agent command {owner_name}"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


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


def test_claude_implementation_worker_orders_fresh_and_resume_argv(
    monkeypatch, tmp_path
):
    calls = []
    session_ids = iter(["claude-session-1", "claude-session-2"])

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, json.dumps({"session_id": next(session_ids), "result": "ok"})

    monkeypatch.setattr(agents, "_run_claude_stream", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "claude",
            "MODEL_A": "owner-model",
            "CLAUDE_ARGS": '--base-claude "base words"',
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
        "--base-claude",
        "base words",
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
        return 0, "ok", next(thread_ids)

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    settings = make(
        {
            "AGENT_A": "codex",
            "MODEL_A": "owner-model",
            "CODEX_ARGS": '--base-codex "base words"',
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
        "--base-codex",
        "base words",
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
            "AGY_ARGS": '--base-agy "base words"',
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
        "--base-agy",
        "base words",
        "--impl-agy",
        "impl words",
    ]
    for argv in calls:
        model_index = argv.index("--model")
        assert argv[model_index : model_index + len(expected_args)] == expected_args
    assert "--conversation" not in calls[0]
    assert calls[1][-2:] == ["--conversation", conversation_id]


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
    echoed, envelope = agents.render_claude_event(json.dumps(event))

    assert echoed == expected
    assert envelope == ""


def test_render_claude_event_returns_the_result_envelope():
    line = json.dumps({"type": "result", "session_id": "s1", "result": "done"})

    echoed, envelope = agents.render_claude_event(line)

    assert echoed == []
    assert json.loads(envelope) == json.loads(line)


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
    assert agents.render_claude_event(line) == (expected, "")


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

    echoed, _ = agents.render_claude_event(json.dumps(event))

    assert echoed == [expected]


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
    lines = [json.dumps(CLAUDE_ASSISTANT), result]
    io, echoed = make_io(tmp_path)

    rc, envelope = agents._run_claude_stream(
        _claude_emitter(tmp_path, lines), io, AgentRef("A", "claude")
    )

    assert rc == 0
    assert json.loads(envelope) == json.loads(result)
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

    rc, envelope = agents._run_claude_stream(
        _claude_emitter(tmp_path, lines), io, AgentRef("A", "claude")
    )

    assert rc == 0
    assert envelope == "\n".join(lines)


def test_claude_worker_quota_stream_reaches_agent_out_for_ratelimit(tmp_path):
    from adversarial_ai_coding.ratelimit import is_rate_limited

    lines = ['{"type":"system","subtype":"init"}',
             "You've hit your usage limit. Please try again in 20s."]
    io, _ = make_io(tmp_path)

    _, envelope = agents._run_claude_stream(
        _claude_emitter(tmp_path, lines), io, AgentRef("A", "claude")
    )
    agents._write_agent_out(io, envelope)

    assert is_rate_limited(io.agent_out)


def test_claude_worker_parses_json_and_tracks_session(monkeypatch, tmp_path):
    payload = json.dumps(
        {"session_id": "sess-1", "total_cost_usd": 0.42, "result": "did the work"}
    )
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload))
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
        return (0, json.dumps({"session_id": "s2", "result": "ok"}))

    monkeypatch.setattr(agents, "_run_claude_stream", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_A": "haiku",
            "CLAUDE_ARGS": '--append-system-prompt "two words"',
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (2, "quota text"))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "not json at all"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="keep-me", owner=AgentRef("A", "claude"))
    result = run_worker("claude", "p", s, session, io)
    assert result.rc == 0
    assert result.text == "not json at all"
    assert session.worker_session == "keep-me"


def test_claude_worker_top_level_null_matches_bash(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "null"))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, raw))
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
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload))
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
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload))
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
        return (0, "codex output", next(ids))

    monkeypatch.setattr(agents, "_run_codex_json", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_B": "gpt-5.5",
            "AGENT_B": "codex",
            "CODEX_ARGS": (
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
            "AGY_ARGS": '--append-system-prompt "two words"',
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result.text == "review text"
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is True and verdict["suggestions"] == ["s1"]


def test_claude_reviewer_invalid_json_matches_bash_jq_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "not json at all"))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, "null"))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, raw))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result == AgentResult(rc=0, text="review")
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == structured_output


def test_claude_reviewer_missing_structured_output_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_claude_stream",
                        lambda argv, io, ref: (0, json.dumps({"result": "no verdict"})))
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
    monkeypatch.setattr(agents, "_run_claude_stream", lambda argv, io, ref: (0, payload))
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
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload))
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
        agents, "_run_claude_stream", lambda argv, io, ref: (0, json.dumps(payload))
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)

    result = run_reviewer("claude", "p", s, AgentSession(), io)

    assert result == AgentResult(rc=0, text=expected)


def test_claude_reviewer_argv_has_schema(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agents, "_run_claude_stream",
                        lambda argv, io, ref: (seen.update(argv=argv), (0, "{}"))[1])
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


def test_notify_noop_when_unset_and_warns_on_failure(tmp_path, capsys):
    s = Settings.from_env({}, run_id="r")
    notify(s, "hello")  # no NOTIFY_CMD: silent no-op
    s2 = Settings.from_env({"NOTIFY_CMD": "definitely-not-a-command-xyz"}, run_id="r")
    notify(s2, "hello")
    assert "notification command failed" in capsys.readouterr().err
