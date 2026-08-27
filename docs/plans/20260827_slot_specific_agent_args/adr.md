# Make Extra Agent Arguments Slot-Specific

Date: 2026-08-27
Status: Accepted

Agent selection and model selection already belong to workflow slots, but
extra arguments currently use a mix of adapter-wide and slot-specific
settings. We will remove the adapter-wide settings and make A, B, and I the
only owners of extra agent arguments. This makes slot behavior consistent and
allows A and B to use the same built-in CLI with different settings.

## Context

The workflow has three agent slots: A, B, and I. A and B select the main
agents, while I can override the selected owner for implementation work. Each
slot already has its own agent and model setting.

Extra arguments do not follow that model today. Built-in agents read one
argument setting per CLI, so two slots using the same CLI must share the same
arguments. Custom agents read A/B slot settings, and a built-in implementation
agent combines its CLI-wide setting with `IMPL_ARGS`. A future maintainer could
reasonably assume `AGENT_A_ARGS` applies to `AGENT_A`, but that is only true for
custom agents.

Keeping two kinds of ownership would require permanent precedence and
inheritance rules. It would also make it easier for runtime argv and metadata
to report different effective settings.

## Decision

Extra arguments belong only to slots:

- A uses `AGENT_A_ARGS`.
- B uses `AGENT_B_ARGS`.
- I uses `IMPL_ARGS`.

This rule applies to built-in and custom agents. Built-in arguments are
validated with the rules for the adapter selected by their slot. Custom
arguments are passed through after shell-quoting validation. Models remain
separate and cannot be overridden through built-in slot arguments.

The old adapter-wide variables are rejected, even when empty. They are removed
from normal settings and persisted state, and the snapshot schema moves to
version 2 without migration.

Implementation is an exact alias of its owner only when every `IMPL_*` setting
is empty. Once any implementation override is present, I is independent and
does not inherit owner arguments. Safe same-command model inheritance remains
because model inheritance is already an explicit part of the I-slot contract.

When dual-spec candidates use different adapters and no implementation agent
is explicit, adapter-specific validation waits until the owner is known. This
avoids treating the rules of an unused candidate as part of the effective
adapter contract.

## Considered Options

**Keep adapter-wide arguments and add A/B overrides.** Rejected because it
creates two sources for the same setting. The project would need rules for
merge order, replacement, clearing, resume, and metadata forever.

**Keep the old variables as deprecated aliases.** Rejected because backward
compatibility is not required, and aliases would preserve the confusing model
the change is meant to remove.

**Let an independent I slot inherit owner arguments.** Rejected because a
partial hidden inheritance rule would make I different from A and B. Users can
copy an argument deliberately when two slots need the same value.

**Validate `IMPL_ARGS` against every possible dual-spec owner.** Rejected
because the intersection of unrelated adapter rules can reject arguments that
are valid for the owner eventually selected.

**Infer argument rules from the model name.** Rejected because model names do
not reliably identify a CLI, and model capabilities change outside this
project. The selected adapter defines syntax and reserved workflow flags; the
CLI defines ordinary values and model compatibility.

## Consequences

- This is a breaking configuration and snapshot change.
- A and B can use the same built-in CLI with different effort and other
  supported options.
- Users must repeat shared arguments across slots. This duplication is
  deliberate and keeps ownership visible.
- Any independent implementation override must state its own `IMPL_ARGS`.
- Runtime invocation, logs, archive metadata, and metrics can share one
  slot-based argument source.
- Custom wrappers keep control of their own argument contract.
- The workflow still does not promise that an ordinary argument value is
  supported by a selected model; the external CLI remains responsible for
  that validation.
