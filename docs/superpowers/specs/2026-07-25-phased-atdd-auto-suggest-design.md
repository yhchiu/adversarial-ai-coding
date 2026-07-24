# Phased ATDD Auto-Suggestion at the Spec Human Gate

Date: 2026-07-25
Status: Approved design, pending implementation plan

## Context

Phased ATDD mode (`PHASES=1`) replaces the single up-front acceptance-test
stage with a per-phase loop. It pays off when the task splits into two or
more vertical features that can be accepted independently; it is overhead
for a single-feature task, a bugfix, or a refactor.

Today the user must decide `PHASES` before the run starts, when the only
information available is the one-line task description. The best moment to
judge fitness is after the spec exists: the spec spells out how many
independently observable features the task contains, and the reviewer has
just read the whole document. The spec human gate is also the last cheap
point to switch — it runs before `write-implementation-plan`, where the
stage graph first diverges on `PHASES`.

This feature makes the spec reviewer judge phased fitness as part of its
existing review, and lets the human enable Phased ATDD at the spec gate
when the reviewer recommends it.

## Decisions Already Made

- The judgment is made by the **spec reviewer inside the existing review
  call** — no extra AI call, no Python heuristic over free-form markdown.
  Consequence: paths with no spec review (`IMPORT_REVIEW=0`) never get a
  suggestion.
- The recommendation travels in a **separate side file**
  (`{{WF}}/phased-suggestion.json`), never inside `verdict.json`. The
  verdict schema is strict, has recovery machinery, and has a history of
  prompt-compliance bugs; it must not grow fields.
- The suggestion is **suppressed** when the user explicitly set `PHASES=0`
  in the environment, when `IMPORT_PLAN` is set, and (interactively) when
  `HUMAN_GATE=0`. See Arming Conditions.
- The workflow **never flips `PHASES` automatically**. With `HUMAN_GATE=0`
  the recommendation is logged and nothing else happens.
- `PHASES` remains immutable across resume. The one sanctioned in-run flip
  rewrites the settings snapshot atomically, so every later resume still
  sees a single consistent value.

## Goals

- After the spec is approved by review, tell the human at the spec gate
  when the reviewer judges the task a good fit for Phased ATDD, with a
  one-to-two sentence reason.
- Let the human enable phased mode with one keystroke at that gate, safely
  persisted for resume.
- Zero behavior change when the feature is not armed: identical prompts,
  identical gate interaction.
- The suggestion machinery must never block or fail a run: a missing or
  malformed side file means "no suggestion".

## Non-Goals

- No automatic enabling of `PHASES` under any condition.
- No "suggest turning phased off" direction; the mechanism only proposes
  enabling it when it is currently off.
- No re-asking after the user declines: the write-spec stage completes and
  resume never re-runs its gate.
- No change to `verdict.json`, the review loop's approval logic, or the
  dual-spec candidate reviews.
- No new CLI flags; `PHASES` stays the only user-facing switch.

## Arming Conditions

The mechanism is "armed" only when all of these hold:

1. `settings.phases` is off (effective `PHASES=0`).
2. `PHASES` was **not** explicitly set in the environment. `Settings` gains
   `phases_explicit: bool`, true when the launching environment contained
   `PHASES` (any value). The field is derived per attempt and is **not**
   written to the snapshot. Explicit `PHASES=0` means the user already
   decided; it also avoids a resume trap — flipping to 1 while the user's
   shell still exports `PHASES=0` would trip the `IMMUTABLE_KEYS` conflict
   check on the next resume.
3. `IMPORT_PLAN` is empty. An imported plan was written outside the run;
   enabling phased at the gate would retroactively require it to be a valid
   phased plan and the import validation would fail the run.
4. A reviewer actually reviews the final spec (true everywhere except the
   `IMPORT_SPEC` path with `IMPORT_REVIEW=0`; this falls out of the design
   rather than being checked separately).

When not armed, no prompt text is added and the gate is unchanged.

## Reviewer Judgment and Transport

When armed, the workflow appends a fixed instruction block to the spec
review scope — `review-scope-spec` in the single-spec path, and the
dual-spec **final** review scope in the dual-spec path (candidate reviews
are untouched):

- After finishing the review, additionally judge whether the task suits
  Phased ATDD and write `{{WF}}/phased-suggestion.json` as one line of
  JSON: `{"phased": true|false, "reason": "one or two sentences"}`.
- Fitness criterion, stated in the prompt: the spec describes **two or
  more vertical features that can each be accepted independently at a
  stable seam** (the same feature-not-layer definition the phased plan
  format requires). A single feature, a bugfix, a refactor, or a
  documentation task is not a fit.

Mechanics follow the verdict conventions without touching them:

- The workflow pre-creates the file with `{"phased": false, "reason": ""}`
  before each armed review round, so a reviewer that ignores the
  instruction yields "no suggestion", not a stale or missing file.
- Multi-round reviews overwrite it each round; the last (approving) round
  wins.
- Each round's file is archived alongside the verdict as
  `phased-suggestion-<stage-slug>-r<N>.json`.

## Spec Gate Interaction and the Flip

`human_gate_spec` (both call sites: single-spec workflow and dual-spec
`apply_dual_spec_decision`) becomes:

1. Unchanged checkpoint banner and the existing "y approves the spec"
   question. Anything but y/Y still aborts the run; no suggestion is shown
   to someone who is about to abort.
2. After spec approval, if armed and `phased-suggestion.json` exists,
   parses, and has `phased: true`: print the reviewer's reason, then ask
   `Enable Phased ATDD for this run? [y/N]`. Anything but y/Y declines —
   same convention as the existing gate.
3. On yes:
   - `ctx.settings = dataclasses.replace(ctx.settings, phases=True)`.
   - Rewrite the run-state settings snapshot (`settings.json`) with
     `phases = "1"`, using the existing atomic-write path. Later resumes
     read `PHASES=1` from the snapshot; the environment does not conflict
     because arming excluded explicit `PHASES` values.
   - Log and notify the decision; record it in the archive log.
4. With `HUMAN_GATE=0` the gate still returns early for approval, but if an
   armed suggestion with `phased: true` exists, one log line is emitted:
   `reviewer suggests Phased ATDD: <reason>; HUMAN_GATE=0, not asking`.
   Nothing is flipped.

Timing safety: the spec gate runs inside the write-spec / finalize-spec
stage, before `write-implementation-plan` — the first stage whose behavior
depends on `PHASES`. No completed stage is invalidated by the flip.

## Error Handling and Edge Cases

- Side file missing, unreadable, invalid JSON, or wrong field types →
  treated as "no suggestion"; the question is silently skipped. The
  suggestion path can never abort or fail a run.
- `phased: false` → no output at the gate (at most a debug log line).
- User declines → nothing changes, no re-ask (resume never re-runs a
  completed gate stage).
- Dual-spec: only the final merged/picked spec review carries the
  instruction, so at most one suggestion file exists per run attempt.
- Declined or unused suggestion files are still archived for the run
  record.

## Testing

Unit tests:

- Arming matrix: phases on/off × `phases_explicit` × `IMPORT_PLAN` set /
  empty, including `Settings.from_env` deriving `phases_explicit` from
  environment presence.
- Side-file parsing: valid true/false, missing file, malformed JSON, wrong
  types — all non-true outcomes are silent.
- Gate flow: accept (settings replaced, snapshot rewritten, log emitted),
  decline (no changes), `HUMAN_GATE=0` (log-only, no flip).
- Resume consistency: after an accepted flip, `Settings.from_env` with an
  empty environment plus the rewritten snapshot yields `phases=True` and
  passes the `IMMUTABLE_KEYS` check.
- Prompt rendering: the instruction block is appended exactly when armed,
  in both the single-spec and dual-spec final review scopes; candidate
  review scopes never contain it.

Integration test (style of `test_phased_integration.py`): a run that starts
with `PHASES` unset, gets a `phased: true` suggestion, answers yes at the
spec gate, and proceeds through phased plan validation and the per-phase
loop.

## Documentation

- README.md and README.zh-TW.md: the `PHASES` table row and the Phased
  ATDD section gain the spec-gate suggestion; reword "cannot change across
  resume" to explain the one sanctioned flip keeps the snapshot consistent.
- how-it-works docs and `flow-default.txt` / `flow-dual-spec.txt` /
  `flow-phased-atdd.txt`: add the suggestion step at the spec gate where
  those describe it.
- `resources/AGENTS.template.md`: document the `phased-suggestion.json`
  side-file convention if it lists the verdict convention.
