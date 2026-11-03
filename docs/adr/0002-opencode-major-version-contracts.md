# Speak OpenCode 1 and OpenCode 2 as different contracts

Date: 2026-11-03
Status: Accepted

## Context

The built-in OpenCode support was written for OpenCode 1. A non-interactive
call is `opencode run --format json --auto`. A worker resume appends
`--session <id>`. The model is `-m provider/model`. A reasoning level is a
separate `--variant` in that slot's args. Quota detection reads
`error.data.statusCode`, so a 429 counts even when the provider's text does
not say "rate limit".

OpenCode 2 uses the same `opencode` command and replaces OpenCode 1. It does
not install beside it. Upstream still ships the 1.x line. Moving to 2.x is an
explicit `opencode upgrade --major`, which OpenCode 1.19 does not do on its
own. The `--variant` form in both READMEs is an OpenCode 1 setting.

The two lines do not accept one invocation:

- OpenCode 2 quotes any positional argument that contains a space, and
  escapes quotes inside it. Workflow prompts contain spaces, so the model
  would receive a different prompt from the one AAC built.
- `opencode run` on OpenCode 2 has no `--variant` flag. The variant is a
  `#variant` suffix on the model reference.
- The HTTP status on a provider error moved from `error.data.statusCode` to
  `error.status`.
- `--session` on OpenCode 2 creates a conversation when that id does not
  exist, and the new conversation keeps the id it was given. The events then
  look like a resume of an empty conversation.

The captured id lives only in the workflow process. Entering a stage clears
it. `RESUME_RUN` restores workflow state and does not restore it. A new
process therefore cannot resume an OpenCode conversation from a previous
process, on either product line.

This repository already uses "v1" and "v2" for unrelated plans. Those words
are not names for these two product lines. The names are OpenCode 1 and
OpenCode 2.

## Decision

When a run will invoke `opencode`, AAC reads the major version once during
startup. The version is not stored in the run snapshot. The next process,
including one started with `RESUME_RUN`, reads it again.

Major version 1 keeps the contract AAC already speaks. Major version 2 uses
the contract in ADR 0003. A version string with no major number, including
the development build that prints `local`, is refused at startup. AAC does
not guess a contract.

A run that never invokes `opencode` does not need the binary and does not
read its version.

## Considered options

- **OpenCode 2 only.** Rejected. Machines that have not taken the explicit
  upgrade are still on a supported OpenCode line, and the settings this
  project already documents would fail there.
- **One command line for both.** Rejected. Prompt quoting, the variant flag,
  the error record, and a missing session id differ. One invocation is wrong
  on one of the two lines.

## Consequences

- Support for OpenCode 1 stays until a later decision removes it. This one
  does not expire that contract.
- The rule that rejects two variant values on one slot applies to both
  product lines. It is stated with the OpenCode 2 variant rules in ADR 0003,
  because that is where a variant value is interpreted.
