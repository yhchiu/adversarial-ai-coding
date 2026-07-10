"""Ports tests/helpers.test.sh:63-71 (agent aliases) and pins the bash
settings defaults from adversarial-ai-coding.sh:285-330."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError, alias_env_or_default


def make(env=None, run_id="20260710-120000", snapshot=None):
    return Settings.from_env(env or {}, run_id=run_id, snapshot=snapshot)


def test_defaults_match_bash():
    s = make()
    assert s.engine_a == "claude"
    assert s.engine_b == "codex"
    assert s.model_a == ""
    assert s.model_b == ""
    assert s.max_rounds == 3
    assert s.auto_branch is True
    assert s.use_worktree is False
    assert s.human_gate is True
    assert s.dual_spec is False
    assert s.open_pr is False
    assert s.notify_cmd == ""
    assert s.retry_on_limit is True
    assert s.retry_max == 6
    assert s.retry_base_wait == 300
    assert s.retry_max_wait == 3600
    assert s.retry_max_reset_wait == 21600
    assert s.tools == "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"
    assert s.spec_dir == "specs/20260710-120000"
    assert s.runs_dir == ".workflow/runs"


def test_agent_a_alias_configures_slot_a():
    # helpers.test.sh: "agent aliases:AGENT_A configures slot A"
    assert make({"AGENT_A": "codex", "AGENT_B": "claude"}).engine_a == "codex"


def test_legacy_engine_vars_still_work():
    s = make({"ENGINE_A": "agy", "ENGINE_B": "claude"})
    assert (s.engine_a, s.engine_b) == ("agy", "claude")


def test_conflicting_alias_fails_fast():
    # helpers.test.sh: "agent aliases:conflicting AGENT_A and ENGINE_A fail fast"
    with pytest.raises(SettingsError, match="Conflicting AGENT_A and ENGINE_A"):
        make({"AGENT_A": "claude", "ENGINE_A": "codex"})


def test_matching_alias_values_are_not_a_conflict():
    assert make({"AGENT_A": "codex", "ENGINE_A": "codex"}).engine_a == "codex"


def test_custom_agent_args_alias():
    # helpers.test.sh: "agent aliases:custom agent uses AGENT_A_ARGS"
    s = make({"AGENT_A": "custom-agent", "AGENT_A_ARGS": "--model custom --flag"})
    assert s.engine_a_args == "--model custom --flag"


def test_conflicting_args_alias_fails_fast():
    with pytest.raises(SettingsError, match="Conflicting AGENT_B_ARGS and ENGINE_B_ARGS"):
        make({"AGENT_B_ARGS": "--x", "ENGINE_B_ARGS": "--y"})


def test_snapshot_supplies_resumed_defaults_and_env_wins():
    snap = {"ENGINE_A": "agy", "MAX_ROUNDS": "5", "TOOLS": "Bash(ls *)"}
    s = make({}, snapshot=snap)
    assert s.engine_a == "agy"
    assert s.max_rounds == 5
    assert s.tools == "Bash(ls *)"
    s = make({"AGENT_A": "codex", "MAX_ROUNDS": "2"}, snapshot=snap)
    assert s.engine_a == "codex"
    assert s.max_rounds == 2


def test_notify_cmd_and_retry_never_come_from_snapshot():
    # Bash line 307: NOTIFY_CMD deliberately not persisted; RETRY_* likewise.
    s = make({}, snapshot={"NOTIFY_CMD": "notify-send", "RETRY_MAX": "99"})
    assert s.notify_cmd == ""
    assert s.retry_max == 6


def test_spec_dir_uses_run_id_by_default():
    assert make(run_id="abc-123").spec_dir == "specs/abc-123"
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


def test_alias_env_or_default_direct():
    assert alias_env_or_default({}, "A", "B", "d") == "d"
    assert alias_env_or_default({"B": "legacy"}, "A", "B", "d") == "legacy"
    assert alias_env_or_default({"A": "new", "B": "new"}, "A", "B", "d") == "new"
