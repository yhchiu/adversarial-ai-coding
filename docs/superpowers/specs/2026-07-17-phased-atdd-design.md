# Phased ATDD Design

Date: 2026-07-17
Status: Approved design, pending implementation plan

## Context

Today stage 4 writes every acceptance test up front, and the stage 5 task
loop runs only the compile-only `BUILD_GATE_CMD` per task. Acceptance tests
can therefore stay red across the whole implementation loop and only turn
green at the final full gate. On large tasks this makes failure localization
poor: a red suite at the end points at a dozen tasks, not one phase.

Source proposal: `docs/plans/20260717_phase_v1_gpt56sol-high-thinking.md`
(external review, "Phased ATDD"). This design adopts its direction with four
corrections:

1. JIT test generation removes the need for per-phase test selection. At the
   end of phase N, only tests for phases 1..N exist, so "run everything" is
   already "history plus current phase all green". No pytest markers, no
   `.workflow/phase-tests/` registry.
2. The proposal's mandatory red check contradicts its own example (a
   "old behavior unchanged" phase is green from the start). This design adds
   a `(regression-guard)` exemption marked in the plan.
3. Mandatory per-phase reviewer diff review multiplies reviewer calls by the
   phase count. It becomes opt-in (`PHASE_REVIEW=1`).
4. The proposal implied one commit per phase. Tasks stay the commit unit;
   a phase is the acceptance unit that groups tasks.

## Decisions Already Made

- `PHASES=1` is opt-in. `PHASES=0` (default) keeps the current flow with no
  behavior change.
- `PHASE_GATE_CMD` is a new variable; when empty, the phase gate runs
  `GATE_CMD`.
- `PHASE_REVIEW=1` enables an optional per-phase diff review by B; default
  off.
- The red check is a hard deterministic gate run by the workflow, with the
  `(regression-guard)` plan marker flipping the expectation.
- Phase gate repairs and phase review fixes go to the implementation slot
  `I` (its session holds the freshest context). The owner still handles
  everything after the last phase, as today.

## Goals

- Localize failures per phase: every completed phase stays green for the
  rest of the run.
- Keep the adversarial separation: B writes phase tests, A reviews them,
  `I` implements, and protected controls still fail closed.
- Keep per-task commits and the small-commit discipline inside phases.
- Keep `PHASES=0` behavior identical to today.

## Non-Goals

- No per-phase test selection mechanism (tags, markers, registries).
- No new review roles; the existing review loop is reused as-is.
- No changes to the spec stages. `DUAL_SPEC` is orthogonal: phases start
  after the plan regardless of how the spec was chosen.
- No automatic phase-count tuning or heuristics; the plan defines phases.

## Configuration

| Variable | Default | Description |
|---|---:|---|
| `PHASES` | `0` | `1` enables the phased flow. Decides the stage graph, so it is immutable across resume (same rule as `DUAL_SPEC`). |
| `PHASE_GATE_CMD` | empty | Gate command run at each phase boundary (red check and phase gate). Empty falls back to `GATE_CMD`. Follows the normal non-empty resume override rule. |
| `PHASE_REVIEW` | `0` | `1` adds a B review of each phase diff, with the standard blocker loop. Follows the normal non-empty resume override rule. |

## Plan Format (PHASES=1)

```markdown
## Phase 1: JSON output for the success path
Acceptance: `mytool list --json` prints a valid JSON array.
- [ ] Add --json flag parsing
- [ ] Emit JSON for the success path

## Phase 2: Old behavior unchanged (regression-guard)
Acceptance: output without --json is byte-identical to before.
- [ ] Add regression fixtures
```

Rules:

- `## Phase N: <title>` headings split the plan into phases. A trailing
  `(regression-guard)` on the title exempts the phase from the red
  requirement (its tests must be green immediately instead).
- Every phase needs an observable `Acceptance:` line (behavior at a stable
  boundary: CLI, API, public interface, file output) and at least one
  `- [ ]` task. Tasks remain the commit unit.
- Phases must be vertical functional slices, not horizontal technical
  layers. The phased plan prompt states this with a good and a bad example,
  and the phased plan review scope lists horizontal slicing, missing
  acceptance criteria, and empty phases as blockers.
- New prompt templates `write-implementation-plan-phased.md` and
  `review-scope-plan-phased.md` are used when `PHASES=1` (the template
  renderer has no conditionals, so separate files are the clean option).
- Deterministic structure check: after the plan review loop approves and
  before the plan commit, the workflow parses the plan itself. On a parse
  failure it sends a repair prompt to the owner, up to `MAX_ROUNDS`, then
  aborts resumable. This prevents paying for spec and plan and only then
  discovering an unusable plan format.

## Stage Flow (PHASES=1)

Stages 1-3 (spec, human gate, plan) are unchanged. The single
`write-acceptance-tests` stage does not run. Instead, for each phase k:

```text
stage phase-kk-write-tests
  1. B writes phase k tests only. New prompt `write-phase-tests`: receives
     the spec, the plan, phase k's title and acceptance criteria, and the
     list of completed phases.
  2. A reviews. The existing `review-scope-acceptance-tests` template is
     reused; its `TEST_BASE` is the phase test base, which scopes the
     review to this phase's new tests.
  3. Red check: the workflow runs PHASE_GATE_CMD.
       normal phase          -> non-zero exit required (the suite was green
                                before this phase, so any red comes from
                                the new tests)
       regression-guard phase -> zero exit required
     A wrong result means B's tests are invalid: repair prompt to B, up to
     MAX_ROUNDS, then abort resumable. The check runs after the review loop
     so it validates the final tests.
  4. Commit the tests.
  5. The workflow appends the new test files to protected-tests.txt,
     writes HEAD to protected-base.sha, and re-activates the snapshot.
     New files are detected as today: `git diff --name-only <phase test
     base> HEAD` with the spec directory excluded. The phase test base is
     HEAD before B starts writing.

stage phase-kk-implement
  1. Per-task loop exactly as today: I implements, BUILD_GATE_CMD,
     protected check, one commit per task.
  2. Phase gate: gate loop on PHASE_GATE_CMD. Repairs go to I.
  3. If PHASE_REVIEW=1: B reviews the phase diff (phase start SHA to HEAD)
     with the standard review loop. Fixes go to I, then commit_if_dirty.
```

After the last phase, the tail is unchanged: the owner runs the full
`GATE_CMD` gate loop, B reviews the branch diff, then final review and
finish (stages 6-8).

Known limitation: the red check cannot distinguish "new tests are red" from
"B broke the build or the existing suite". A's test review and the phase
gate after implementation are the safety nets. Accepted for v1.

## Implementation-Test Policy

The default test level is the phase acceptance test at a stable boundary
(system, component, or contract level; not necessarily end-to-end). `I` may
add lower-level implementation tests during a task when at least one
trigger holds: many input combinations or edge cases; parser, state
machine, algorithm, or data transformation logic; concurrency, timeout,
retry, or cancellation behavior; failures that acceptance tests cannot
localize or reproduce cheaply. This policy is prompt text in
`AGENTS.template.md` and the phased plan prompt, not workflow code.

## Protected Controls Lifecycle

- `protected-tests.txt` becomes append-only across phases: files already
  protected are never removed by the workflow.
- Only the workflow mutates the control files, only at a phase boundary,
  never while a worker is active. It rewrites both controls and replaces
  the in-memory snapshot wholesale (`_activate_protected_controls`).
- Verification during worker execution is byte-for-byte unchanged: any
  control-file change while a worker runs still fails closed (the 47f1b0b
  semantics are preserved).
- The manual escape hatch is unchanged: a human fixes a wrong protected
  test, commits, updates `protected-base.sha`, and resumes; the resumed
  process trusts the on-disk controls as its starting state.

## Resume / RunState

- Stage names carry the phase index (`phase-02-write-tests`,
  `phase-02-implement`), so the existing linear stage ledger, skip logic,
  and metrics work without structural change.
- The state stores the parsed phase structure: the ordered phase list
  (title, regression-guard flag), the per-phase task queues, and each
  phase's test base SHA. Resume restores from state and never re-parses
  `plan.md`, matching the existing task-queue snapshot philosophy.
- A resume attempt that changes `PHASES` is refused, like `DUAL_SPEC`.
- Safe interruption points: between write-tests and implement, between
  phases, and between tasks — all existing ledger granularity.

## Error Handling

Red check exhaustion, phase gate exhaustion, and plan structure check
exhaustion all behave like existing loops: abort resumable with the
original exit-code semantics and fire `NOTIFY_CMD`.

## Testing

- Unit: phased plan parsing (headings, regression-guard marker, missing
  acceptance line, empty phase, malformed mixes); protected list append and
  snapshot re-activation; red check expectations for both phase kinds and
  MAX_ROUNDS exhaustion; `PHASE_GATE_CMD` empty fallback; resume refusal on
  a `PHASES` conflict.
- Integration (scripted fake agents, no AI cost): a full `PHASES=1` run;
  interrupt after write-tests then resume; interrupt mid-phase between
  tasks then resume; `PHASE_REVIEW=1` path.
- Live E2E: one small `PHASES=1` task via `pytest -m e2e` (not started from
  a sandboxed shell).

## Documentation

- README (English and zh-TW): phase loop branch in the mermaid diagram,
  three new rows in the configuration table, and a "Phased ATDD" section
  covering vertical slices and the `(regression-guard)` marker.
- `AGENTS.template.md`: phase rules and the implementation-test trigger
  list.
