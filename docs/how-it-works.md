# How It Works — Stage-by-Stage Details

This is the detailed companion to the
[How It Works](../README.md#how-it-works) overview in the README: the complete
pipeline diagram, the review-loop mechanics, and per-stage notes. The normative
reference for every file that crosses an agent boundary — verdicts, reviews,
specs, plans, protected tests, and run state — is
[artifact-contract.md](artifact-contract.md).

The workflow drives two durable slots, `A` and `B`. By default A is the owner
and B is the reviewer. Stage 5 can also use a separate implementation slot,
`I` (the implementer; owner unless `IMPL_*` is set). Any slot can resolve to:

- `claude` for Claude Code CLI
- `codex` for Codex CLI
- `agy` for Antigravity CLI
- `opencode` for OpenCode (any model the user has already authenticated)
- A custom agent CLI or wrapper command

A worker call is any producing agent call, not a job title. Using different
agent commands for owner and reviewer is recommended because their failure
modes are different. Dual spec can rebind owner and reviewer; slot names stay
A and B. The diagrams below use the default mapping (A = owner, B = reviewer).

Every step marked ⟳ in the pipeline runs the same review loop, shown in the
second diagram.

```mermaid
flowchart TD
    spec["<b>1 · Write spec</b><br/>owner (A) writes · reviewer (B) reviews ⟳"]
    gate{"2 · Human approves<br/>the spec?"}
    plan["<b>3 · Write plan</b><br/>owner (A) writes the checkbox task list · reviewer (B) reviews ⟳"]
    plangate{"Human approves the plan?<br/>(optional: HUMAN_GATE_PLAN=1)"}
    tests["<b>4 · Acceptance tests</b> (roles swapped)<br/>reviewer (B) writes · owner (A) reviews ⟳"]
    task["<b>5 · Implement next task</b><br/>I (implementer; owner by default) codes · build gate (compile only) · protected-test check · commit"]
    more{"Tasks left?"}
    branch["<b>6 · Full gate + branch review</b><br/>workflow runs GATE_CMD · reviewer (B) reviews diff ⟳"]
    final["<b>7 · Final review and fixes</b><br/>owner (A) self-review · reviewer (B) final acceptance ⟳"]
    fin(["<b>8 · Finish</b><br/>print push / PR commands"])
    abort(["Abort"])

    spec --> gate
    gate -- "y" --> plan
    gate -- "anything else" --> abort
    plan --> plangate
    plangate -- "y (or gate disabled)" --> tests
    plangate -- "anything else" --> abort
    tests --> task --> more
    more -- "yes" --> task
    more -- "no" --> branch --> final --> fin
    tests -. "run by the full gate" .-> branch
    phased["<b>4-5 · Phased loop (PHASES=1)</b><br/>per phase: reviewer (B) writes tests · owner (A) reviews ⟳<br/>red check · I implements tasks · phase gate"]
    plangate -. "y · PHASES=1" .-> phased
    phased -.-> branch
```

The ⟳ review loop is one reusable building block. The workflow, not the AI,
decides when the loop ends:

```mermaid
flowchart LR
    review["reviewer (B) reviews the scope"] --> verdict{"verdict.json<br/>approved?"}
    verdict -- "yes" --> done(["stage continues"])
    verdict -- "no (blockers)" --> fix["owner (A) replies to review.md<br/>and fixes"]
    fix --> dgate["deterministic gate<br/>(if configured)"] --> review
    verdict -. "MAX_ROUNDS exhausted" .-> halt(["abort + notify human"])
```

A deterministic gate is a shell command the workflow runs itself instead of
trusting the AI's "tests pass" claims. There are two here: `GATE_CMD` is the
full gate (build, vet, and every test, including the acceptance tests), and
`BUILD_GATE_CMD` is the lightweight per-task gate (compile only). Stages
without a configured gate command skip that step. Phased ATDD adds a third,
`PHASE_GATE_CMD`. For how each one is detected, what an empty value costs,
and how a failure is repaired, see [`gates.md`](gates.md).

Stage notes:

1. **Write spec**: `spec.md` must include an Assumptions and Open Questions
   section, because headless AI cannot ask humans and silent guessing is
   forbidden. With `DUAL_SPEC=1`, A and B write independent candidate specs
   first; see [Dual Spec Mode](../README.md#dual-spec-mode).
   With `IMPORT_SPEC=path`, the workflow copies your file in instead of
   asking the owner to write it; see the import contract in
   [import-format.md](import-format.md).
   When `PHASES` is unset and `IMPORT_PLAN` is not set, the spec reviewer also
   writes a phased-fitness judgment to `aac/.run/phased-suggestion.json`,
   and the spec human gate may offer to enable Phased ATDD before the plan is
   written.
2. **Human approval**: the highest-leverage checkpoint. A bad spec amplifies
   into many bad changes, so a human approves the spec (and may edit it first)
   before costly implementation starts. `HUMAN_GATE=0` skips this gate.
3. **Write plan**: `plan.md` must be a `- [ ]` checkbox task list. Each task
   maps to one commit. `HUMAN_GATE_PLAN=1` adds a second human checkpoint
   here, after the review and before the plan is committed: the plan is the
   task queue, so it is the last cheap place to intervene. Off by default;
   like the spec gate, you may edit `plan.md` first and your edits are
   committed with it.
   With `IMPORT_PLAN=path` the plan is imported the same way; the
   review, gates, and structure checks still run (`IMPORT_REVIEW=0`
   skips only the AI review of imported files).
4. **Acceptance tests**: adversarial TDD separates the test author from the
   implementer, so the roles swap: the reviewer writes the tests and the owner
   only reviews them.
   The test files become protected; the workflow hard-checks them with
   `git diff` after every later producing call. Red tests are expected here
   (TDD red phase). See
   [Protected Acceptance Tests](../README.md#protected-acceptance-tests) for
   details and the escape hatch when a protected test is wrong.
5. **Implement tasks**: one checkbox task per commit keeps review and rollback
   small. The implementation slot handles the whole per-task loop: implementing
   the task, repairing `BUILD_GATE_CMD` failures, repairing protected-test
   violations, and making that task's commit. With no `IMPL_*` setting, this
   slot is exactly the owner and behavior is unchanged. The per-task gate is
   lightweight (compile only), so acceptance tests may stay red until all tasks
   are done. After the loop, the normal owner/reviewer pairing resumes: the
   owner handles full-`GATE_CMD` repairs, branch-review fixes, and final-review
   fixes, while the reviewer performs branch review and final acceptance.
6. **Full gate + branch review**: the workflow itself runs `GATE_CMD` — the AI's
   own "tests pass" claim is never trusted — and acceptance tests must pass
   now. The reviewer then reviews the complete branch diff.
7. **Final review and fixes**: the owner works through the accumulated
   `aac/.run/suggestions.md` items and its own self-review findings, then the
   reviewer gives final acceptance.
8. **Finish**: the workflow prints `git push` / `gh pr create` commands and run
   metrics. `OPEN_PR=1` runs them automatically.

Review verdicts are graded. `verdict.json` is
`{approved, blockers[], suggestions[]}`: only blockers make the loop repeat,
while suggestions accumulate in `aac/.run/suggestions.md` and are handled in
stage 7. Contract C1 in [artifact-contract.md](artifact-contract.md) is the
authoritative schema and fail-closed rules. This keeps a reviewer from blocking on nitpicks or approving just to
be polite.
