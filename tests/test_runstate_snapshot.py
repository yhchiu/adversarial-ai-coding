"""Ports tests/helpers.test.sh:696-731 (conf parser/writer) onto settings.json."""

import json

import pytest

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runstate import (
    IMMUTABLE_KEYS,
    SNAPSHOT_KEYS,
    RunStateError,
    check_immutable,
    load_snapshot,
    snapshot_values,
    write_snapshot,
)


def make_settings(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def write_raw(state_dir, payload):
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "settings.json").write_text(payload, encoding="utf-8")


def test_write_parse_roundtrip_keeps_spaces_and_quotes(tmp_path):
    # helpers.test.sh: "resume conf:write/parse roundtrip keeps spaces and quotes"
    s = make_settings(
        {
            "SPEC_DIR": "specs/x y",
            "CODEX_ARGS": '-c model="x,y" --flag "quoted value"',
        }
    )
    values = snapshot_values(
        s,
        branch="main",
        gate_cmd="go test ./...",
        build_gate_cmd="",
        task_arg="task.md",
        task_source_kind="file",
        task_source_path="/tmp/task dir/task.md",
    )
    write_snapshot(tmp_path / "st", values)
    snap = load_snapshot(tmp_path / "st")
    assert snap["SPEC_DIR"] == "specs/x y"
    assert snap["CODEX_ARGS"] == '-c model="x,y" --flag "quoted value"'
    assert snap["GATE_CMD"] == "go test ./..."
    assert snap["TASK_SOURCE_PATH"] == "/tmp/task dir/task.md"


def test_newline_values_round_trip(tmp_path):
    # Divergence from bash (which refused newlines due to its line format):
    # JSON holds them safely; task_arg still keeps only its first line.
    s = make_settings({"TOOLS": "Bash(git *)"})
    values = snapshot_values(
        s,
        branch="b",
        gate_cmd="a\nb",
        build_gate_cmd="",
        task_arg="line1\nline2",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["task_arg"] == "line1"
    write_snapshot(tmp_path / "st", values)
    assert load_snapshot(tmp_path / "st")["GATE_CMD"] == "a\nb"


def test_unknown_key_is_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"schema": 1, "evil_key": "x"}))
    with pytest.raises(RunStateError, match="unknown key"):
        load_snapshot(tmp_path / "st")


def test_missing_schema_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"spec_dir": "specs/x"}))
    with pytest.raises(RunStateError, match="schema"):
        load_snapshot(tmp_path / "st")


def test_wrong_schema_rejected(tmp_path):
    write_raw(tmp_path / "st", json.dumps({"schema": 2, "spec_dir": "x"}))
    with pytest.raises(RunStateError, match="schema"):
        load_snapshot(tmp_path / "st")


def test_empty_or_invalid_json_rejected(tmp_path):
    write_raw(tmp_path / "st", "")
    with pytest.raises(RunStateError):
        load_snapshot(tmp_path / "st")
    write_raw(tmp_path / "st", "truncated line without equals")
    with pytest.raises(RunStateError):
        load_snapshot(tmp_path / "st")


def test_missing_file_rejected(tmp_path):
    with pytest.raises(RunStateError, match="Missing resume settings snapshot"):
        load_snapshot(tmp_path / "st")


def test_snapshot_feeds_settings_from_env():
    s = make_settings({"AGENT_A": "agy", "MAX_ROUNDS": "5"})
    values = snapshot_values(
        s,
        branch="b",
        gate_cmd="",
        build_gate_cmd="",
        task_arg="",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["agent_a"] == "agy"
    assert values["max_rounds"] == "5"
    assert values["auto_branch"] == "1"
    assert values["use_worktree"] == "0"


def test_impl_settings_are_snapshot_keys_but_not_immutable():
    assert {"impl_agent", "impl_model", "impl_args"} <= set(SNAPSHOT_KEYS)
    assert {"IMPL_AGENT", "IMPL_MODEL", "IMPL_ARGS"}.isdisjoint(IMMUTABLE_KEYS)


def test_snapshot_values_includes_impl_settings():
    s = make_settings(
        {
            "IMPL_AGENT": "custom-impl",
            "IMPL_MODEL": "impl-model",
            "IMPL_ARGS": "--impl-flag value",
        }
    )
    values = snapshot_values(
        s,
        branch="b",
        gate_cmd="",
        build_gate_cmd="",
        task_arg="",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["impl_agent"] == "custom-impl"
    assert values["impl_model"] == "impl-model"
    assert values["impl_args"] == "--impl-flag value"


def test_plan_gate_survives_resume_without_the_env_var(tmp_path):
    s = make_settings({"HUMAN_GATE_PLAN": "1"})
    values = snapshot_values(
        s,
        branch="b",
        gate_cmd="",
        build_gate_cmd="",
        task_arg="",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["human_gate_plan"] == "1"
    write_snapshot(tmp_path / "st", values)
    snap = load_snapshot(tmp_path / "st")
    resumed = Settings.from_env({}, run_id="20260711-000000", snapshot=snap)
    assert resumed.human_gate_plan is True


def test_check_immutable_conflict():
    # helpers.test.sh: "resume load:immutable field conflict is rejected"
    snap = {"DUAL_SPEC": "0", "SPEC_DIR": "specs/r"}
    with pytest.raises(RunStateError, match="DUAL_SPEC=1 conflicts"):
        check_immutable({"DUAL_SPEC": "1"}, snap)
    check_immutable({"DUAL_SPEC": "0"}, snap)
    check_immutable({"DUAL_SPEC": ""}, snap)
    check_immutable({}, snap)
