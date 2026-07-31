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
            "SPEC_DIR": "aac/docs/x y",
            "CODEX_ARGS": '-c model="x,y" --flag "quoted value"',
        }
    )
    values = snapshot_values(
        s,
        branch="main",
        gate_cmd="go test ./...",
        build_gate_cmd="",
        phase_gate_cmd="",
        task_arg="task.md",
        task_source_kind="file",
        task_source_path="/tmp/task dir/task.md",
    )
    write_snapshot(tmp_path / "st", values)
    snap = load_snapshot(tmp_path / "st")
    assert snap["SPEC_DIR"] == "aac/docs/x y"
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
        phase_gate_cmd="",
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
    write_raw(tmp_path / "st", json.dumps({"spec_dir": "aac/docs/x"}))
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
        phase_gate_cmd="",
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
        phase_gate_cmd="",
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
        phase_gate_cmd="",
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
    snap = {"DUAL_SPEC": "0", "SPEC_DIR": "aac/docs/r"}
    with pytest.raises(RunStateError, match="DUAL_SPEC=1 conflicts"):
        check_immutable({"DUAL_SPEC": "1"}, snap)
    check_immutable({"DUAL_SPEC": "0"}, snap)
    check_immutable({"DUAL_SPEC": ""}, snap)
    check_immutable({}, snap)


def test_snapshot_values_records_phase_settings():
    from adversarial_ai_coding.config import Settings

    settings = Settings.from_env({"PHASES": "1", "PHASE_REVIEW": "1"}, run_id="r")
    values = snapshot_values(
        settings,
        branch="b",
        gate_cmd="g",
        build_gate_cmd="bg",
        phase_gate_cmd="pg",
        task_arg="t",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["phases"] == "1"
    assert values["phase_review"] == "1"
    assert values["phase_gate_cmd"] == "pg"


def test_snapshot_round_trips_phase_keys(tmp_path):
    values = {key: "" for key in SNAPSHOT_KEYS}
    values.update(
        {"phases": "1", "phase_review": "1", "phase_gate_cmd": "go test ./..."}
    )
    write_snapshot(tmp_path, values)
    snapshot = load_snapshot(tmp_path)
    assert snapshot["PHASES"] == "1"
    assert snapshot["PHASE_REVIEW"] == "1"
    assert snapshot["PHASE_GATE_CMD"] == "go test ./..."


def test_check_immutable_refuses_phases_conflict():
    with pytest.raises(RunStateError, match="PHASES=1 conflicts"):
        check_immutable({"PHASES": "1"}, {"PHASES": "0"})
    check_immutable({"PHASES": "1"}, {"PHASES": "1"})
    check_immutable({}, {"PHASES": "0"})


def test_check_immutable_defaults_missing_phases_to_off():
    # Snapshots from before the phased feature have no "phases" key; they
    # were necessarily non-phased runs, so PHASES=1 on resume must refuse.
    with pytest.raises(RunStateError, match="PHASES=1 conflicts"):
        check_immutable({"PHASES": "1"}, {})
    check_immutable({"PHASES": "0"}, {})


def test_snapshot_round_trips_import_settings(tmp_path):
    settings = Settings.from_env(
        {
            "IMPORT_SPEC": "ext/spec.md",
            "IMPORT_PLAN": "ext/plan.md",
            "IMPORT_REVIEW": "0",
        },
        run_id="r",
    )
    values = snapshot_values(
        settings,
        branch="aac/r",
        gate_cmd="",
        build_gate_cmd="",
        phase_gate_cmd="",
        task_arg="t",
        task_source_kind="literal",
        task_source_path="",
    )
    assert values["import_spec"] == "ext/spec.md"
    assert values["import_plan"] == "ext/plan.md"
    assert values["import_review"] == "0"
    write_snapshot(tmp_path, values)
    snap = load_snapshot(tmp_path)
    resumed = Settings.from_env({}, run_id="r2", snapshot=snap)
    assert resumed.import_spec == "ext/spec.md"
    assert resumed.import_plan == "ext/plan.md"
    assert resumed.import_review is False


def test_import_keys_are_immutable_on_resume():
    snap = {"IMPORT_SPEC": "a.md", "IMPORT_PLAN": "", "IMPORT_REVIEW": "1"}
    check_immutable({"IMPORT_SPEC": "a.md"}, snap)
    with pytest.raises(RunStateError, match="IMPORT_SPEC"):
        check_immutable({"IMPORT_SPEC": "b.md"}, snap)
    with pytest.raises(RunStateError, match="IMPORT_REVIEW"):
        check_immutable({"IMPORT_REVIEW": "0"}, snap)


def test_import_missing_from_old_snapshot_refuses_enabling():
    check_immutable({}, {})
    with pytest.raises(RunStateError, match="IMPORT_SPEC"):
        check_immutable({"IMPORT_SPEC": "a.md"}, {})
    with pytest.raises(RunStateError, match="IMPORT_REVIEW"):
        check_immutable({"IMPORT_REVIEW": "0"}, {})
    check_immutable({"IMPORT_REVIEW": "1"}, {})


def test_enable_snapshot_phases_flips_and_stays_resumable(tmp_path):
    import pytest

    from adversarial_ai_coding.config import Settings
    from adversarial_ai_coding.runstate import (
        RunStateError,
        check_immutable,
        enable_snapshot_phases,
        load_snapshot,
        snapshot_values,
        write_snapshot,
    )

    settings = Settings.from_env({}, run_id="t")
    write_snapshot(
        tmp_path,
        snapshot_values(
            settings,
            branch="main",
            gate_cmd="",
            build_gate_cmd="",
            phase_gate_cmd="",
            task_arg="t",
            task_source_kind="arg",
            task_source_path="",
        ),
    )
    enable_snapshot_phases(tmp_path)
    snap = load_snapshot(tmp_path)
    assert snap["PHASES"] == "1"
    # Resume with a clean environment: no immutable-key conflict, and the
    # resumed settings run phased without being "explicit".
    check_immutable({}, snap)
    resumed = Settings.from_env({}, run_id="t", snapshot=snap)
    assert resumed.phases is True
    assert resumed.phases_explicit is False
    # A stale explicit PHASES=0 in the resume environment still conflicts.
    with pytest.raises(RunStateError, match="PHASES=0 conflicts"):
        check_immutable({"PHASES": "0"}, snap)


def test_enable_snapshot_phases_requires_a_snapshot(tmp_path):
    import pytest

    from adversarial_ai_coding.runstate import (
        RunStateError,
        enable_snapshot_phases,
    )

    with pytest.raises(RunStateError, match="cannot record the Phased ATDD") as err:
        enable_snapshot_phases(tmp_path)
    # The human just answered y, so the abort must say what happens next.
    assert "a resume runs it again and offers Phased ATDD again" in str(err.value)


def test_enable_snapshot_phases_wraps_atomic_write_failure_and_preserves_snapshot(
    tmp_path, monkeypatch
):
    from adversarial_ai_coding import runstate

    settings = Settings.from_env({}, run_id="t")
    write_snapshot(
        tmp_path,
        snapshot_values(
            settings,
            branch="main",
            gate_cmd="",
            build_gate_cmd="",
            phase_gate_cmd="",
            task_arg="t",
            task_source_kind="arg",
            task_source_path="",
        ),
    )

    def fail_atomic_write(path, text):
        raise OSError("replace denied")

    monkeypatch.setattr(runstate, "_atomic_write", fail_atomic_write)

    with pytest.raises(RunStateError, match="cannot record the Phased ATDD"):
        runstate.enable_snapshot_phases(tmp_path)

    assert load_snapshot(tmp_path)["PHASES"] == "0"
