# Phased ATDD GPT Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the seven verified blockers and two suggestions from the GPT cross-review of the Phased ATDD merge (`docs/plans/20260718_phased_atdd_review_gpt56sol-max.md`), in priority order.

**Architecture:** All fixes are small, local hardening changes to the existing workflow modules: `phaseflow.py` (stage flow), `review.py` (review loop), `workflow.py` (protected controls), `runstate.py` (persistence), `phases.py` (plan parser). No new modules; each task lands with its own unit/integration tests and a commit.

**Tech Stack:** Python 3, pytest, uv. Offline tests only (fake agents); no `-m e2e` runs are required by this plan.

## Global Constraints

- Conventional Commit format; simple English subject; detailed body; **no `Co-Authored-By` trailer** (user rule).
- Commit each task separately, only after its tests pass.
- On this Windows machine, clear the poisoned system env before running uv (memory: `windows-python-env-quirk`). In PowerShell: `$env:PYTHONHOME=''; $env:PYTHONPATH=''` before the first `uv run`.
- Run tests with `uv run pytest <path> -v`. The default suite deselects `-m e2e`. Full-suite baseline before this plan: 560 passed.
- Do not change prompt-template file names; `tests/test_prompts.py` pins the placeholder inventory.
- Priority order (from the verified review): blockers 1, 2, 4, 5 then 3, 6, 7, then suggestion S2. Suggestion S1's core fault-injection cases (the write-tests boundary windows that produce real defects) are folded into Task 4; S1's remaining windows (after archive snapshots, after each task commit/queue pop, after a phase-gate repair) are deliberately out of scope — no known defect sits behind them, and the resume machinery they exercise is already covered by the existing resume suite. Revisit only if a real resume bug appears there.

---

### Task 1: Commit phase-gate repairs before the stage ends (blocker 1)

With `PHASE_REVIEW=0` (the **default**), a repair made inside the final phase gate stays uncommitted; the next phase's test commit sweeps it in and `record_protected_tests` then protects product code as if it were a test.

**Files:**
- Modify: `src/adversarial_ai_coding/phaseflow.py` (after the phase-gate `gate_loop_ref`, currently lines 228-237)
- Test: `tests/test_phaseflow.py` (extend `test_run_phased_stages_drives_phases_in_order`)
- Test: `tests/test_phased_integration.py` (new integration test)

**Interfaces:**
- Consumes: existing `wf.commit_if_dirty(ctx, agent, description)`.
- Produces: a `Phase {N} gate repairs` commit whenever the phase gate loop leaves the tree dirty. Later tasks rely on the tree being clean when a `phase-NN-write-tests` stage starts.

- [ ] **Step 1: Extend the unit test to expect the new commit event**

In `tests/test_phaseflow.py`, `test_run_phased_stages_drives_phases_in_order` already collects `("dirty", slot, description)` events from a monkeypatched `commit_if_dirty`. Add at the end of the test:

```python
    dirty_commits = [event for event in events if event[0] == "dirty"]
    assert ("dirty", "I", "Phase 1 gate repairs") in dirty_commits
    assert ("dirty", "I", "Phase 2 gate repairs") in dirty_commits
```

- [ ] **Step 2: Add the integration leak test**

Append to `tests/test_phased_integration.py` (module already imports `sys`, `driver_workdir`, `phased_env`, `run_cli`, `state_dir_of`):

```python
def test_phase_gate_repair_is_committed_not_leaked(new_repo, tmp_path, monkeypatch):
    """A dirty phase-gate repair must not ride into the next phase's
    protected-test commit (GPT review blocker 1)."""
    work = driver_workdir(tmp_path)
    work.mkdir()
    # Gate: red while src.txt is missing; then fails once while dropping a
    # dirty repair file into the workspace (simulating an uncommitted fix
    # made during the gate loop); passes afterwards.
    (work / "flaky_gate.py").write_text(
        "import pathlib, sys\n"
        "if not pathlib.Path('src.txt').exists():\n"
        "    sys.exit(1)\n"
        "if not pathlib.Path('repair.txt').exists():\n"
        "    pathlib.Path('repair.txt').write_text('dirty repair\\n')\n"
        "    sys.exit(1)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    env = phased_env(
        work,
        PHASE_GATE_CMD=f'"{sys.executable}" "{work / "flaky_gate.py"}"',
    )
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 0
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `uv run pytest tests/test_phaseflow.py::test_run_phased_stages_drives_phases_in_order tests/test_phased_integration.py::test_phase_gate_repair_is_committed_not_leaked -v`
Expected: both FAIL (no `gate repairs` dirty event; `repair.txt` appears in protected-tests.txt).

- [ ] **Step 4: Implement the fix**

In `src/adversarial_ai_coding/phaseflow.py`, directly after the phase-gate `wf.gate_loop_ref(...)` call (the one passing `phase_gate`) and **before** `if ctx.settings.phase_review:`, insert:

```python
            wf.commit_if_dirty(
                ctx, impl, f"Phase {phase.number} gate repairs"
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_phaseflow.py tests/test_phased_integration.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/phaseflow.py tests/test_phaseflow.py tests/test_phased_integration.py
git commit -m "fix(phaseflow): commit phase-gate repairs before the stage ends" -m "With PHASE_REVIEW=0 (the default) a repair made during the final phase
gate stayed uncommitted. The next phase's test commit then swept it in,
and record_protected_tests protected product code as if it were an
acceptance test. Commit dirty gate repairs with the implementation agent
right after the phase gate passes, before the stage ends.

Found by the GPT cross-review (blocker 1,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 2: Keep review.md across rounds so worker replies are verified (blocker 2)

`run_review` truncates `review.md` at the start of every round, but the review prompt requires the reviewer to verify worker replies from the previous round. Move the clean-slate reset to the start of `review_loop`; within a loop, preserve readable content between rounds.

**Files:**
- Modify: `src/adversarial_ai_coding/review.py:144-147` (`run_review` pre-create) and `review_loop` (line 222)
- Test: `tests/test_review.py`

**Interfaces:**
- Produces: `_reset_review_file(ctx)` — module-private helper; recovers an unreadable `review.md` then truncates it. Called once per `review_loop`. Task 5 builds on the resulting `run_review` shape.
- Unchanged: `run_review(ctx, agent, scope) -> bool`, `review_loop(ctx, reviewer, worker, scope, gate_cmd="")`. `dual_spec.run_candidate_spec_review` unlinks both files before calling `run_review` and keeps working (missing file → recreated empty).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_review.py`:

```python
def test_review_round_two_sees_worker_replies(make_ctx, monkeypatch):
    """Round N must read the findings and replies from round N-1
    (GPT review blocker 2)."""
    ctx = make_ctx()
    seen = {}

    def rejecting_reviewer(name, prompt, settings, session, io):
        ctx.review_path.write_text("- Blocker: bad name\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":false,"blockers":["bad name"],"suggestions":[]}',
            encoding="utf-8",
        )
        io.agent_out.write_text("rejected\n", encoding="utf-8")
        return AgentResult(0, "rejected")

    def verifying_reviewer(name, prompt, settings, session, io):
        seen["review"] = ctx.review_path.read_text(encoding="utf-8")
        ctx.review_path.write_text("approved\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
        )
        io.agent_out.write_text("approved\n", encoding="utf-8")
        return AgentResult(0, "approved")

    reviewers = iter([rejecting_reviewer, verifying_reviewer])
    monkeypatch.setattr(
        review_mod, "run_reviewer", lambda *args: next(reviewers)(*args)
    )

    def worker_reply(ctx_arg, agent, prompt):
        with ctx.review_path.open("a", encoding="utf-8") as review:
            review.write("Disagree: the name matches the spec\n")

    monkeypatch.setattr(review_mod, "work", worker_reply)
    monkeypatch.setattr(review_mod, "gate_loop", lambda cmd, **kwargs: None)
    review_loop(ctx, ctx.ref("B"), ctx.ref("A"), "scope")
    assert "- Blocker: bad name" in seen["review"]
    assert "Disagree: the name matches the spec" in seen["review"]


def test_review_loop_starts_with_a_clean_review_file(make_ctx, monkeypatch):
    """Stale content from another stage must not leak into a new loop."""
    ctx = make_ctx()
    ctx.review_path.write_text(
        "stale replies from another stage\n", encoding="utf-8"
    )
    seen = {}

    def reviewer(name, prompt, settings, session, io):
        seen["review"] = ctx.review_path.read_text(encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
        )
        io.agent_out.write_text("ok\n", encoding="utf-8")
        return AgentResult(0, "ok")

    monkeypatch.setattr(review_mod, "run_reviewer", reviewer)
    review_loop(ctx, ctx.ref("B"), ctx.ref("A"), "scope")
    assert seen["review"] == ""
```

- [ ] **Step 2: Run them to verify the first fails**

Run: `uv run pytest tests/test_review.py::test_review_round_two_sees_worker_replies tests/test_review.py::test_review_loop_starts_with_a_clean_review_file -v`
Expected: `test_review_round_two_sees_worker_replies` FAILS (round 2 sees an empty file); the clean-slate test passes today and pins current behavior.

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/review.py`:

(a) After `_recover_unreadable_output`, add:

```python
def _reset_review_file(ctx: WorkflowContext) -> None:
    """Give a new review loop a clean review.md under the parent identity."""

    _recover_unreadable_output(ctx, ctx.review_path, REVIEW_UNREADABLE_STUB)
    ctx.review_path.write_text("", encoding="utf-8")
```

(b) In `run_review`, replace:

```python
    # Pre-create reviewer outputs under the parent workflow identity. On
    # Windows, a sandboxed reviewer can otherwise create review.md with an
    # owner ACL that prevents the parent workflow from reading it afterward.
    ctx.review_path.write_text("", encoding="utf-8")
```

with:

```python
    # Ensure reviewer outputs exist under the parent workflow identity (on
    # Windows a sandboxed reviewer can otherwise create review.md with an
    # ACL the workflow cannot read back), but keep readable content: round
    # N must see the worker replies written after round N-1.
    _recover_unreadable_output(ctx, ctx.review_path, REVIEW_UNREADABLE_STUB)
    if not ctx.review_path.is_file():
        ctx.review_path.write_text("", encoding="utf-8")
```

(c) In `review_loop`, after `ctx.cur_round = 1`, add:

```python
    _reset_review_file(ctx)
```

- [ ] **Step 4: Run the review suite**

Run: `uv run pytest tests/test_review.py tests/test_dual_spec.py -v`
Expected: all PASS (including the pre-existing `test_run_review_precreates_reviewer_output_files`, which starts with no file and still gets an empty one).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/review.py tests/test_review.py
git commit -m "fix(review): keep review.md across rounds so replies are verified" -m "run_review truncated review.md at the start of every round, but the
review prompt tells the reviewer to verify worker replies from the
previous round; the replies were always gone. Reset the file once per
review loop instead, and only recover-or-create it per round so round N
can adjudicate round N-1's Disagree replies.

Found by the GPT cross-review (blocker 2,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 3: Re-review phase tests repaired inside the red check (blocker 4)

Repairs B makes inside `red_check` bypass A's acceptance-test review and are committed as soon as the exit code looks right; B can even delete failing regression-guard tests. Combine review and red check into one bounded loop.

**Files:**
- Modify: `src/adversarial_ai_coding/phaseflow.py` (`red_check`, and the call site in `run_phased_stages`)
- Test: `tests/test_phaseflow.py`

**Interfaces:**
- Changes: `red_check(ctx, phase, cmd) -> bool` — now returns True when the test author had to repair the tests (repair invalidates the earlier review). Existing callers that ignore the return keep working.
- Produces: `reviewed_red_check(ctx, phase, cmd, scope) -> None` — loops `review_loop_ref` + `red_check` until a candidate passes the check with no repair; aborts after `ctx.settings.max_rounds` repaired rounds. `run_phased_stages` calls this instead of the separate review + check.

- [ ] **Step 1: Write the failing tests**

In `tests/test_phaseflow.py` (module already imports `pytest`, `gates`, `wf_mod`, `phaseflow`, `WorkflowAbort`, `NORMAL`), append:

```python
def test_reviewed_red_check_rereviews_after_repair(make_ctx, monkeypatch):
    """A red-check repair invalidates A's approval: review must run again
    before the candidate is accepted (GPT review blocker 4)."""
    ctx = make_ctx()
    results = iter([(0, "green"), (1, "red"), (1, "red")])
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(results))
    events = []
    monkeypatch.setattr(
        wf_mod, "work", lambda ctx_arg, agent, prompt: events.append("repair")
    )
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": events.append(
            "review"
        ),
    )
    phaseflow.reviewed_red_check(ctx, NORMAL, "gate", "scope")
    assert events == ["review", "repair", "review"]


def test_reviewed_red_check_aborts_when_repairs_never_settle(
    make_ctx, monkeypatch
):
    ctx = make_ctx()
    results = iter([(0, "green"), (1, "red")] * 40)
    monkeypatch.setattr(gates, "run_shell", lambda cmd, cwd: next(results))
    monkeypatch.setattr(wf_mod, "work", lambda *args: None)
    monkeypatch.setattr(
        wf_mod,
        "review_loop_ref",
        lambda ctx_arg, reviewer, worker, scope, gate_cmd="": None,
    )
    notices = []
    monkeypatch.setattr(ctx, "notify", notices.append)
    with pytest.raises(WorkflowAbort, match="repaired inside the red check"):
        phaseflow.reviewed_red_check(ctx, NORMAL, "gate", "scope")
    assert notices
```

Also update the existing red-check tests to pin the new return value:
- `test_red_check_passes_when_normal_phase_is_red`: change the call line to `assert phaseflow.red_check(ctx, NORMAL, "gate") is False`
- `test_red_check_passes_when_guard_phase_is_green`: `assert phaseflow.red_check(ctx, GUARD, "gate") is False`
- `test_red_check_repairs_with_test_author_then_passes`: `assert phaseflow.red_check(ctx, NORMAL, "gate") is True`
- `test_red_check_skips_without_command`: `assert phaseflow.red_check(ctx, NORMAL, "") is False`

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_phaseflow.py -v`
Expected: the two new tests ERROR (`reviewed_red_check` missing); the updated return-value asserts FAIL (`red_check` returns None).

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/phaseflow.py`:

(a) `red_check` — change the signature/docstring, return `False` at the no-command early return, track repairs, and return `repaired` on success:

```python
def red_check(ctx: wf.WorkflowContext, phase: Phase, cmd: str) -> bool:
    """TDD-red gate run by the workflow, never trusted from AI output.

    Returns True when the test author had to repair the tests to reach the
    expected result; the repair invalidates the earlier test review, so the
    caller must run the review again before accepting the candidate.
    """

    from .gates import run_shell

    if not cmd:
        ctx.echo_err(
            "(warning: no phase gate command; the red check is skipped. Set "
            "PHASE_GATE_CMD or GATE_CMD to enable it.)"
        )
        return False
    attempt = 1
    repaired = False
    while True:
```

and inside the loop, `if ok:` becomes:

```python
        if ok:
            ctx.log("Phase red check passed")
            return repaired
```

and right after `attempt += 1` add `repaired = True` (the rest of the repair branch is unchanged).

(b) Add `reviewed_red_check` after `red_check`:

```python
def reviewed_red_check(
    ctx: wf.WorkflowContext, phase: Phase, cmd: str, scope: str
) -> None:
    """One combined gate: A's test review plus the deterministic red check.

    Any repair B makes inside the red check bypassed A's approval, so loop
    review -> red check until a candidate passes the check with no repair.
    """

    attempt = 1
    while True:
        wf.review_loop_ref(
            ctx,
            ctx.spec_roles.owner_agent,
            ctx.spec_roles.reviewer_agent,
            scope,
        )
        if not red_check(ctx, phase, cmd):
            return
        if attempt >= ctx.settings.max_rounds:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] phase tests kept "
                "needing red-check repairs after review; human intervention "
                "required"
            )
            raise WorkflowAbort(
                f"!! [{ctx.cur_stage}] Phase tests were repaired inside the "
                f"red check {ctx.settings.max_rounds} times without a "
                "reviewed candidate; stopping for human intervention."
            )
        attempt += 1
        ctx.log(
            "Red-check repairs changed the phase tests; running the "
            "acceptance-test review again"
        )
```

(c) In `run_phased_stages`, replace the back-to-back review + check:

```python
            wf.review_loop_ref(
                ctx,
                ctx.spec_roles.owner_agent,
                ctx.spec_roles.reviewer_agent,
                scope,
            )
            red_check(ctx, phase, phase_gate)
```

with:

```python
            reviewed_red_check(ctx, phase, phase_gate, scope)
```

- [ ] **Step 4: Run the phaseflow and integration suites**

Run: `uv run pytest tests/test_phaseflow.py tests/test_phased_integration.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/phaseflow.py tests/test_phaseflow.py
git commit -m "fix(phaseflow): re-review phase tests repaired inside the red check" -m "Repairs made by the test author inside red_check bypassed the
acceptance-test review: once the command had the expected exit status
the tests were committed, so B could delete failing regression-guard
tests or force a fake red. red_check now reports whether it repaired,
and reviewed_red_check loops review -> red check until a candidate
passes the deterministic check with no unreviewed repair, bounded by
MAX_ROUNDS.

Found by the GPT cross-review (blocker 4,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 4: Make protected recording crash-consistent on resume (blocker 5 + suggestion S1 core)

Two changes: (a) write `protected-base.sha` **before** `protected-tests.txt`, both atomically — an interrupt then leaves `{fresh base, stale list}`, which never misreads committed phase tests as tampering (the old order left `{fresh list, stale base}`, which did); (b) a durable per-phase `controls-recorded` checkpoint so a resume after `record_protected_tests` but before `end_stage` finishes the stage without re-running the test writer.

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py` (`record_protected_tests`, lines 289-293)
- Modify: `src/adversarial_ai_coding/runstate.py` (new checkpoint helpers near `restore_or_record_base`)
- Modify: `src/adversarial_ai_coding/phaseflow.py` (write-tests stage body)
- Test: `tests/test_protected_recording.py`, `tests/test_phased_integration.py`

**Interfaces:**
- Produces (runstate): `checkpoint_done(state: RunState, name: str) -> bool` and `record_checkpoint(state: RunState, name: str) -> None`; checkpoint files live in `state.state_dir` (durable across resume, like the task queues).
- Consumes: `runstate._atomic_write(path, text)` (existing temp-then-`os.replace` helper).
- Checkpoint name used by phaseflow: `f"phase-{NN}-controls-recorded"`.

- [ ] **Step 1: Write the failing unit test (write ordering)**

In `tests/test_protected_recording.py`, add `import pytest` to the imports, then append:

```python
def test_interrupt_between_control_writes_is_benign(
    make_ctx, new_repo, monkeypatch
):
    """A crash between the two control writes must leave {fresh base,
    stale list}: that pair never flags committed phase tests as tampering
    (GPT review blocker 5)."""
    from adversarial_ai_coding import runstate
    from adversarial_ai_coding.gitops import protected_violations

    ctx = make_ctx()
    (ctx.wf / ".gitignore").write_text("*\n", encoding="utf-8")
    base_one = head_sha(new_repo)
    _commit_file(new_repo, "test_one.py", "phase 1 tests")
    record_protected_tests(ctx, base_one)

    base_two = head_sha(new_repo)
    _commit_file(new_repo, "test_two.py", "phase 2 tests")
    real_write = runstate._atomic_write

    def failing_list_write(path, text):
        if path.name == "protected-tests.txt":
            raise OSError("injected crash before the list write")
        real_write(path, text)

    monkeypatch.setattr(runstate, "_atomic_write", failing_list_write)
    with pytest.raises(OSError):
        record_protected_tests(ctx, base_two, append=True)

    protected = (ctx.wf / "protected-tests.txt").read_text(encoding="utf-8")
    base = (ctx.wf / "protected-base.sha").read_text(encoding="utf-8").strip()
    assert protected == "test_one.py\n"
    assert base == head_sha(new_repo)
    assert protected_violations({"test_one.py"}, base, new_repo) == []
```

- [ ] **Step 2: Write the failing integration test (resume after record, before end_stage)**

Append to `tests/test_phased_integration.py`:

```python
def test_resume_after_controls_recorded_before_stage_end(
    new_repo, tmp_path, monkeypatch
):
    """Crash window: phase-2 tests committed and controls recorded, but
    phase-02-write-tests not yet in the ledger. Resume must finish the
    stage without re-running the test writer (GPT review blocker 5)."""
    from adversarial_ai_coding import workflow as wf_mod
    from adversarial_ai_coding.config import WorkflowAbort

    work = driver_workdir(tmp_path)
    work.mkdir()
    env = phased_env(work)
    real_end = wf_mod.end_stage
    injected = []

    def crashing_end_stage(ctx):
        if ctx.cur_stage == "phase-02-write-tests" and not injected:
            injected.append(True)
            raise WorkflowAbort("injected: crash before the ledger write")
        real_end(ctx)

    monkeypatch.setattr(wf_mod, "end_stage", crashing_end_stage)
    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 1
    state = state_dir_of(new_repo)
    st = RunState(state_dir=state, run_id=state.name)
    assert "phase-02-write-tests" not in st.completed_stages()

    rc = run_cli(
        new_repo,
        dict(env, RESUME_RUN=state.name),
        args=[],
        monkeypatch=monkeypatch,
    )
    assert rc == 0
    # The checkpoint keeps the resume from re-asking B for phase-2 tests
    # (the fake writes identical content, so tampering would not fire here;
    # a real writer varies content and exhausts the recovery loop).
    assert calls(work, "fake-reviewer write-phase-tests") == 2
    protected = (new_repo / ".workflow" / "protected-tests.txt").read_text(
        encoding="utf-8"
    )
    assert protected == "acc/feature-works.txt\nacc/old-behavior-unchanged.txt\n"
```

- [ ] **Step 3: Run both to verify they fail**

Run: `uv run pytest tests/test_protected_recording.py::test_interrupt_between_control_writes_is_benign tests/test_phased_integration.py::test_resume_after_controls_recorded_before_stage_end -v`
Expected: ordering test FAILS (list written before base today, and writes are not atomic); resume test FAILS (`write-phase-tests` called 3 times on resume).

- [ ] **Step 4: Implement the ordered atomic writes**

In `src/adversarial_ai_coding/workflow.py`, `record_protected_tests`: extend the function-top import line `from .gitops import git_out, head_sha` with a second line `from .runstate import _atomic_write`, then replace:

```python
    merged = existing + [name for name in names if name not in existing]
    protected_list.write_text(
        "".join(name + "\n" for name in merged), encoding="utf-8"
    )
    protected_base.write_text(head_sha(ctx.workspace) + "\n", encoding="utf-8")
```

with:

```python
    merged = existing + [name for name in names if name not in existing]
    # Base first, atomically: an interrupt then leaves {fresh base, stale
    # list}, which flags nothing. The old order left {fresh list, stale
    # base} and misread already-committed phase tests as tampering.
    _atomic_write(protected_base, head_sha(ctx.workspace) + "\n")
    _atomic_write(protected_list, "".join(name + "\n" for name in merged))
```

- [ ] **Step 5: Implement the checkpoint helpers**

In `src/adversarial_ai_coding/runstate.py`, after `restore_or_record_acceptance_base`, add:

```python
def checkpoint_done(state: RunState, name: str) -> bool:
    return (state.state_dir / name).is_file()


def record_checkpoint(state: RunState, name: str) -> None:
    # Durable sub-stage marker: survives resume like the task queues.
    _atomic_write(state.state_dir / name, "done\n")
```

- [ ] **Step 6: Wire the checkpoint into the write-tests stage**

In `src/adversarial_ai_coding/phaseflow.py`, add `checkpoint_done` and `record_checkpoint` to the `from .runstate import (...)` list in `run_phased_stages`. Then wrap the write-tests stage body (the inner statements are the existing ones — including Task 3's `reviewed_red_check` — re-indented one level under `else:`, content unchanged):

```python
        if wf.begin_stage(ctx, f"{label}-write-tests", protected_list, protected_base):
            controls_checkpoint = f"{label}-controls-recorded"
            if checkpoint_done(ctx.state, controls_checkpoint):
                ctx.log(
                    f"== [{label}-write-tests] protected controls already "
                    "recorded; finishing the interrupted stage"
                )
            else:
                test_base = restore_or_record_base(
                    ctx.state, base_name, lambda: head_sha(ctx.workspace)
                )
                wf.work(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    render_prompt(
                        ctx.prompts_dir,
                        "write-phase-tests",
                        {
                            "SPEC_FILE": str(spec_file),
                            "PLAN_FILE": str(plan_file),
                            "SPEC_DIR": str(ctx.spec_dir),
                            "PHASE_TITLE": phase.title,
                            "PHASES_DONE": ", ".join(done_titles) or "none",
                            "PROTECTED_TESTS_FILE": str(protected_list),
                        },
                    ),
                )
                scope = render_prompt(
                    ctx.prompts_dir,
                    "review-scope-acceptance-tests",
                    {"TEST_BASE": test_base, "SPEC_FILE": str(spec_file)},
                )
                reviewed_red_check(ctx, phase, phase_gate, scope)
                wf.commit_work(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    f"Phase {phase.number} acceptance tests",
                )
                wf.record_protected_tests(ctx, test_base, append=True)
                record_checkpoint(ctx.state, controls_checkpoint)
            wf.end_stage(ctx)
```

- [ ] **Step 7: Run the affected suites**

Run: `uv run pytest tests/test_protected_recording.py tests/test_phased_integration.py tests/test_phaseflow.py tests/test_phased_state.py -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py src/adversarial_ai_coding/runstate.py src/adversarial_ai_coding/phaseflow.py tests/test_protected_recording.py tests/test_phased_integration.py
git commit -m "fix(workflow): make protected recording crash-consistent on resume" -m "record_protected_tests advanced the two protected controls and the
stage ledger as separate plain writes. An interrupt between them left a
mixed pair that misclassified committed phase tests as tampering, or a
resume that re-ran the test writer against controls that already
protect the new tests.

Write protected-base.sha first and both controls atomically (an
interrupt now leaves the benign fresh-base/stale-list pair), and record
a durable per-phase controls-recorded checkpoint so a resume after
recording finishes the stage without re-running the test writer. Adds
fault-injection coverage for both windows (review suggestion 1).

Found by the GPT cross-review (blocker 5,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 5: Fail the round when review.md is unreadable or missing (blocker 3)

`_recover_unreadable_output`'s docstring promises fail-closed, but only `verdict.json` actually fails the round; a discarded `review.md` still approves. Require both readable outputs before approval. (Builds on Task 2's `run_review` shape.)

**Files:**
- Modify: `src/adversarial_ai_coding/review.py` (`_recover_unreadable_output`, `run_review` tail)
- Test: `tests/test_review.py`

**Interfaces:**
- Changes: `_recover_unreadable_output(ctx, path, fallback) -> bool` — True when the file was unreadable and replaced; False when readable or missing. `run_review` still returns bool; a round with an unreadable or missing `review.md` now returns False even with an approving verdict.

- [ ] **Step 1: Update the pinned test and add the deletion test**

In `tests/test_review.py`, rewrite `test_run_review_recovers_unreadable_review_md` as:

```python
def test_run_review_fails_when_review_md_unreadable(make_ctx, monkeypatch):
    """An approved verdict with a discarded review body must not end the
    loop (GPT review blocker 3)."""
    ctx = make_ctx()
    warnings = []
    ctx.echo_err = warnings.append
    monkeypatch.setattr(review_mod, "run_reviewer", approving_reviewer())
    monkeypatch.setattr(review_mod, "_read_probe", failing_probe("review.md"))
    assert run_review(ctx, ctx.ref("B"), "scope") is False
    assert "unreadable" in ctx.review_path.read_text(encoding="utf-8")
    assert any("review.md is unreadable" in line for line in warnings)
```

and append:

```python
def test_run_review_fails_when_reviewer_deletes_review_md(make_ctx, monkeypatch):
    ctx = make_ctx()

    def deleting_reviewer(name, prompt, settings, session, io):
        io.agent_out.write_text("reviewed\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8"
        )
        ctx.review_path.unlink()
        return AgentResult(0, "review text")

    monkeypatch.setattr(review_mod, "run_reviewer", deleting_reviewer)
    assert run_review(ctx, ctx.ref("B"), "scope") is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_review.py -v -k "unreadable or deletes"`
Expected: both new/updated tests FAIL (`run_review` returns True today).

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/review.py`:

(a) `_recover_unreadable_output` returns whether it replaced the file:

```python
def _recover_unreadable_output(
    ctx: WorkflowContext, path: Path, fallback: str
) -> bool:
```

with `return False` for the readable and `FileNotFoundError` paths, and `return True` at the end after the successful `path.write_text(fallback, ...)`. Extend the docstring's last sentence to: "fail closed: discard the poisoned file, restore a safe fallback, and report the loss so the round is treated as failed."

(b) In `run_review`, replace the recovery tail:

```python
    _recover_unreadable_output(ctx, ctx.verdict_path, FAILED_VERDICT)
    _recover_unreadable_output(ctx, ctx.review_path, REVIEW_UNREADABLE_STUB)
    if not ctx.verdict_path.is_file():
        ctx.echo_err("(reviewer did not write verdict.json; treating as failed)")
        return False
    if ctx.collect_review_suggestions:
        collect_suggestions(ctx)
```

with:

```python
    _recover_unreadable_output(ctx, ctx.verdict_path, FAILED_VERDICT)
    review_unreadable = _recover_unreadable_output(
        ctx, ctx.review_path, REVIEW_UNREADABLE_STUB
    )
    if not ctx.verdict_path.is_file():
        ctx.echo_err("(reviewer did not write verdict.json; treating as failed)")
        return False
    review_missing = not ctx.review_path.is_file()
    if ctx.collect_review_suggestions and not (review_unreadable or review_missing):
        collect_suggestions(ctx)
```

and after the two `archive_snapshot` calls (so the stub is still archived), before the `verdict_approved` check, add:

```python
    if review_unreadable or review_missing:
        ctx.echo_err(
            "(reviewer review.md was unreadable or missing; treating the "
            "round as failed)"
        )
        return False
```

- [ ] **Step 4: Run the review and dual-spec suites**

Run: `uv run pytest tests/test_review.py tests/test_dual_spec.py -v`
Expected: all PASS (`run_candidate_spec_review` already tolerates a failed round and regenerates missing files).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/review.py tests/test_review.py
git commit -m "fix(review): fail the round when review.md is unreadable or missing" -m "Recovery replaced an unreadable review.md with a stub but still let an
approving verdict.json end the loop, contradicting the helper's own
fail-closed contract; deleting review.md approved the same way.
_recover_unreadable_output now reports the loss and run_review requires
both readable output files before approval; suggestions are not
collected from a round whose body was discarded.

Found by the GPT cross-review (blocker 3,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 6: Validate persisted phases without coercion (blocker 6)

`load_phases` coerces persisted values (`bool("false")` is True; `"abc"` iterates into three tasks; `"phases": []` skips every phase). Validate the payload and raise `RunStateError` on any violation.

**Files:**
- Modify: `src/adversarial_ai_coding/runstate.py` (`load_phases`, new `_validated_phase` helper)
- Test: `tests/test_phased_state.py`

**Interfaces:**
- Unchanged signature: `load_phases(state) -> tuple[Phase, ...] | None`. New behavior: strict types — sequential ints (bool rejected), non-empty stripped-truthy str title, real bool guard, non-empty list of non-empty str tasks, non-empty phases list.

- [ ] **Step 1: Write the failing tests**

In `tests/test_phased_state.py`, add `import json` to the imports, change `test_load_phases_refuses_malformed_entry`'s match to `"title must be a non-empty string"`, and append:

```python
def _write_phases(state, phases):
    (state.state_dir / "phases.json").write_text(
        json.dumps({"schema": 1, "phases": phases}), encoding="utf-8"
    )


def test_load_phases_rejects_string_regression_guard(state):
    _write_phases(
        state,
        [{"number": 1, "title": "one", "regression_guard": "false", "tasks": ["a"]}],
    )
    with pytest.raises(RunStateError, match="regression_guard must be a boolean"):
        load_phases(state)


def test_load_phases_rejects_string_tasks(state):
    _write_phases(
        state,
        [{"number": 1, "title": "one", "regression_guard": False, "tasks": "abc"}],
    )
    with pytest.raises(RunStateError, match="tasks must be a non-empty list"):
        load_phases(state)


def test_load_phases_rejects_empty_phase_list(state):
    _write_phases(state, [])
    with pytest.raises(RunStateError, match="non-empty list"):
        load_phases(state)


def test_load_phases_rejects_non_sequential_numbers(state):
    _write_phases(
        state,
        [{"number": 2, "title": "two", "regression_guard": False, "tasks": ["a"]}],
    )
    with pytest.raises(RunStateError, match="phase number must be 1"):
        load_phases(state)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_phased_state.py -v`
Expected: the four new tests FAIL (values load via coercion today); the updated malformed-entry match FAILS.

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/runstate.py`, before `load_phases`, add:

```python
def _validated_phase(path: Path, entry: object, expected_number: int) -> Phase:
    def bad(reason: str) -> RunStateError:
        return RunStateError(
            f"!! {path}: {reason}; the state may be damaged. Start a fresh run."
        )

    if not isinstance(entry, dict):
        raise bad("phase entry is not an object")
    number = entry.get("number")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number != expected_number
    ):
        raise bad(f"phase number must be {expected_number}")
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise bad(f"phase {expected_number} title must be a non-empty string")
    guard = entry.get("regression_guard")
    if not isinstance(guard, bool):
        raise bad(f"phase {expected_number} regression_guard must be a boolean")
    tasks = entry.get("tasks")
    if (
        not isinstance(tasks, list)
        or not tasks
        or not all(isinstance(task, str) and task.strip() for task in tasks)
    ):
        raise bad(
            f"phase {expected_number} tasks must be a non-empty list of "
            "non-empty strings"
        )
    return Phase(
        number=number, title=title, regression_guard=guard, tasks=tuple(tasks)
    )
```

and replace the `try: return tuple(...) except (KeyError, TypeError, ValueError): ...` block in `load_phases` with:

```python
    entries = payload.get("phases")
    if not isinstance(entries, list) or not entries:
        raise RunStateError(
            f"!! {path}: phases must be a non-empty list; the state may be "
            "damaged. Start a fresh run."
        )
    return tuple(
        _validated_phase(path, entry, index + 1)
        for index, entry in enumerate(entries)
    )
```

- [ ] **Step 4: Run the state suites**

Run: `uv run pytest tests/test_phased_state.py tests/test_phased_integration.py -v`
Expected: all PASS (round-trip payloads from `save_phases` are valid by construction).

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/runstate.py tests/test_phased_state.py
git commit -m "fix(runstate): validate persisted phases without coercion" -m "load_phases coerced damaged phases.json values into different control
flow: a string regression_guard became True, a string task list became
per-character tasks, and an empty phases list silently skipped every
phase. Validate the complete payload (sequential int numbers, non-empty
title and tasks, real bool guard, non-empty phase list) and raise
RunStateError on any violation, keeping the trust-persisted-state rule
for valid payloads.

Found by the GPT cross-review (blocker 6,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 7: Reject empty acceptance text and blank tasks in the plan parser (blocker 7)

`parse_phases` records only the presence of the `Acceptance:` prefix and the task marker; `Acceptance:` with no text and `- [ ] ` with blank text pass, producing a phase with no implementation work.

**Files:**
- Modify: `src/adversarial_ai_coding/phases.py:79-88`
- Test: `tests/test_phases.py`

**Interfaces:**
- Unchanged: `parse_phases(plan_path) -> tuple[Phase, ...]`, raising `PhasePlanError` with per-problem lines. New problems: `Phase N has an empty 'Acceptance:' line`, `Phase N has an empty '- [ ] ' task`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_phases.py`:

```python
def test_empty_acceptance_text_is_a_problem(tmp_path):
    text = "## Phase 1: x\nAcceptance:\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="empty 'Acceptance:' line"):
        parse_phases(_write(tmp_path, text))


def test_blank_task_text_is_a_problem(tmp_path):
    text = "## Phase 1: x\nAcceptance: y.\n- [ ] \n"
    with pytest.raises(PhasePlanError, match="empty '- \\[ \\] ' task"):
        parse_phases(_write(tmp_path, text))


def test_whitespace_only_task_text_is_a_problem(tmp_path):
    text = "## Phase 1: x\nAcceptance: y.\n- [ ]   \n"
    with pytest.raises(PhasePlanError, match="empty '- \\[ \\] ' task"):
        parse_phases(_write(tmp_path, text))
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_phases.py -v`
Expected: the three new tests FAIL (both shapes parse successfully today).

- [ ] **Step 3: Implement**

In `src/adversarial_ai_coding/phases.py`, replace the task and acceptance branches of the parse loop:

```python
        if line.startswith(TASK_PREFIX):
            text = line[len(TASK_PREFIX) :]
            if current is None:
                problems.append(f"task outside any phase: {text}")
            elif not text.strip():
                problems.append(
                    f"Phase {current['number']} has an empty '- [ ] ' task"
                )
            else:
                current["tasks"].append(text)
            continue
        if line.startswith("Acceptance:") and current is not None:
            if line[len("Acceptance:") :].strip():
                current["acceptance"] = True
            else:
                problems.append(
                    f"Phase {current['number']} has an empty 'Acceptance:' line"
                )
```

(A phase whose only task line is blank now also reports "has no '- [ ] ' task" from `close()`; both problems are true and the owner repair prompt lists them all.)

- [ ] **Step 4: Run the parser and phaseflow suites**

Run: `uv run pytest tests/test_phases.py tests/test_phaseflow.py tests/test_phased_integration.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/phases.py tests/test_phases.py
git commit -m "fix(phases): reject empty acceptance text and blank tasks" -m "parse_phases only checked for the presence of the Acceptance: prefix
and the task marker, so 'Acceptance:' with no text and '- [ ] ' with
blank text passed the structure gate. The blank task was later filtered
out by remaining_tasks, leaving a phase with no implementation work.
Require non-whitespace text after both markers and report each as a
structure problem for the owner to repair.

Found by the GPT cross-review (blocker 7,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 8: Give each live E2E test a unique workspace under E2E_DIR (suggestion S2)

`e2e_base` returns the exact `E2E_DIR` for every caller, so running both live tests with one override makes the second `make_fixture_repo` fail on the existing `<E2E_DIR>/repo`.

**Files:**
- Modify: `tests/e2e/test_e2e.py:45-48` (`e2e_base`)

**Interfaces:**
- Unchanged signature: `e2e_base(prefix: str) -> Path`. New behavior: with `E2E_DIR` set, returns a fresh `mkdtemp` child under it instead of the directory itself.

- [ ] **Step 1: Implement**

In `tests/e2e/test_e2e.py`, replace:

```python
    if os.environ.get("E2E_DIR"):
        base = Path(os.environ["E2E_DIR"])
        base.mkdir(parents=True, exist_ok=True)
        return base
```

with:

```python
    if os.environ.get("E2E_DIR"):
        # A fresh prefix-named child per test: both live tests can share
        # one E2E_DIR override without colliding on <E2E_DIR>/repo.
        root = Path(os.environ["E2E_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=root))
```

- [ ] **Step 2: Verify collection still works offline**

Run: `uv run pytest tests/e2e/test_e2e.py --collect-only -q`
Expected: tests collected, no import errors. (No live `-m e2e` run in this plan; the change is `tempfile.mkdtemp` infrastructure.)

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_e2e.py
git commit -m "fix(e2e): give each live test a unique workspace under E2E_DIR" -m "e2e_base returned the exact E2E_DIR for every caller, so selecting
both live tests with one override made the second make_fixture_repo
fail because <E2E_DIR>/repo already existed. Create a fresh
prefix-named mkdtemp child under the override instead.

Found by the GPT cross-review (suggestion 2,
docs/plans/20260718_phased_atdd_review_gpt56sol-max.md)."
```

---

### Task 9: Full-suite verification and follow-up bookkeeping

**Files:**
- Modify: `docs/todos/20260717_phased_atdd_followups.md` (F5 entry)

- [ ] **Step 1: Run the full offline suite**

Run: `uv run pytest`
Expected: all PASS (baseline 560 + roughly 15 new tests), `2 deselected` (e2e). If `test_fixture_baseline` fails on the Go build cache (known machine issue from the review's Verification notes), re-run it alone with a fresh `GOCACHE` before treating it as a regression.

- [ ] **Step 2: Record the outcome in the followups doc**

In `docs/todos/20260717_phased_atdd_followups.md`, under F5, check the box and append a line in the same style as F1-F4:

```markdown
- [x] 2026-07-19 GPT review 完成:7 blockers + 2 suggestions 全數確認並修復,
      計畫與驗證見 `docs/superpowers/plans/2026-07-19-phased-atdd-gpt-review-fixes.md`。
```

- [ ] **Step 3: Commit**

```bash
git add docs/todos/20260717_phased_atdd_followups.md docs/superpowers/plans/2026-07-19-phased-atdd-gpt-review-fixes.md
git commit -m "docs(todos): record the GPT phased review fix plan" -m "Mark followup F5 done: the GPT cross-review of the phased ATDD merge
was verified finding-by-finding and all seven blockers plus both
suggestions are fixed by the tasks in
docs/superpowers/plans/2026-07-19-phased-atdd-gpt-review-fixes.md."
```
