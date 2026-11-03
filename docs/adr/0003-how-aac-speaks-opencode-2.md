# Speak to OpenCode 2 without reusing the OpenCode 1 command shape

Date: 2026-11-03
Status: Accepted

## Context

ADR 0002 keeps the OpenCode 1 contract and requires a separate one for
OpenCode 2. This is that contract. Each rule below is one that a later edit
would be tempted to fold back into the OpenCode 1 shape.

OpenCode 2 quotes a positional argument that contains a space. Splitting the
prompt into words and joining them again would keep single spaces and drop
newlines and repeated spaces. The prompt is the task AAC assembled, including
verdict examples and code, so the model has to receive those bytes unchanged.
OpenCode 2 also appends a `--` tail to the message. A bare word in the slot
args becomes part of the message too. Either one rewrites the prompt that
stdin was meant to protect.

Users set a reasoning level with `--variant`, which OpenCode 2 will not
accept as a flag. OpenCode 2 takes `provider/model#variant`, and it cannot
apply a variant before a model is selected. The archive records the user's
`MODEL_*` and slot args. It does not record the argv of the child process.
Translating a variant only for the child keeps that archive honest. Two
variant values at once would make the archive and the call describe different
levels, which is the same defect `--model` in slot args was reserved to
prevent.

A worker resume is the only place AAC sends a captured id, and only inside
the process that captured it. OpenCode 2 will create an empty conversation
under an id it does not know. The events carry that same id, so AAC would
keep it and treat the call as a resume. A conversation that exists and
happens to be empty is still the captured conversation. A check that fails
for any other reason, such as the server not answering, does not prove the
conversation is gone. Treating that as absence would throw away a
conversation that is still there.

The check and the call have to see the same server. `--server` and
`--standalone` choose it, and both are legal slot args today. A check that
always used the default background server would treat a conversation on a
private server as missing.

OpenCode 2 puts the provider's retry delay in `retryAfterMs`. The quota loop
already prefers an explicit delay over its own backoff, and it already
refuses to wait longer than `RETRY_MAX_RESET_WAIT`.

`external_directory` is allowed by default on OpenCode 2. OpenCode 1 asks
before touching a path outside the project. Those rules live in the user's
OpenCode configuration.

## Decision

**Prompt.** Worker, reviewer, and implementation calls on OpenCode 2 write
the prompt to stdin and pass no positional message. OpenCode 1 keeps the
positional prompt and a closed stdin.

On OpenCode 2, slot args are rejected at startup when they contain a bare
word or a `--` separator. A bare word is a token that does not start with
`-` and is not the value of a known value-taking flag. Those flags are
`--session` / `-s`, `--model` / `-m`, `--agent`, `--format`, `--file` / `-f`,
`--title`, `--server`, and `--worktree` / `-w`. A boolean flag does not take
the next token. A flag this list does not name does not take the next token
either. OpenCode 1 slot args are unchanged, except for the variant rule
below.

**Variant.** `--variant low` and `--variant=low` are one value. On OpenCode
2, a single variant together with a `MODEL_*` that has no `#variant` is
passed as `provider/model#variant` on `-m`. The `--variant` flag is not
forwarded. Two values on one slot, whether from `#` plus `--variant` or from
`--variant` repeated, fail startup and name each value. That two-value
failure applies on OpenCode 1 as well. `--variant` with no `MODEL_*` fails
startup on OpenCode 2, and is still forwarded on OpenCode 1. The archive
keeps the user's `MODEL_*` and the user's slot args.

**Resume.** AAC checks an OpenCode 2 worker resume before it passes
`--session`. Reviewer calls stay fresh and are not checked. The check
receives the same `--server` and `--standalone` as the call.

`Session not found: <id>` for the captured id is absence. AAC warns, drops
the id, and makes a fresh call. An id reported by that fresh call is the new
captured id. A fresh call that reports none leaves the process with no id,
which is the existing warning. Any other result of the check fails the call.
The failure text is the call's output. The call does not become fresh, and
it is not a quota retry. A conversation that exists is a resume even when it
has no messages.

**Quota.** `error.status` is read the way `error.data.statusCode` was read.
A 429 is a rate limit even when the text does not say "rate limit". When a
quota failure includes `retryAfterMs`, that duration is the wait, still
subject to `RETRY_MAX_RESET_WAIT`. A missing or unusable value leaves the
current wording parser and backoff in place. Spending-limit text stays
non-retryable. Cost stays the sum of `step_finish` `part.cost` values.

**Permissions.** AAC does not write the user's OpenCode configuration. The
READMEs record that OpenCode 2 allows `external_directory` by default, and
the permission entry that restores the OpenCode 1 behavior.

## Considered options

- **Let OpenCode 2 create the missing conversation.** Rejected. The captured
  id would come back on the events, with none of the previous conversation.
- **Abort the whole run when the conversation is gone.** Rejected. A fresh
  call can still do the step from the archived prompt. That is already what
  happens when no id was captured.
- **Split the prompt on whitespace.** Rejected. Newlines and repeated spaces
  would not survive.
- **Treat every failed existence check as a missing conversation.**
  Rejected. A server that did not answer is not evidence that the
  conversation is gone.

## Consequences

- The English and Traditional Chinese READMEs, and the agent session
  lifecycle docs, change together with the code. They currently describe one
  OpenCode command shape.
- Tests pin the version choice, the stdin prompt, the folded model, the
  not-found check, and `error.status` with fixtures. They do not need a live
  `opencode` binary.
- A flag OpenCode adds later cannot be passed in slot args until this list
  names it. That delay is the cost of not letting an unknown flag hide a
  bare word.
