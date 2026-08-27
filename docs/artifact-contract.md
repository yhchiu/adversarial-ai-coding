# Artifact Contracts — How A/B/I Exchange Work

English | [繁體中文](artifact-contract.zh-TW.md)

This document makes the implicit inter-agent protocol explicit. In
adversarial-ai-coding, the worker slot `A`, the reviewer slot `B`, and the
optional implementation slot `I` never talk to each other directly. Every
boundary between them crosses a file that the workflow orchestrator controls.
Those files are the protocol. This page is the normative reference for them:
what each artifact must contain, who may write it, how the workflow validates
it, and what happens when it is wrong.

Audience: maintainers changing the pipeline, authors of custom agent wrappers,
and anyone building tooling that consumes run artifacts. Agents themselves
receive their contract through `AGENTS.md` (`resources/AGENTS.template.md`)
and the per-stage prompts; this document explains why those rules are shaped
the way they are.

Related reading:
[how-it-works.md](how-it-works.md) for the stage-by-stage pipeline,
[import-format.md](import-format.md) for the external spec/plan import
contract, and
[docs/adr/0001-single-aac-root-for-run-artifacts.md](adr/0001-single-aac-root-for-run-artifacts.md)
for the directory layout rationale.

## Protocol Rules

Five rules govern every cross-slot boundary:

1. **The orchestrator mediates everything.** Agents exchange information only
   through files under `aac/`. There is no shared chat context: sessions are
   discarded at handoff boundaries, and each call points at one complete,
   self-contained prompt file instead.
2. **Structured outputs fail closed.** If a required structured artifact
   (`verdict.json`, `phases.json`, `ledger.json`) is missing, unreadable, or
   invalid, the workflow treats it as failure or refuses to continue. An
   agent's claim is never trusted over what it wrote to disk. The single
   deliberate exception is `phased-suggestion.json`, which fails open because
   it must never block a run.
3. **Findings are graded.** Blockers repeat the review loop; suggestions
   accumulate and are handled later. This keeps a reviewer from blocking on
   nitpicks or approving to be polite.
4. **Control truth lives in workflow state, not in prose.** `plan.md` is the
   human-readable view; the authoritative task queue is
   `state/<RUN_ID>/tasks-remaining`. Once a run starts, the persisted JSON
   state decides control flow.
5. **Everything is archived with provenance.** Every prompt, reply, verdict,
   diff snapshot, and raw CLI transcript lands in
   `aac/.run/archive/<RUN_ID>/` with a `.meta.json` sidecar naming the
   generator, slot, model, stage, and round.

## The Handoff Unit: Archived Prompt Files

Every agent call is the same shape, for built-in and custom commands alike:

```text
$AGENT $AGENT_ARGS "Read the full workflow prompt from this repository \
file and follow it exactly: aac/.run/archive/<RUN_ID>/NNN-*-prompt.md"
```

- The prompt file is rendered from a template in `resources/prompts/*.md`
  (`{{PLACEHOLDER}}` substitution) and archived before the call
  (`src/adversarial_ai_coding/prompts.py:64`). It is self-contained: role,
  scope, output paths, and constraints. Nothing depends on retained chat
  history.
- Archive naming: `{seq:03d}-worker-{stage}-r{round}-prompt.md` for the
  acting worker and `{seq:03d}-reviewer-{stage}-r{round}-prompt.md` for the
  reviewer, with matching `-output.txt`, `-attempt-N-rc*.raw`, and
  `-attempt-N-rc*.cli.raw` siblings
  (`src/adversarial_ai_coding/workflow.py:368`,
  `src/adversarial_ai_coding/review.py:177`). `{seq}` is a three-digit
  counter assigned atomically per run.
- A custom agent's entire obligation is: read that file, do what it says,
  exit non-zero on execution failure. Everything else below tells it *what*
  the file will ask for.

## Agent-Facing Artifact Contracts

### C1 · `aac/.run/verdict.json` — the review result

Written by: the reviewer, with its built-in file editing tool (never shell
redirection).

Schema — exactly one line of JSON:

```json
{"approved": true|false, "blockers": ["must-fix issue"], "suggestions": ["non-blocking"]}
```

Contract:

- The workflow pre-seeds the file with a failed verdict before every reviewer
  call (`src/adversarial_ai_coding/review.py:195`). A reviewer that omits the
  file therefore loses by default — fail closed.
- Approval requires `approved === true` **and** an empty blockers list;
  malformed JSON, a wrong type, or a self-contradicting verdict all count as
  *not approved* (`src/adversarial_ai_coding/review.py:106`).
- Blockers are correctness bugs, spec violations, weakened tests, security
  problems. Only blockers repeat the review→fix loop; the workflow ends the
  loop, never the agent (`MAX_ROUNDS` caps it and aborts the run when
  exhausted).
- Suggestions are appended to `aac/.run/suggestions.md` grouped by
  `## {stage}(round {n})` and are handled in the final stage
  (`src/adversarial_ai_coding/review.py:130`). They never block approval.
- Each round's verdict is archived as
  `NNN-verdict-{stage}-r{n}.json`; the live file always describes only the
  current round.

### C2 · `aac/.run/review.md` — findings and replies

Written by: the reviewer (findings) and the worker (replies), alternating.

Contract:

- The reviewer lists findings one by one, naming the file and giving evidence
  the worker can reproduce: the command run and its key output lines, or the
  exact spec/plan text violated. Each new round **overwrites** old content
  but keeps items the worker has not replied to yet.
- The worker replies under each finding: `Fixed: <summary>` after fixing, or
  `Disagree: <reason>`. Silently ignoring a finding violates the contract.
- The reviewer's next round verifies each reply first, then looks for new
  problems.
- The reviewer may modify **only** `review.md` and `verdict.json`
  (`resources/prompts/review.md`); running tests is allowed, editing the
  branch is not.
- Rounds are archived as `NNN-review-{stage}-r{n}.md` (reviewer) and
  `NNN-review-{stage}-r{n}-worker.md` (worker reply pass).
- An unreadable `review.md` (a sandbox can write it with a broken ACL) is
  discarded and the round is treated as failed
  (`src/adversarial_ai_coding/review.py:38`).

### C3 · `aac/.run/phased-suggestion.json` — side judgment

Written by: the spec reviewer, only when armed (no explicit `PHASES`, no
imported plan).

Schema — one line: `{"phased": true|false, "reason": "..."}`
(default `{"phased": false, "reason": ""}`).

Contract:

- It is a judgment separate from the verdict: never put it inside
  `verdict.json`, never let it influence `approved` or blockers.
- It fails open: any missing, malformed, or mistyped input degrades to “no
  suggestion” with a warning, never to a failed stage
  (`src/adversarial_ai_coding/phased_suggestion.py:45`).

### C4 · `aac/docs/<RUN_ID>/spec.md` — the specification

Written by: the owner slot (or imported via `IMPORT_SPEC`).

Required sections: feature description, testable acceptance criteria, edge
cases, out-of-scope items, and an **Assumptions and Open Questions** section
that honestly lists every assumption made without asking a human (headless
agents cannot ask).

Dual-spec additions (`DUAL_SPEC=1`):

- `spec-a.md` / `spec-b.md` are independent candidates. A slot asked to write
  one candidate must not read the other candidate, its reviews, or the
  comparison files — isolation is part of the contract.
- Cross reviews land in `spec-{a,b}.review-by-{b,a}.md` with verdicts in
  `spec-{a,b}.verdict-by-{b,a}.json` (same schema as C1, but informational:
  candidate reviews never block, they inform the human decision).
- Each slot writes a comparison table `spec-comparison-{a,b}.md`; the
  workflow writes the `spec-comparison.md` index; the human picks
  `a`/`b`/`ma`/`mb` and `spec-decision.md` records the chosen owner.
- With a merge (`ma`/`mb`), the human edits `aac/.run/spec-merge-request.md`;
  the owner must adopt those items into `spec.md` and the reviewer verifies
  they arrived intact and undistorted.

### C5 · `aac/docs/<RUN_ID>/plan.md` — the task queue view

Written by: the owner slot (or imported via `IMPORT_PLAN`).

Default mode contract:

- The task list is `- [ ]` checkbox lines; each item maps to exactly one
  commit and must be independently implementable and verifiable.
- The implementer flips its finished item to `- [x]` (the workflow also flips
  it authoritatively after the commit,
  `src/adversarial_ai_coding/runstate.py:583`).

Phased mode (`PHASES=1`) adds structural requirements validated
deterministically before any implementation starts
(`src/adversarial_ai_coding/phases.py:33`):

- Sections use `## Phase N: <title>` headings; numbering is sequential from
  Phase 1.
- Every phase needs a non-empty `Acceptance:` line (observable behavior at a
  stable boundary) and at least one non-empty `- [ ] ` task.
- Tasks outside any phase, duplicate/gap numbering, or empty titles are
  rejected and sent back to the owner.
- A title ending in `(regression-guard)` inverts the red-check expectation:
  those tests must pass immediately.
- After validation the parsed graph is persisted to
  `state/<RUN_ID>/phases.json` (schema 1) and becomes control truth;
  `plan.md` degrades to UI.

### C6 · Acceptance tests and protected controls

Written by: the **reviewer** (roles swap for this stage; the owner only
reviews the tests). This separation is deliberate — no slot implements
against tests it wrote itself.

- Test files go in the project's normal test location; red results are
  expected (TDD red phase). The test writer must not write product code or
  modify files under `aac/docs/`.
- After the stage, the workflow records the test paths in
  `aac/.run/protected-tests.txt` and the base commit in
  `protected-base.sha`. From then on the implementer must not edit, delete,
  or skip those files (`resources/prompts/implement-plan-task.md`); the
  workflow re-checks the bytes around every worker action and forces a revert
  on violation.
- A believed-wrong test is never fixed by an agent: record the objection in
  the spec's Assumptions and Open Questions. Only a human may change the
  protected list or advance `protected-base.sha`.

## Workflow-Owned State (Read-Only for Agents)

Under `aac/.run/state/<RUN_ID>/`. Written only by the workflow; parsed as
data, never executed; unknown schemas are refused.

| File | Schema | Content |
|---|---|---|
| `settings.json` | `{"schema": 2, ...}` | Resolved settings snapshot; unknown keys refuse a resume (`src/adversarial_ai_coding/runstate.py:146`) |
| `ledger.json` | `{"schema": 1, "stages": [...]}` | Append-only completed-stage list; resume skips these |
| `task.txt` | text | Immutable request snapshot |
| `tasks-remaining` / `tasks-remaining-phase-NN` | text, one task per line | Authoritative task queue; empty file means every task committed |
| `phases.json` | `{"schema": 1, "phases": [{number, title, regression_guard, tasks}]}` | Persisted phase graph (control truth for `PHASES=1`) |
| `last-head`, `acceptance-test-base`, `run-base` | text (SHA) | Cross-stage git baselines restored across resumes |
| `imported-{kind}-archive-path` | text (path) | Where an imported spec/plan was archived |
| `lock/` | directory mutex | One active attempt per run id |
| `completed` | marker | Present once the run finishes; completed runs refuse resume |

Transient files directly under `aac/.run/` describe the current round only
(`review.md`, `verdict.json`, `last-agent-output.txt`, `pr-body.md` are
cleared on every start; durable controls survive a resume,
`src/adversarial_ai_coding/runstate.py:595`).

## Provenance: `.meta.json` Sidecars and `metrics.csv`

Every archived artifact gets a sibling `<name>.meta.json` written atomically
(`src/adversarial_ai_coding/archive.py:114`):

| Field | Meaning |
|---|---|
| `generated_at` | Local time with offset, `%Y-%m-%dT%H:%M:%S%z` |
| `generator_role` | Functional role that produced it: `worker`, `reviewer`, or `workflow` |
| `agent` | Resolved agent command/runtime (e.g. `claude`, a custom wrapper name, `workflow`) |
| `agent_slot` | Slot identity: `A`, `B`, `I`, or `workflow` |
| `model` / `model_args` | Resolved model override and model flags (empty if unset) |
| `stage` / `round` | Stage slug and review round (`round` serialized as a string) |
| `run_id` / `artifact` | Run identity and artifact path |

Note the distinction the pair encodes: during the acceptance-test stage slot
B *writes* the tests, so its artifacts carry `generator_role: "worker"` with
`agent_slot: "B"` — role is what the agent did, slot is who it is.

`metrics.csv` columns: `run_id, stage, role, agent, round, duration_s,
cost_usd, model, model_args, generated_at, agent_slot`.

## Stage Name Registry

Names recorded in `ledger.json` and used in archive slugs:

- Default flow: `write-spec`, `commit-spec`, `write-implementation-plan`,
  `write-acceptance-tests`, `write-code`, `final-review-and-fixes`.
- Dual spec replaces the spec stage with: `write-spec-a`, `write-spec-b`,
  `review-spec-a`, `review-spec-b`, `compare-specs-a`, `compare-specs-b`,
  `select-spec`, `finalize-spec`.
- Phased mode replaces stages 4–5 with per-phase pairs:
  `phase-{NN}-write-tests`, `phase-{NN}-implement` (zero-padded two-digit
  phase number).

## Invariant Summary

| # | Invariant | Enforcement |
|---|---|---|
| 1 | `approved: true` ⟺ zero blockers; missing/invalid verdict ⇒ not approved | `src/adversarial_ai_coding/review.py:106` |
| 2 | Verdict pre-seeded to failed before every reviewer call | `src/adversarial_ai_coding/review.py:195` |
| 3 | Suggestions never block; they accumulate to `suggestions.md` | `src/adversarial_ai_coding/review.py:130` |
| 4 | Reviewer modifies only `review.md` + `verdict.json` | `resources/prompts/review.md` |
| 5 | Worker answers every finding (`Fixed:` / `Disagree:`) | `resources/AGENTS.template.md` |
| 6 | No slot writes both the spec and the acceptance tests | Pipeline design (`docs/how-it-works.md`) |
| 7 | Implementer cannot touch protected tests | `protected-tests.txt` + byte re-checks |
| 8 | Task queue truth is workflow state, `plan.md` is UI | `src/adversarial_ai_coding/runstate.py:541` |
| 9 | Phased plan structure is validated before implementation | `src/adversarial_ai_coding/phases.py:33` |
| 10 | Persisted state refuses unknown schemas and conflicting resumes | `src/adversarial_ai_coding/runstate.py:157,203` |
