"""Ports tests/helpers.test.sh:733-790 (RESUME_RUN resolution and locking)."""

import pytest

from adversarial_ai_coding.runstate import (
    RunState,
    RunStateError,
    list_run_state_ids,
    resolve_resume_id,
    write_snapshot,
)


def make_state(root, run_id, completed=False):
    d = root / run_id
    d.mkdir(parents=True)
    write_snapshot(d, {})
    if completed:
        (d / "completed").touch()
    return d


def test_path_traversal_id_is_rejected(tmp_path):
    with pytest.raises(RunStateError, match="Invalid RESUME_RUN"):
        resolve_resume_id(tmp_path, "../../x")


def test_unknown_id_fails_and_lists_available_runs(tmp_path):
    make_state(tmp_path, "aaa-run")
    with pytest.raises(RunStateError, match="aaa-run"):
        resolve_resume_id(tmp_path, "nope")


def test_completed_run_is_refused(tmp_path):
    make_state(tmp_path, "aaa-run", completed=True)
    with pytest.raises(RunStateError, match="already completed"):
        resolve_resume_id(tmp_path, "aaa-run")


def test_last_picks_newest_unfinished(tmp_path):
    make_state(tmp_path, "20260101-000000")
    make_state(tmp_path, "20260102-000000", completed=True)
    assert resolve_resume_id(tmp_path, "last") == "20260101-000000"


def test_last_with_everything_completed_fails(tmp_path):
    make_state(tmp_path, "20260101-000000", completed=True)
    with pytest.raises(RunStateError, match="no unfinished run"):
        resolve_resume_id(tmp_path, "last")


def test_list_ids_newest_first(tmp_path):
    make_state(tmp_path, "20260101-000000")
    make_state(tmp_path, "20260103-000000")
    make_state(tmp_path, "20260102-000000")
    assert list_run_state_ids(tmp_path) == [
        "20260103-000000",
        "20260102-000000",
        "20260101-000000",
    ]


def test_busy_lock_is_refused_with_removal_hint(tmp_path):
    d = make_state(tmp_path, "r4")
    (d / "lock").mkdir()
    with pytest.raises(RunStateError, match=r"rm -r .*lock"):
        RunState.resume(tmp_path, "r4")


def test_resume_acquires_and_releases_lock(tmp_path):
    make_state(tmp_path, "r1")
    state = RunState.resume(tmp_path, "r1")
    assert (state.state_dir / "lock").is_dir()
    state.release_lock()
    assert not (state.state_dir / "lock").exists()
    state.release_lock()


def test_create_claims_fresh_state(tmp_path):
    state = RunState.create(tmp_path, "run-1", "the task\n")
    assert state.task_text() == "the task\n"
    assert not state.is_completed()
    state.mark_completed()
    assert state.is_completed()


def test_create_same_second_collision_fails_clearly(tmp_path):
    RunState.create(tmp_path, "xdup", "t").release_lock()
    with pytest.raises(RunStateError, match="already exists"):
        RunState.create(tmp_path, "xdup", "t")


def test_missing_task_snapshot_is_damaged_state(tmp_path):
    make_state(tmp_path, "r1")
    state = RunState.resume(tmp_path, "r1")
    with pytest.raises(RunStateError, match="damaged"):
        state.task_text()
