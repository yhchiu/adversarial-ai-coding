# Slot-Specific Agent Arguments

Status: Ready for implementation

## Problem Statement

The project has two different ways to pass extra command-line arguments to an
agent. Built-in agents use command-wide variables such as `CODEX_ARGS`, while
custom agents use slot variables such as `AGENT_A_ARGS`. The implementation
slot uses both patterns: a built-in implementation agent receives its
command-wide arguments and then `IMPL_ARGS`.

This is hard to understand. A user can set `AGENT_A=codex` and reasonably
expect `AGENT_A_ARGS` to affect Codex, but it does not. A and B also cannot use
the same built-in CLI with different effort or other CLI settings because they
share one command-wide argument variable.

The mixed model also makes ownership unclear. Agent selection and model
selection already belong to slots, but extra arguments sometimes belong to a
CLI name and sometimes belong to a slot. This makes execution, validation,
resume state, and recorded metadata harder to reason about.

## Solution

Make every extra-argument setting belong to an agent slot:

| Slot | Agent | Model | Extra arguments |
|---|---|---|---|
| A | `AGENT_A` | `MODEL_A` | `AGENT_A_ARGS` |
| B | `AGENT_B` | `MODEL_B` | `AGENT_B_ARGS` |
| I | `IMPL_AGENT` | `IMPL_MODEL` | `IMPL_ARGS` |

Remove `CLAUDE_ARGS`, `CODEX_ARGS`, `AGY_ARGS`, and `OPENCODE_ARGS` from the
supported configuration. Reject them clearly instead of ignoring them.

Built-in slot arguments use the validation rules for the adapter selected by
that slot. Custom-agent arguments remain pass-through after shell-quoting
validation. Model selection remains separate from extra arguments so runtime
behavior and recorded metadata cannot disagree about the selected model.

When all `IMPL_*` settings are empty, implementation remains an exact alias of
the selected owner and therefore uses the owner's slot arguments. When any
`IMPL_*` setting is present, implementation becomes an independent I slot and
uses only `IMPL_ARGS`.

## User Stories

1. As a user of a built-in agent in slot A, I want to pass its extra arguments through `AGENT_A_ARGS`, so that the variable name matches the slot I configured.
2. As a user of a built-in agent in slot B, I want to pass its extra arguments through `AGENT_B_ARGS`, so that A and B have the same configuration shape.
3. As a user of the implementation slot, I want `IMPL_ARGS` to be the only extra-argument source for an independent I slot, so that its behavior is predictable.
4. As a user running Codex in both A and B, I want each slot to use a different reasoning effort, so that the two roles can have different cost and quality settings.
5. As a user running Claude in both A and B, I want each slot to use different CLI options, so that the slots do not share hidden command-wide state.
6. As a user running Agy in both A and B, I want each slot's arguments to stay isolated, so that changing one role does not change the other.
7. As a user running OpenCode in both A and B, I want each slot's arguments to stay isolated, so that variants and agent choices can differ by role.
8. As a user of a custom agent, I want `AGENT_A_ARGS` and `AGENT_B_ARGS` to remain pass-through, so that the workflow does not guess the wrapper's argument contract.
9. As a user of a custom implementation agent, I want `IMPL_ARGS` to remain pass-through, so that the wrapper can own its model and session flags.
10. As a user, I want a clear error when I pass any removed command-wide argument variable, so that I do not believe an ignored setting took effect.
11. As a user, I want removed variables to fail even when their values are empty, so that stale shell and CI configuration is visible.
12. As a user who passed more than one removed variable, I want one error to list all of them, so that I can fix my configuration in one step.
13. As a user of a translated CLI, I want the removed-variable error to use the existing locale system, so that the error follows the rest of the product.
14. As a user, I want the error to point to `AGENT_A_ARGS`, `AGENT_B_ARGS`, and `IMPL_ARGS`, so that the replacement is obvious.
15. As a user of a built-in agent, I want model flags rejected in slot arguments, so that `MODEL_A`, `MODEL_B`, and `IMPL_MODEL` remain the single source of truth.
16. As a user, I want workflow-owned session, output, and sandbox flags rejected for the selected built-in adapter, so that extra arguments cannot break workflow control.
17. As a user, I want rules from unrelated adapters ignored, so that a valid Codex option is not rejected only because Claude gives the same token another meaning.
18. As a user, I want invalid shell quoting reported against the exact slot variable, so that I know which setting to fix.
19. As a user, I want the installed CLI to validate normal option names, option values, and model compatibility, so that this project does not maintain a stale model capability table.
20. As a user who leaves every `IMPL_*` setting empty, I want implementation to use the exact owner configuration, so that the default behavior remains simple.
21. As a user who sets any `IMPL_*` value, I want I to stop inheriting owner arguments, so that an implementation override creates a real independent slot.
22. As a user who sets only `IMPL_MODEL`, I want the independent I slot to use no extra arguments unless I also set `IMPL_ARGS`, so that hidden owner settings do not leak into implementation.
23. As a user who omits `IMPL_MODEL` while I uses the same built-in adapter as its owner, I want the existing safe model inheritance rule to remain, so that I do not need to repeat the model unnecessarily.
24. As a user who changes the implementation adapter, I do not want the owner's model name inherited, so that a model name is never sent to the wrong CLI.
25. As a dual-spec user, I want `IMPL_ARGS` checked against the adapter that actually becomes the owner, so that the unused candidate does not reject valid arguments.
26. As a dual-spec user whose candidates use the same adapter, I want invalid `IMPL_ARGS` rejected at startup, so that a known error fails early.
27. As a user resuming a run, I want A, B, and I slot arguments saved and restored, so that resumed execution matches the recorded configuration.
28. As a user resuming a run, I want the existing non-empty override rule to remain, so that this change does not also redesign resume clearing behavior.
29. As a user with an old settings snapshot, I want a clear schema error, so that an unsupported run never resumes with partly ignored configuration.
30. As a user reading logs, artifact metadata, or metrics, I want each record to show only the arguments used by that agent slot, so that diagnostics and cost attribution match execution.
31. As a maintainer, I want runtime argv and metadata to use one argument-resolution seam, so that the two views cannot drift apart.
32. As a maintainer, I want active documentation to describe only slot-specific arguments, so that examples teach the supported interface.
33. As a maintainer, I want historical plans and reviews left unchanged, so that they continue to describe the design that existed when they were written.

## Implementation Decisions

- The supported public argument variables are `AGENT_A_ARGS`,
  `AGENT_B_ARGS`, and `IMPL_ARGS`. The four adapter-wide variables are removed.
- Configuration rejects removed variable names in both the launch environment
  and an explicitly supplied snapshot mapping. Presence is enough to fail;
  an empty value is still rejected.
- A single error reports every removed variable that was present and points to
  all three supported replacements. The error is translated through every
  existing locale.
- Removed variables are deleted from the settings model, snapshot allowlist,
  and snapshot writer. Their names remain only in a constant used to report
  unsupported input.
- The settings snapshot schema becomes version 2. Schema 1 snapshots are not
  migrated and cannot be resumed.
- Argument resolution is slot-based for built-in and custom agents alike. A
  resolves only `AGENT_A_ARGS`, B resolves only `AGENT_B_ARGS`, and I resolves
  only `IMPL_ARGS`.
- Runtime argv, log banners, artifact metadata, run metadata, and metrics all
  consume the same resolved slot-argument source.
- Slot arguments follow the slot in every workflow role. They do not change
  meaning when a slot acts as worker, reviewer, planner, or implementer.
- Built-in adapters are recognized only by the exact names `claude`, `codex`,
  `agy`, and `opencode`. Wrappers, paths, aliases, and different casing remain
  custom agents.
- Every slot argument string is parsed with POSIX shell quoting. Quoting errors
  name the slot variable that supplied the value.
- A built-in slot applies only its selected adapter's reserved-argument rules.
  Validation does not use a union of all built-in rules.
- All built-in adapters reject `--model` and `-m` forms in slot arguments.
  Codex also rejects model changes through `-c` or `--config`. Models continue
  to come only from `MODEL_A`, `MODEL_B`, and `IMPL_MODEL`.
- Claude keeps its existing protection for workflow-owned session and output
  options, including continue/resume controls, session identifiers, output
  format, verbosity, and JSON schema controls.
- Codex keeps its existing protection for JSON stream control, resume,
  sandbox selection, sandbox bypasses, ephemeral execution, and
  `sandbox_mode` configuration.
- Agy keeps its existing protection for log-file and conversation/session
  controls.
- OpenCode keeps its existing protection for output format, session and
  continuation controls, fork/attach behavior, automatic execution, sharing,
  stored-command replacement, and working-directory control.
- Custom-agent slot arguments receive quoting validation only. Their flags and
  values are otherwise passed through unchanged.
- The workflow does not validate ordinary option names, ordinary option
  values, reasoning-effort values, or compatibility between an option and a
  model. The selected CLI owns those checks.
- A and B are validated at startup because their adapters are already known.
- An explicit `IMPL_AGENT` is validated at startup. Without one, a single
  known owner adapter is also validated at startup.
- Under dual-spec selection, when A and B use different adapters and
  `IMPL_AGENT` is absent, startup validates only `IMPL_ARGS` quoting. The full
  adapter-specific validation runs after the owner is selected. When both
  candidates use the same adapter, full validation may run at startup.
- When all `IMPL_*` values are empty, implementation uses the exact owner
  reference. This includes the owner's model, slot arguments, and session
  identity.
- When any `IMPL_*` value is present, implementation uses an independent I
  reference. Its arguments come only from `IMPL_ARGS`, even when it uses the
  same adapter as its owner.
- Existing implementation model inheritance remains: an empty `IMPL_MODEL`
  may inherit from the owner only when the implementation and owner commands
  match. Changing commands never carries a model name across adapters.
- Existing custom-agent command conflict and session-safety rules remain in
  place.
- A and B may use the same built-in CLI with different models and arguments.
  Their slot identity prevents one slot's session ID from being reused by the
  other slot when control changes between refs.
- Resume keeps the current non-empty environment override rule. An empty
  `AGENT_A_ARGS`, `AGENT_B_ARGS`, or `IMPL_ARGS` does not clear a stored value;
  changing that rule is a separate design problem.
- Active English and Traditional Chinese setup, troubleshooting, session, and
  parity documentation is updated. Historical plans, reviews, todos, and
  design records are not rewritten.
- This feature does not introduce a changelog or change the package version.
- The implementation is delivered as one coherent Conventional Commit after
  the complete test suite passes: `feat(agents): use slot-specific adapter
  arguments`.

## Testing Decisions

- Tests assert public behavior rather than private helper structure. A good
  test observes accepted or rejected configuration, the final adapter argv,
  persisted state, or recorded metadata.
- The configuration boundary verifies that every removed variable is rejected
  from both environment and snapshot input, including empty values, and that
  multiple names appear in one localized error.
- Snapshot tests verify that new snapshots use schema 2, contain only the
  three slot argument fields, round-trip those fields, and reject schema 1.
- Adapter validation tests cover A and B separately for Claude, Codex, Agy,
  and OpenCode. They verify model restrictions, workflow-owned flags,
  adapter-specific behavior, quoting failures, and the absence of a cross-
  adapter rule union.
- Custom-agent validation tests verify that A, B, and I continue to pass
  arbitrary tokens through after quoting succeeds.
- Argument-resolution tests verify that each `AgentRef` resolves only its own
  slot arguments. The same built-in command in A and B must produce different
  resolved values when the settings differ.
- Existing adapter runner seams are used with fake subprocess runners to
  inspect complete fresh and resumed argv without contacting external
  services. All four built-in adapters are covered.
- Implementation tests verify exact owner aliasing when every `IMPL_*` value
  is empty and argument isolation when any override is present.
- Dual-spec tests verify early validation for one known adapter, deferred
  validation for different candidates, and final validation against the
  selected owner.
- Archive tests verify artifact metadata, run metadata, log metadata, and
  metrics for different A/B arguments on the same built-in CLI. These records
  must match the arguments present in the runner argv.
- Documentation tests are updated to require the slot-specific interface and
  reject active examples of the removed variables.
- The complete existing pytest suite is the final regression gate.
- Live calls to Claude, Codex, Agy, or OpenCode are not part of acceptance.
  They depend on local installation, credentials, quota, network access, and
  external CLI versions, while fake-runner integration tests can verify the
  workflow's full argv contract deterministically.

## Out of Scope

- Supporting or migrating the removed command-wide argument variables.
- Migrating schema 1 resume snapshots.
- Adding aliases for built-in agent command names or detecting the CLI behind
  a custom wrapper.
- Validating whether a model supports a reasoning-effort value or another
  ordinary CLI option.
- Changing how empty environment values clear persisted settings.
- Redesigning custom-agent session ownership or command-conflict rules.
- Adding a new shared-arguments variable.
- Calling real external agent services in acceptance tests.
- Rewriting historical design documents.
- Creating a release process, changelog, or version bump.

## Further Notes

This is an intentional breaking change. Users who want the same arguments in
more than one slot must repeat them. That small duplication is preferred over
a second shared source with precedence and inheritance rules.

The key invariant is: one slot has one agent, one model setting, and one
extra-argument setting. Execution and observability must resolve that same
slot configuration.

The architectural reason for this invariant is recorded in
[adr.md](adr.md).
