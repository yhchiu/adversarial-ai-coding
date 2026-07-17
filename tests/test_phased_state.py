"""Phase structure persistence and per-phase task queues."""

import pytest

from adversarial_ai_coding.phases import Phase
from adversarial_ai_coding.runstate import (
    RunState,
    RunStateError,
    ensure_named_task_queue,
    ensure_phases,
    load_phases,
    phase_queue_name,
    pop_task_queue,
    remaining_tasks,
    restore_or_record_base,
    save_phases,
)

PHASES = (
    Phase(number=1, title="one", regression_guard=False, tasks=("a", "b")),
    Phase(number=2, title="two", regression_guard=True, tasks=("c",)),
)


@pytest.fixture
def state(tmp_path):
    return RunState.create(tmp_path / "state", "run", "t\n")


def test_save_and_load_phases_round_trip(state):
    save_phases(state, PHASES)
    assert load_phases(state) == PHASES


def test_load_phases_returns_none_when_absent(state):
    assert load_phases(state) is None


def test_load_phases_refuses_damaged_json(state):
    (state.state_dir / "phases.json").write_text("{oops", encoding="utf-8")
    with pytest.raises(RunStateError, match="not valid JSON"):
        load_phases(state)


def test_ensure_phases_parses_once_then_trusts_state(state, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Phase 1: one\nAcceptance: x.\n- [ ] a\n- [ ] b\n"
        "## Phase 2: two (regression-guard)\nAcceptance: y.\n- [ ] c\n",
        encoding="utf-8",
    )
    assert ensure_phases(state, plan) == PHASES
    plan.write_text("garbage, no phases\n", encoding="utf-8")
    assert ensure_phases(state, plan) == PHASES


def test_phase_queue_name_is_zero_padded():
    assert phase_queue_name(3) == "tasks-remaining-phase-03"


def test_named_queues_are_independent(state):
    ensure_named_task_queue(state, phase_queue_name(1), ["a", "b"])
    ensure_named_task_queue(state, phase_queue_name(2), ["c"])
    pop_task_queue(state, phase_queue_name(1))
    assert remaining_tasks(state, phase_queue_name(1)) == ["b"]
    assert remaining_tasks(state, phase_queue_name(2)) == ["c"]
    # ensure is idempotent: an existing (even empty) queue is kept
    pop_task_queue(state, phase_queue_name(2))
    ensure_named_task_queue(state, phase_queue_name(2), ["c"])
    assert remaining_tasks(state, phase_queue_name(2)) == []


def test_restore_or_record_base_is_per_name(state):
    shas = iter(["sha-one", "sha-two"])
    assert restore_or_record_base(state, "phase-01-test-base", lambda: next(shas)) == "sha-one"
    assert restore_or_record_base(state, "phase-02-test-base", lambda: next(shas)) == "sha-two"
    assert (
        restore_or_record_base(state, "phase-01-test-base", lambda: "never")
        == "sha-one"
    )
