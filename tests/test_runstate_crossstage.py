"""Ports helpers.test.sh:792-806, 846-856, 942-1001 (cross-stage state)."""

from adversarial_ai_coding.runstate import (
    RunState,
    ensure_task_queue,
    init_live_state,
    mark_plan_task_done,
    plan_tasks,
    pop_task_queue,
    remaining_tasks,
    restore_or_record_acceptance_base,
)


def claimed(tmp_path):
    return RunState.create(tmp_path / "state", "run", "task\n")


def test_record_stage_and_head_checkpoint(tmp_path):
    st = claimed(tmp_path)
    assert not st.stage_done("stage-one")
    st.record_stage("stage-one", "abc123")
    assert st.stage_done("stage-one")
    assert not st.stage_done("stage-two")
    assert st.read_last_head() == "abc123"
    st.record_stage("stage-two", "def456")
    assert st.completed_stages() == ["stage-one", "stage-two"]
    assert st.read_last_head() == "def456"


def test_acceptance_base_persisted_value_is_reused(tmp_path):
    st = claimed(tmp_path)
    (st.state_dir / "acceptance-test-base").write_text(
        "cafebabe\n", encoding="utf-8"
    )
    assert restore_or_record_acceptance_base(st, lambda: "NEW") == "cafebabe"


def test_acceptance_base_first_entry_records_and_persists(tmp_path):
    st = claimed(tmp_path)
    assert restore_or_record_acceptance_base(st, lambda: "headsha") == "headsha"
    raw = (st.state_dir / "acceptance-test-base").read_text(encoding="utf-8")
    assert raw.strip() == "headsha"


def test_acceptance_base_without_state_uses_head(tmp_path):
    assert restore_or_record_acceptance_base(None, lambda: "live-head") == "live-head"


def test_plan_tasks_only_unfinished(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# plan\n\n- [ ] task one\n- [x] finished task\n- [ ] task two\nplain text\n",
        encoding="utf-8",
    )
    assert plan_tasks(plan) == ["task one", "task two"]
    assert plan_tasks(tmp_path / "missing.md") == []


def test_task_queue_created_from_plan(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text(
        "- [ ] task one\n- [x] done task\n- [ ] task two\n", encoding="utf-8"
    )
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == ["task one", "task two"]


def test_task_queue_existing_not_rebuilt_and_pop(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("- [ ] task one\n", encoding="utf-8")
    (st.state_dir / "tasks-remaining").write_text(
        "custom remaining task\n", encoding="utf-8"
    )
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == ["custom remaining task"]
    pop_task_queue(st)
    assert remaining_tasks(st) == []
    # An existing EMPTY queue means all tasks committed: no fallback rebuild.
    ensure_task_queue(st, plan)
    assert remaining_tasks(st) == []


def test_task_queue_fallback_without_checkboxes(tmp_path):
    st = claimed(tmp_path)
    plan = tmp_path / "plan2.md"
    plan.write_text("prose only, no checkbox list\n", encoding="utf-8")
    ensure_task_queue(st, plan)
    tasks = remaining_tasks(st)
    assert len(tasks) == 1
    assert "Complete the full implementation" in tasks[0]
    assert "plan2.md" in tasks[0]


def test_mark_plan_task_done_exact_line_once(tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "- [ ] task one\n- [ ] task one extra\nplain line\n", encoding="utf-8"
    )
    mark_plan_task_done(plan, "task one")
    mark_plan_task_done(plan, "task one")
    assert plan.read_text(encoding="utf-8") == (
        "- [x] task one\n- [ ] task one extra\nplain line\n"
    )


def test_init_live_state_resume_keeps_durables_clears_transients(tmp_path):
    wf = tmp_path / "aac/.run"
    wf.mkdir(parents=True)
    names = [
        "suggestions.md",
        "protected-tests.txt",
        "protected-base.sha",
        "spec-merge-request.md",
        "review.md",
        "verdict.json",
        "last-agent-output.txt",
        "pr-body.md",
    ]
    for name in names:
        (wf / name).write_text("x\n", encoding="utf-8")
    init_live_state(wf, resume=True)
    for durable in [
        "suggestions.md",
        "protected-tests.txt",
        "protected-base.sha",
        "spec-merge-request.md",
    ]:
        assert (wf / durable).is_file()
    for transient in [
        "review.md",
        "verdict.json",
        "last-agent-output.txt",
        "pr-body.md",
    ]:
        assert not (wf / transient).exists()


def test_init_live_state_fresh_clears_everything(tmp_path):
    wf = tmp_path / "aac/.run"
    wf.mkdir(parents=True)
    for name in ["suggestions.md", "review.md"]:
        (wf / name).write_text("x\n", encoding="utf-8")
    init_live_state(wf, resume=False)
    assert not (wf / "suggestions.md").exists()
    assert not (wf / "review.md").exists()
