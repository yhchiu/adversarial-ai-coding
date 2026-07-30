# Review request: Phased ATDD (PHASES=1), merged diff

You are a strict, independent code reviewer for the repository in the
current working directory (`adversarial-ai-coding`, a Python 3.12
stdlib-only workflow tool). Review this merged feature range:

    git log --oneline a8f2fa7..03526e1   # 16 commits, feat/phased-atdd
    git diff a8f2fa7..03526e1

Read these first, in order:

1. `docs/superpowers/specs/2026-07-17-phased-atdd-design.md` (approved design)
2. `docs/superpowers/plans/2026-07-17-phased-atdd.md` (implementation plan)
3. The "Phased ATDD Mode" section of `README.md`

Feature summary: `PHASES=1` replaces the single up-front acceptance-test
stage with a per-phase loop: the reviewer writes one phase's protected
tests, the workflow runs a deterministic red check (inverted for
`(regression-guard)` phases), the implementation slot implements the
phase's tasks one commit each, then a phase gate must pass. `PHASES=0`
must behave exactly as before the change.

Already found and fixed in this range - do NOT re-report:

- Phase-review repairs were not re-gated (fixed in f35a190).
- Pre-phased snapshots could resume with PHASES=1, and malformed
  phases.json raised a bare KeyError (both fixed in dc63b59).
- `activate_protected_controls` was renamed public (same commit).

Also in scope (merged in the same range):

- `review.py` reviewer-output recovery for unreadable files (8842544 and
  e893fe6). This was written under time pressure during live-E2E
  debugging; scrutinize it hardest.
- The six phased prompt templates, and the E2E workspace relocation
  (7b6501b, 03526e1).

Priority areas:

1. Resume correctness in `phaseflow.py`: interruption at every boundary
   (before/inside/after `phase-NN-write-tests` and `phase-NN-implement`),
   per-phase queue and test-base restoration, ledger skip paths, and
   `ensure_phases` trusting persisted state over plan.md.
2. Protected-controls lifecycle: append-only list growth,
   snapshot re-activation timing at phase boundaries, any window where a
   worker could tamper undetected, and the crash-and-rerun path around
   `record_protected_tests`.
3. `_recover_unreadable_output` in `review.py`: fail-closed guarantees,
   ordering versus the archive/suggestion/verdict reads, and repeated
   poisoning across rounds.
4. `red_check` semantics: regression-guard inversion, empty-command skip,
   MAX_ROUNDS accounting.
5. `PHASES=0` regression risk from the `run_workflow` restructure.

Rules:

- Verify every claim against the actual code before reporting it; no
  speculation. If you cannot construct a concrete failure scenario, it is
  a suggestion, not a blocker.
- Rank findings by severity. For each: file:line, the defect in one
  sentence, a concrete failure scenario (inputs/state -> wrong outcome),
  and a suggested fix.
- Separate blockers (correctness, security, data loss) from suggestions
  (style, simplification, docs).
- You may run the offline suite with `uv run pytest -q` (if imports fail,
  clear the PYTHONHOME and PYTHONPATH environment variables first).
  Never run `pytest -m e2e`; it calls real AI agents.
- Do not modify any repository file. Your only output is the review,
  written in ASCII English to
  `docs/plans/20260718_phased_atdd_review_gpt56sol-max.md`.
