# Review: Phased ATDD (PHASES=1), merged diff

Reviewed range: `a8f2fa7..03526e1`

## Blockers

### 1. High: phase-gate repairs can be committed as the next phase's tests and then become protected

- Location: `src/adversarial_ai_coding/phaseflow.py:228`
- Defect: The phase gate can ask I to repair product code, but the phased stage ends without committing any dirty repair when `PHASE_REVIEW=0`.
- Concrete failure scenario: Phase 1 tasks are committed, its phase gate fails, and I edits `product.py` until the gate passes but leaves the edit dirty. The workflow records `phase-01-implement` at the old HEAD. Phase 2 records that HEAD as its test base; when B writes and commits phase 2 tests, `commit_work` also sweeps the dirty `product.py` repair into the test commit. `record_protected_tests(..., append=True)` sees both paths in `git diff <phase-02-test-base> HEAD`, appends `product.py` to `protected-tests.txt`, and later implementation work is forced to treat product code as an immutable acceptance test. A direct reproduction produced new protected names `['phase2_test.py', 'product.py']`.
- Suggested fix: After the phase gate passes, call `commit_if_dirty` with I before ending the stage or starting the next phase. Add an integration test where the phase gate fails once, I leaves a dirty product repair, and the next protected-list update contains only the new test paths.

### 2. High: every review round erases the worker replies that the reviewer is required to verify

- Location: `src/adversarial_ai_coding/review.py:147`
- Defect: `run_review` unconditionally truncates `review.md` before invoking the reviewer, so round N cannot read the findings and worker replies saved by round N-1.
- Concrete failure scenario: Round 1 reports a blocker. The worker writes `Disagree: ...` under it without fixing the code. At the start of round 2, line 147 replaces the whole file with an empty string; the reviewer prompt still says to verify previous replies, but there are no replies left to inspect. The reviewer can therefore approve without adjudicating the disagreement, and the archived round-2 review also lacks the audit trail.
- Suggested fix: Preserve a readable existing `review.md` until the reviewer has started and can inspect it. Pre-create only a missing file, and recover an unreadable existing file before the reviewer call instead of truncating every round. Add a real two-round `run_review` test that asserts round 2 sees the worker's reply.

### 3. High: an unreadable or missing review body can still produce an approved round

- Location: `src/adversarial_ai_coding/review.py:193`
- Defect: Recovery replaces an unreadable `review.md` with a stub but leaves a readable approving verdict intact, and `run_review` checks only `verdict.json` before returning true.
- Concrete failure scenario: A sandboxed reviewer writes an approved `verdict.json` and an unreadable `review.md` containing its analysis or blockers. `_recover_unreadable_output` discards the review and writes `REVIEW_UNREADABLE_STUB`; line 216 still sees `approved: true`, so the review loop ends successfully with the substantive review discarded. The added test `test_run_review_recovers_unreadable_review_md` explicitly confirms that `run_review` returns true in this state. Deleting `review.md` has the same outcome because a missing review is never treated as failure.
- Suggested fix: Make recovery report whether either required output was missing or unreadable, and force `FAILED_VERDICT` or return false whenever the review body cannot be preserved. Require both readable output files before approval.

### 4. High: red-check repairs are committed without another independent review

- Location: `src/adversarial_ai_coding/phaseflow.py:179`
- Defect: A reviews the tests before `red_check`, but every B repair made inside `red_check` bypasses A and is committed immediately once the command has the expected exit status.
- Concrete failure scenario: For a regression-guard phase, A approves meaningful tests but the gate unexpectedly fails. B responds to `phase-red-check-failed` by deleting the failing tests; the command now exits zero, so `red_check` returns, the deletion is committed, and `record_protected_tests` can record no new phase test at all. The implementation tasks and phase gate can then pass without the approved regression coverage. For a normal phase, B can similarly add an unconditional failure to obtain a nonzero result and leave I with an impossible protected gate.
- Suggested fix: Combine test review and the red check into one bounded loop. Whenever B changes tests to repair the red result, run A's acceptance-test review again, then rerun the deterministic check; commit only a candidate that is both reviewed and has the expected result.

### 5. High: protected recording is not crash-consistent with the stage ledger

- Locations: `src/adversarial_ai_coding/workflow.py:290`, `src/adversarial_ai_coding/phaseflow.py:185`
- Defect: The two global protected controls and the `phase-NN-write-tests` ledger entry are advanced as separate writes, with no durable sub-checkpoint or rollback for a resume.
- Concrete failure scenario: Phase 2 tests are committed and `record_protected_tests` writes both controls, then the process stops or an archive write fails before `end_stage`. The ledger still says `phase-02-write-tests` is incomplete. On resume, the skipped phase 1 path calls `activate_protected_controls`, which snapshots the global list that already includes phase 2. The rerun then asks B to write phase 2 tests, but any edit to those tests is misclassified as protected tampering and can exhaust the recovery loop. A direct reproduction showed the resumed snapshot containing `phase2_test.py` and a writer edit being returned by `protected_violations`. If interruption instead lands between the list write and the base write, the mixed controls can classify the already-committed phase 2 additions themselves as violations against the old base and ask B to remove them.
- Suggested fix: Persist phase-specific protected-control state and a durable `controls-recorded` checkpoint. On resume, either finish the write-tests stage without rerunning B when the current phase controls were fully recorded, or restore controls to the last completed phase before rerunning it. Also recover the two control files as one logical transaction so interruption between their writes cannot create a mixed pair.

### 6. Medium: schema-1 `phases.json` accepts malformed control-flow values

- Location: `src/adversarial_ai_coding/runstate.py:381`
- Defect: `load_phases` coerces persisted values instead of validating their types and phase invariants, so damaged state is trusted as different control flow.
- Concrete failure scenario: A persisted entry with `"regression_guard": "false"` loads as `True`, inverting the red check, and `"tasks": "abc"` loads as three tasks `("a", "b", "c")`. A schema-1 payload with `"phases": []` loads successfully and makes `run_phased_stages` skip every phase, after which the tail can complete if the configured gates do not catch the missing feature. Both states were accepted by direct calls to `load_phases`.
- Suggested fix: Validate the complete payload without coercion: `phases` must be a non-empty list; numbers must be sequential positive integers; titles and task strings must be non-empty strings; `regression_guard` must be an actual bool; and task collections must be non-empty lists. Raise `RunStateError` for any violation while continuing to trust valid persisted state over `plan.md`.

### 7. Medium: the deterministic plan parser accepts empty acceptance criteria and an empty task

- Location: `src/adversarial_ai_coding/phases.py:45`
- Defect: The parser records only the presence of an `Acceptance:` prefix and a task-list entry, not whether either contains text.
- Concrete failure scenario: `## Phase 1: x\nAcceptance:\n- [ ] \n` passes `parse_phases` as `Phase(..., tasks=("",))`. `ensure_named_task_queue` writes a blank line, while `remaining_tasks` filters blank lines out, so I receives no implementation task for the phase. With no meaningful acceptance test and permissive or empty gates, the phase is recorded complete without implementing behavior.
- Suggested fix: Require non-whitespace text after `Acceptance:` and after `- [ ] `, report both as structure problems, and add parser plus integration coverage for these cases.

## Suggestions

### 1. Add fault-injection coverage at the durable phase boundaries

- Location: `tests/test_phased_integration.py:80`
- Suggestion: The current resume test aborts during the next phase's initial test-writer call. Add cases after the test commit, after each protected-control write, after archive snapshots, after `end_stage`, after each task commit/queue pop/plan update, and after a phase-gate repair. These are the states that determine whether the advertised resumability is real.

### 2. Make `E2E_DIR` safe when both live tests are selected together

- Location: `tests/e2e/test_e2e.py:45`
- Suggestion: `e2e_base` returns the exact same explicit `E2E_DIR` for both live tests. Running the whole `-m e2e` file with one override makes the second `make_fixture_repo` fail because `<E2E_DIR>/repo` already exists. Create a unique prefix-named child under `E2E_DIR`, or document and enforce that the override must identify a fresh directory per test.

## Verification

- Focused offline checks passed: unreadable-review recovery behavior, red-check repair behavior, and protected recording (`5 passed` across the completed focused invocations).
- Direct temporary-repository reproductions confirmed the product-file protection leak and the post-record resume violation.
- The default offline suite completed with `559 passed, 2 deselected, 1 failed`. The sole failure was `test_fixture_baseline`: Go could not initialize its build cache because the user cache path was malformed/inaccessible. A retry with a fresh workspace `GOCACHE` was denied in the sandbox, and the approved unsandboxed retry stalled and was terminated. No feature test failed.
- No `pytest -m e2e` command was run.
