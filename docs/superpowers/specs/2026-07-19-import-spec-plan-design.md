# Import External Spec and Plan Design

Date: 2026-07-19
Status: Approved design, pending implementation plan

## Context

Today the workflow always generates `spec.md` and `plan.md` itself: slot A
writes, slot B reviews, a human approves. Requirement clarification happens
outside the tool or not at all; the only compensation is the mandatory
"Assumptions and Open Questions" section, because headless agents cannot ask
a human anything.

Users often clarify requirements in their own interactive tools (Claude Code
with a brainstorming skill, Codex, a chat session with a PM) and end up with
a finished spec or plan. The workflow has no way to accept those files; it
regenerates everything from the one-line task description and throws the
clarification work away.

An in-tool interactive brainstorming stage was considered and rejected: it
works against the headless-first design, duplicates what interactive CLIs
already do better, and adds a large new protocol surface. Importing external
artifacts solves the same user need with a fraction of the machinery.

## Decisions Already Made

- Import is configured by **environment variables**, matching every other
  setting. No new CLI flags.
- Imported artifacts **still go through adversarial review by default**.
  Review can be skipped with an explicit opt-out variable.
- Human gates are **not** affected by the import feature. `HUMAN_GATE` and
  `HUMAN_GATE_PLAN` keep their existing, independent meaning.
- The variable names avoid `SPEC_FILE` and `PLAN_FILE` because those are
  internal prompt-template placeholders; reusing them would confuse readers
  of the prompt files.

## Goals

- Accept an externally written spec, or spec plus plan, and start the
  pipeline after the corresponding "write" step.
- Keep the adversarial safety net: reviewer review loop and human gates run
  on imported artifacts exactly as they do on generated ones, unless the
  user explicitly opts out of the AI review.
- Fail loudly on malformed input before any paid AI call.
- Record honest provenance: the run archive and PR body must show what was
  imported and whether it was reviewed.

## Non-Goals

- No in-tool interactive brainstorming or question loop.
- No import of acceptance tests; those are always written by the reviewer.
- No per-artifact review granularity (one `IMPORT_REVIEW` switch covers both
  imported artifacts).
- `IMPORT_REVIEW=0` never skips human gates, deterministic format checks, or
  the commit step.
- No changes to DUAL_SPEC mode; it is simply incompatible with import.

## Interface

Three new environment variables:

| Variable | Meaning |
| --- | --- |
| `IMPORT_SPEC=path` | Use this file as `spec.md`; skip the "A writes the spec" step. |
| `IMPORT_PLAN=path` | Use this file as `plan.md`; skip the "A writes the plan" step. Requires `IMPORT_SPEC`. |
| `IMPORT_REVIEW=0/1` | Default `1`: imported artifacts go through the normal reviewer loop. `0`: skip the AI review loop for imported artifacts only. Requires `IMPORT_SPEC`. |

Rules:

- The CLI task argument stays required. It still feeds the PR title and
  body, branch naming, and later prompts.
- `IMPORT_PLAN` without `IMPORT_SPEC` is a config error. A plan is written
  against a spec; the tool does not reverse-engineer a spec from a plan.
- `IMPORT_REVIEW` set (to any value) without `IMPORT_SPEC` is a config
  error, so a leftover variable cannot silently change behavior.
- `IMPORT_SPEC` with `DUAL_SPEC=1` is a config error; dual candidate specs
  and an imported spec contradict each other.
- `IMPORT_REVIEW=0` only affects imported artifacts. Example: with
  `IMPORT_SPEC` set but no `IMPORT_PLAN`, the plan is written by A and
  reviewed by B as usual, even when `IMPORT_REVIEW=0`.

## Stage Flow Changes

Only the "write" half of the two authoring stages changes. Stage names,
`begin_stage`/`end_stage`, the run ledger, resume, and archiving are
untouched.

`write-spec` with `IMPORT_SPEC`:

1. Workflow copies the file to `<spec_dir>/spec.md` (the original is never
   modified) and archives the imported original.
2. `IMPORT_REVIEW=1`: B runs the normal review loop; A answers `review.md`
   and edits the imported spec to fix blockers.
3. `HUMAN_GATE=1`: normal human approval, edits allowed.
4. Normal commit via the owner agent (`commit-approved-work` prompt), normal
   `end_stage`.

`write-implementation-plan` with `IMPORT_PLAN`: same shape, with the plan
review scope, `HUMAN_GATE_PLAN`, and the phased structure check when
`PHASES=1`.

Roles are unchanged: A is owner, B is reviewer (`set_spec_roles_from_slot`
with slot A).

## Validation and Preflight

All import validation runs at startup, before workspace setup and before any
AI call:

- Combination rules from the Interface section.
- Each import file must exist, be readable, and be non-empty.
- Spec: must contain a Markdown heading line (`#` prefix) whose text
  contains both "assumptions" and "open questions", case-insensitive. This
  mirrors what `write-spec` demands from the agent.
- Plan, basic mode: must contain at least one `- [ ]` task line.
- Plan, `PHASES=1`: must pass the existing phased plan structure check. The
  checker is extracted as needed so it can run at preflight against the
  source file.

## Resume Semantics

Import only applies to fresh runs; resume reuses the original decision:

- `IMPORT_SPEC`, `IMPORT_PLAN`, and `IMPORT_REVIEW` are recorded in the
  resume settings snapshot.
- On resume the values come from the snapshot. Setting a conflicting value
  in the environment refuses to resume, using the existing immutable-key
  mechanism, because changing them would change stage behavior mid-run.
- If a resumed run must redo an incomplete import stage and the source file
  no longer exists, the run aborts with a message naming the missing path
  and the archived copy.

## Provenance

- The original imported files are snapshotted into the run archive at import
  time.
- `pr-body.md` states, per artifact, whether it was generated or imported,
  and whether the AI review ran. The current wording, which implies every
  artifact passed cross-review, is only used when that is true.

## Companion Deliverables

- A format contract document in `docs/` (English) describing exactly what an
  importable spec and plan must contain: required sections, checkbox task
  format, phased structure rules.
- A reusable prompt template in `resources/` that users can feed to their
  own interactive tool ("produce a spec in this format") so external
  brainstorming output lands in the contract format on the first try.

## Testing

- Unit: combination rejections, file validation (missing, empty, missing
  section, missing checkbox), phased structure check at preflight, snapshot
  round-trip and immutable conflict for the three new keys.
- Integration: fake-agent runs covering import spec only, import spec plus
  plan, `IMPORT_REVIEW=0`, and provenance wording in `pr-body.md`.
- E2E: one live scenario with an imported spec and plan, reusing the
  existing E2E harness (cost-bounded: a single scenario).
