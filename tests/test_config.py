"""Pins the bash settings defaults from adversarial-ai-coding.sh:285-330.

The bash agent-alias tests (helpers.test.sh:63-71) are deliberately not
ported: the Python version drops the ENGINE_* legacy aliases, and a test
below pins that they are ignored."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError


def make(env=None, run_id="20260710-120000", snapshot=None):
    return Settings.from_env(env or {}, run_id=run_id, snapshot=snapshot)


def test_defaults_match_bash():
    s = make()
    assert s.agent_a == "claude"
    assert s.agent_b == "codex"
    assert s.impl_agent == ""
    assert s.impl_model == ""
    assert s.impl_args == ""
    assert s.model_a == ""
    assert s.model_b == ""
    assert s.max_rounds == 3
    assert s.auto_branch is True
    assert s.use_worktree is False
    assert s.human_gate is True
    assert s.human_gate_plan is False
    assert s.dual_spec is False
    assert s.open_pr is False
    assert s.notify_cmd == ""
    assert s.retry_on_limit is True
    assert s.retry_max == 6
    assert s.retry_base_wait == 300
    assert s.retry_max_wait == 3600
    assert s.retry_max_reset_wait == 21600
    assert s.tools == "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"
    assert s.spec_dir == "aac/docs/20260710-120000"


def test_plan_gate_is_opt_in_and_independent_of_human_gate():
    assert make({"HUMAN_GATE_PLAN": "1"}).human_gate_plan is True
    s = make({"HUMAN_GATE": "0", "HUMAN_GATE_PLAN": "1"})
    assert (s.human_gate, s.human_gate_plan) == (False, True)


def test_agent_a_configures_slot_a():
    # helpers.test.sh: "agent aliases:AGENT_A configures slot A"
    assert make({"AGENT_A": "codex", "AGENT_B": "claude"}).agent_a == "codex"


def test_legacy_engine_vars_are_ignored():
    # Deliberate divergence: bash accepts ENGINE_* as aliases; Python does not.
    s = make({"ENGINE_A": "agy", "ENGINE_B": "claude"})
    assert (s.agent_a, s.agent_b) == ("claude", "codex")


def test_custom_agent_args():
    # helpers.test.sh: "agent aliases:custom agent uses AGENT_A_ARGS"
    s = make({"AGENT_A": "custom-agent", "AGENT_A_ARGS": "--model custom --flag"})
    assert s.agent_a_args == "--model custom --flag"


@pytest.mark.parametrize(
    "name",
    ["CLAUDE_ARGS", "CODEX_ARGS", "AGY_ARGS", "OPENCODE_ARGS"],
)
def test_from_env_rejects_removed_adapter_args_even_when_empty(name):
    with pytest.raises(SettingsError) as exc_info:
        make({name: ""})

    message = str(exc_info.value)
    assert name in message
    assert "AGENT_A_ARGS" in message
    assert "AGENT_B_ARGS" in message
    assert "IMPL_ARGS" in message


def test_from_env_rejects_removed_adapter_args_in_snapshot_mapping():
    with pytest.raises(SettingsError) as exc_info:
        make({}, snapshot={"CODEX_ARGS": ""})

    message = str(exc_info.value)
    assert "CODEX_ARGS" in message
    assert "AGENT_A_ARGS" in message
    assert "AGENT_B_ARGS" in message
    assert "IMPL_ARGS" in message


def test_from_env_lists_every_removed_adapter_arg_in_one_error():
    with pytest.raises(SettingsError) as exc_info:
        make({"OPENCODE_ARGS": "--variant high"}, snapshot={"CLAUDE_ARGS": ""})

    message = str(exc_info.value)
    assert "CLAUDE_ARGS" in message
    assert "OPENCODE_ARGS" in message
    assert message.index("CLAUDE_ARGS") < message.index("OPENCODE_ARGS")
    assert "AGENT_A_ARGS" in message
    assert "AGENT_B_ARGS" in message
    assert "IMPL_ARGS" in message


def test_empty_slot_argument_environment_values_do_not_clear_snapshot():
    snapshot = {
        "AGENT_A_ARGS": "--slot-a",
        "AGENT_B_ARGS": "--slot-b",
        "IMPL_ARGS": "--slot-i",
    }
    s = make(
        {"AGENT_A_ARGS": "", "AGENT_B_ARGS": "", "IMPL_ARGS": ""},
        snapshot=snapshot,
    )
    assert s.agent_a_args == "--slot-a"
    assert s.agent_b_args == "--slot-b"
    assert s.impl_args == "--slot-i"


def test_impl_settings_read_environment_values():
    s = make(
        {
            "IMPL_AGENT": "custom-impl",
            "IMPL_MODEL": "impl-model",
            "IMPL_ARGS": "--impl-flag value",
        }
    )
    assert s.impl_agent == "custom-impl"
    assert s.impl_model == "impl-model"
    assert s.impl_args == "--impl-flag value"


def test_impl_settings_resume_from_snapshot():
    s = make(
        {},
        snapshot={
            "IMPL_AGENT": "snapshot-agent",
            "IMPL_MODEL": "snapshot-model",
            "IMPL_ARGS": "--snapshot-flag",
        },
    )
    assert s.impl_agent == "snapshot-agent"
    assert s.impl_model == "snapshot-model"
    assert s.impl_args == "--snapshot-flag"


def test_non_empty_impl_environment_values_override_snapshot():
    snapshot = {
        "IMPL_AGENT": "snapshot-agent",
        "IMPL_MODEL": "snapshot-model",
        "IMPL_ARGS": "--snapshot-flag",
    }
    s = make(
        {
            "IMPL_AGENT": "env-agent",
            "IMPL_MODEL": "env-model",
            "IMPL_ARGS": "--env-flag",
        },
        snapshot=snapshot,
    )
    assert s.impl_agent == "env-agent"
    assert s.impl_model == "env-model"
    assert s.impl_args == "--env-flag"


def test_empty_impl_environment_values_do_not_clear_snapshot():
    snapshot = {
        "IMPL_AGENT": "snapshot-agent",
        "IMPL_MODEL": "snapshot-model",
        "IMPL_ARGS": "--snapshot-flag",
    }
    s = make(
        {"IMPL_AGENT": "", "IMPL_MODEL": "", "IMPL_ARGS": ""},
        snapshot=snapshot,
    )
    assert s.impl_agent == "snapshot-agent"
    assert s.impl_model == "snapshot-model"
    assert s.impl_args == "--snapshot-flag"


def test_snapshot_supplies_resumed_defaults_and_env_wins():
    snap = {"AGENT_A": "agy", "MAX_ROUNDS": "5", "TOOLS": "Bash(ls *)"}
    s = make({}, snapshot=snap)
    assert s.agent_a == "agy"
    assert s.max_rounds == 5
    assert s.tools == "Bash(ls *)"
    s = make({"AGENT_A": "codex", "MAX_ROUNDS": "2"}, snapshot=snap)
    assert s.agent_a == "codex"
    assert s.max_rounds == 2


def test_notify_cmd_and_retry_never_come_from_snapshot():
    # Bash line 307: NOTIFY_CMD deliberately not persisted; RETRY_* likewise.
    s = make({}, snapshot={"NOTIFY_CMD": "notify-send", "RETRY_MAX": "99"})
    assert s.notify_cmd == ""
    assert s.retry_max == 6


def test_spec_dir_uses_run_id_by_default():
    assert make(run_id="abc-123").spec_dir == "aac/docs/abc-123"
    assert make({"SPEC_DIR": "myspecs"}).spec_dir == "myspecs"


def test_non_integer_max_rounds_raises():
    with pytest.raises(SettingsError, match="MAX_ROUNDS"):
        make({"MAX_ROUNDS": "three"})


def test_empty_env_values_fall_back_like_bash():
    # bash ${VAR:-default} treats exported-but-empty as unset.
    s = make({"RETRY_MAX": "", "RETRY_ON_LIMIT": "", "MAX_ROUNDS": "", "AUTO_BRANCH": ""})
    assert s.retry_max == 6
    assert s.retry_on_limit is True
    assert s.max_rounds == 3
    assert s.auto_branch is True


def test_phase_settings_defaults_and_parsing():
    settings = Settings.from_env({}, run_id="r")
    assert settings.phases is False
    assert settings.phase_review is False
    settings = Settings.from_env({"PHASES": "1", "PHASE_REVIEW": "1"}, run_id="r")
    assert settings.phases is True
    assert settings.phase_review is True


def test_phase_settings_resume_from_snapshot():
    settings = Settings.from_env(
        {}, run_id="r", snapshot={"PHASES": "1", "PHASE_REVIEW": "1"}
    )
    assert settings.phases is True
    assert settings.phase_review is True


def test_phases_explicit_tracks_environment_presence():
    from adversarial_ai_coding.config import Settings

    assert Settings.from_env({"PHASES": "0"}, run_id="t").phases_explicit
    assert Settings.from_env({"PHASES": "1"}, run_id="t").phases_explicit
    assert not Settings.from_env({}, run_id="t").phases_explicit
    # Empty string behaves as unset, matching persisted() semantics.
    assert not Settings.from_env({"PHASES": ""}, run_id="t").phases_explicit
    # A snapshot value is not "explicit": the user did not type it now.
    resumed = Settings.from_env({}, run_id="t", snapshot={"PHASES": "1"})
    assert resumed.phases is True
    assert resumed.phases_explicit is False


def test_import_settings_default_off():
    settings = Settings.from_env({}, run_id="r")
    assert settings.import_spec == ""
    assert settings.import_plan == ""
    assert settings.import_review is True


def test_import_settings_from_env_and_snapshot():
    settings = Settings.from_env(
        {"IMPORT_SPEC": "ext/spec.md", "IMPORT_REVIEW": "0"},
        run_id="r",
        snapshot={"IMPORT_PLAN": "ext/plan.md"},
    )
    assert settings.import_spec == "ext/spec.md"
    assert settings.import_plan == "ext/plan.md"
    assert settings.import_review is False


@pytest.mark.parametrize("raw", ["", "2", "yes"])
def test_import_review_accepts_only_zero_or_one(raw):
    with pytest.raises(SettingsError, match="IMPORT_REVIEW must be 0 or 1"):
        make({"IMPORT_SPEC": "ext/spec.md", "IMPORT_REVIEW": raw})
