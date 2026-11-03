# Speak OpenCode 1 and OpenCode 2 as different contracts

Status: ready-for-agent

ADR 0002 decides which product line is spoken. ADR 0003 decides how OpenCode 2
is spoken. This spec is the behavior to build. The words OpenCode 1, OpenCode
2, Fresh, and Resume have the meanings in the glossary.

## Problem Statement

AAC's built-in OpenCode support speaks OpenCode 1. OpenCode 2 replaces the
same `opencode` command instead of installing beside it, and it does not
accept that contract.

A user who upgrades, or who installs OpenCode 2 on a new machine, keeps the
same settings. Those settings pass `--variant` and expect the prompt AAC
assembled to reach the model unchanged. OpenCode 2 rejects `--variant`,
rewrites a spaced positional prompt, reports a provider status on a different
field, and creates an empty conversation when a resume id is unknown. The
empty conversation reuses the id, so a later worker call looks like Resume
and has none of the previous conversation.

A user who has not upgraded is still on a supported OpenCode 1 line. Treating
every `opencode` binary as OpenCode 2 would make those runs fail.

## Solution

AAC reads the installed major version once at startup when the run will
invoke `opencode`. OpenCode 1 keeps the contract it has today, except that
two variant values on one slot are now a startup error on both lines.
OpenCode 2 is spoken with its own contract: the prompt is delivered unchanged
on stdin, one `--variant` is folded into the model reference, a captured id
is sent only when that conversation is known to exist, and a 429 still counts
as a rate limit.

Published settings do not have to change when the user upgrades. The archive
keeps the settings the user wrote. AAC does not edit the user's OpenCode
configuration.

## User Stories

1. As a user whose run never invokes `opencode`, I want startup to skip the version read, so that I do not need OpenCode installed.
2. As a user whose slot A, slot B, or an independent implementation slot uses the built-in `opencode` command, I want the major version read once at startup, so that every OpenCode call in that process speaks one contract.
3. As a user who starts the next process, including with `RESUME_RUN`, I want the version read again, so that the binary then on the machine is the one that is spoken.
4. As a user whose version output contains no major number, including a development build that prints `local`, I want startup to fail and say that the version could not be read, so that AAC does not guess a contract.
5. As a user on OpenCode 1, I want the major number 1 to select the OpenCode 1 contract, so that a supported 1.x install keeps working.
6. As a user on OpenCode 2, I want the major number 2 to select the OpenCode 2 contract, so that an upgraded install is spoken correctly.
7. As a user on OpenCode 1, I want a non-interactive call to stay `opencode run --format json --auto` with the prompt as the positional argument, so that the current call shape is unchanged.
8. As a user on OpenCode 1, I want the prompt's stdin left closed and empty, so that OpenCode 1 does not append an extra body to the positional prompt.
9. As a user on OpenCode 1, I want a worker Resume to pass `--session` with the captured id and no existence check, so that the current resume path is unchanged.
10. As a user on OpenCode 1, I want one `--variant` forwarded, including when no `MODEL_*` is set, so that OpenCode 1 can still apply it.
11. As a user on OpenCode 1, I want bare words and a `--` separator in slot args left as they are today, so that this work does not tighten the OpenCode 1 argument contract beyond variant conflicts.
12. As a user on OpenCode 1, I want a provider 429 in `error.data.statusCode` to keep counting as a rate limit, so that the existing event shape still waits.
13. As a user on OpenCode 1, I want a non-JSON line from the CLI to stay visible and eligible for the existing quota wording, so that a failure before any event still surfaces.
14. As a user on OpenCode 2, I want the prompt written to stdin and no positional message, so that spaces, newlines, and quotes reach the model unchanged.
15. As a user on OpenCode 2, I want that stdin delivery for the worker, the reviewer, and the implementation slot, so that every role is protected.
16. As a user on OpenCode 2, I want a bare word in slot args rejected at startup, so that it cannot become a second message beside the prompt.
17. As a user on OpenCode 2, I want a `--` separator in slot args rejected at startup, so that the tail cannot be appended to the prompt.
18. As a user on OpenCode 2, I want the value of `--file`, `--title`, `--agent`, `--model`, `-m`, `--format`, `--session`, `-s`, `--server`, and `--worktree` / `-w` kept as a value, so that a legal option is not mistaken for a bare word.
19. As a user on OpenCode 2, I want `--worktree` alone to remain a flag, and the following word to remain its name only when that word does not start with `-`, so that the optional name is not a bare word and the next flag is not eaten.
20. As a user on OpenCode 2, I want a boolean flag such as `--thinking` not to swallow the next word, so that a bare word after it is still rejected.
21. As a user on OpenCode 2, I want a flag AAC does not recognize to leave the next word visible, so that an unknown flag cannot hide a bare word.
22. As a user on OpenCode 2 with `MODEL_*` set to `provider/model` and one `--variant low`, I want the call to pass `-m provider/model#low` and not pass `--variant`, so that OpenCode 2 accepts the reasoning level I already configured.
23. As a user, I want `--variant=low` to be the same single value as `--variant low`, so that both spellings select one level.
24. As a user on OpenCode 2 whose `MODEL_*` already contains `#high` and who also sets `--variant`, I want startup to fail and name both values, so that the call and the settings cannot describe different levels.
25. As a user on OpenCode 2 whose `MODEL_*` already contains `#variant` and who sets no `--variant`, I want that model string passed through on `-m`, so that the suffix I wrote is the one that is used.
26. As a user who sets `--variant` twice on one slot, on either product line, I want startup to fail and name both values, so that the last value is not silently kept.
27. As a user on OpenCode 2 who sets `--variant` and no `MODEL_*`, I want startup to fail, so that a variant is not applied before a model is selected.
28. As a user who sets `--variant` with no value, on either product line, I want startup to fail, so that a half-written flag is not forwarded.
29. As a user of the implementation slot, I want `IMPL_ARGS` to follow the same variant and OpenCode 2 bare-word rules as the A and B slot args, so that I is not a second contract.
30. As a dual-spec user, I want an implementation-slot violation rejected at startup only when every candidate owner would reject it, so that a rule belonging to the owner I have not chosen yet still waits for that choice.
31. As a reader of the archive, I want `MODEL_*` and the slot args recorded as I wrote them, so that folding `#variant` into the child process does not rewrite history.
32. As a user on OpenCode 2, I want the first worker call of a stage to stay Fresh and not look up a session, so that a call with no captured id does not invent one.
33. As a user on OpenCode 2, I want every reviewer call to stay Fresh and not be checked, so that review never continues the worker conversation.
34. As a user on OpenCode 2, I want a worker Resume to check that the captured conversation exists before `--session` is passed, so that OpenCode 2 cannot create an empty conversation under that id.
35. As a user who set `--server` or `--standalone` on the OpenCode 2 slot, I want the existence check to receive those same flags, so that the check sees the server the call will use.
36. As a user on OpenCode 2, I want `--server` and `--standalone` to remain legal slot args, so that choosing a server is not taken away.
37. As a user whose captured id is reported with `Session not found: <id>` for that exact id, I want a warning that this call is not the captured conversation, the id dropped, and a Fresh call made, so that the step still runs from the archived prompt.
38. As a user whose Fresh call then reports a new id, I want later worker calls in that same stage to Resume the new conversation, so that one miss does not repeat on every call.
39. As a user whose Fresh call reports no id, I want the process left with no captured id and the existing missing-id warning, so that the lost id is not sent again.
40. As a user whose existence check fails for any reason other than that exact id being missing, I want that call to fail and show the check's explanation, so that a server that did not answer is not treated as a lost conversation.
41. As a user in that failed check, I want the call not to become Fresh and not to be treated as a quota retry, so that AAC does not wait and does not discard a conversation that may still exist.
42. As a user whose captured conversation exists and has no messages, I want it treated as Resume, so that only a confirmed missing id becomes Fresh.
43. As a user who enters the next stage, I want the captured id cleared as it is today, so that Resume still does not cross a stage boundary.
44. As a user who starts a new process with `RESUME_RUN`, I want no captured OpenCode conversation restored, so that restoring workflow state is not mistaken for Resume.
45. As a user on OpenCode 2, I want a 429 in `error.status` to count as a rate limit even when the message does not say "rate limit", so that a provider that only reports the status still waits.
46. As a user on OpenCode 2, I want the process exit code preserved, so that a non-zero failure is what allows the existing quota loop to retry a classified 429.
47. As a user on OpenCode 2 whose quota failure includes `retryAfterMs`, I want that duration to be the wait, so that the provider's delay is used instead of the generic backoff.
48. As a user whose `retryAfterMs` is longer than `RETRY_MAX_RESET_WAIT`, I want the run to stop as resumable without sleeping, so that the existing ceiling still holds.
49. As a user whose quota failure has no usable `retryAfterMs`, I want the current wording parser and backoff, so that a message that already says when to retry still works.
50. As a user who receives Grok spending-limit text, I want the run to stop as resumable without retrying, so that a hard quota is not slept through.
51. As a user, I want reported cost to remain the sum of `step_finish` costs, including a real zero, so that a missing report and a free model stay distinct.
52. As a user, I want workflow-owned OpenCode flags to stay rejected in slot args on both product lines, so that I cannot replace the prompt, the format, the session, or `--auto`.
53. As a maintainer, I want one reserved-flag list for both product lines, so that flags OpenCode 2 removed are still rejected while OpenCode 1 can still receive them from AAC itself.
54. As a user who hits a new startup error, I want it to name the slot variable and the conflicting values, and to use the existing settings-error locale path, so that I can fix the setting in the language the rest of startup uses.
55. As a user who hits a startup error, I want it to happen before a paid stage, so that a bad contract does not spend a model call.
56. As a reader of either README, I want to see that OpenCode 2 allows `external_directory` by default and the permission entry that asks again, so that I can keep the OpenCode 1 boundary without AAC editing my config.
57. As a reader of either README, I want the published `--variant` example to remain valid for both product lines, so that upgrading OpenCode does not require me to rewrite settings.
58. As a reader of either language, I want the same OpenCode facts, so that the two READMEs do not diverge.
59. As a reader of the session lifecycle in either language, I want OpenCode 2's Fresh call, the pre-resume check, and the reviewer's permanent Freshness described, so that the doc matches the calls.
60. As a user, I want AAC not to write or migrate my OpenCode configuration, so that my providers, deny rules, and plugins stay mine.

## Implementation Decisions

- The version is the first integer in the output of the resolved `opencode` command's version request. It is remembered for that process and is not part of the run snapshot. No integer means the version cannot be read.
- The OpenCode 1 invocation, event rendering, closed stdin, and unguarded `--session` resume stay as they are, aside from the shared two-value variant rejection.
- The OpenCode 2 prompt is the stdin body of `run`. The child is given no positional message. The body is the exact prompt string, in UTF-8, and the stream is closed after it.
- OpenCode 2 slot-arg grammar is applied only after the product line is known to be OpenCode 2. Tokens that start with `-` are flags. A known value-taking flag consumes the following token when that token does not start with `-`. `--worktree` and `-w` are optional: with no following value they are boolean. Boolean flags and unrecognized flags consume nothing. A `--` token is rejected. The known value-taking names are `session`, `s`, `model`, `m`, `agent`, `format`, `file`, `f`, `title`, `server`, `worktree`, and `w`.
- Variant collection looks at `MODEL_*` and at every `--variant` / `--variant=<value>` in that slot's args. Zero or one value is legal, subject to the OpenCode 2 rule that a variant requires a model. Two or more values fail startup and the message lists them. On OpenCode 2 the single value is appended as `#value` only when `MODEL_*` has no `#` already, and the flag is removed from the child argv. The archive continues to record the user's model and the user's raw slot args.
- Implementation-slot checks use the timing already used for reserved arguments. A violation every dual-spec candidate would hit fails at startup. A violation that depends on which candidate is chosen waits until that choice.
- Resume checking runs only for an OpenCode 2 worker call that already holds a captured id. It is a separate invocation of the same resolved command, it is not `run`, and it does not receive the prompt. It is given the slot's `--server` and `--standalone` only. Combined output that reports `Session not found:` for the captured id is absence. Anything else, including a check that does not run, fails that call through the ordinary non-quota failure path.
- On absence, the captured id is dropped before the Fresh call. The warning uses the same user-visible channel as the existing missing-id warning and says this call is not the captured conversation. The Fresh call's reported id, if any, becomes the captured id under the existing rule.
- Quota classification for OpenCode 2 reads `error.status` the way OpenCode 1 reads `error.data.statusCode`, and still appends the status so the existing 429 wording matches. A usable `retryAfterMs` on a classified quota failure is exposed on the call result. The existing retry loop prefers that explicit delay, then the wording parser, then backoff, and still refuses to sleep past `RETRY_MAX_RESET_WAIT`. Spending-limit text stays non-retryable. Cost remains the sum of numeric `step_finish` costs.
- The reserved OpenCode flag list stays one list for both product lines. AAC itself still passes the flags it owns. Users still cannot pass them in slot args.
- New startup failures use the existing settings-error type and locale path, and they name the slot variable.
- The English and Traditional Chinese READMEs, and both session-lifecycle docs, change in the same work. They describe the two contracts, the stdin prompt, the still-valid `--variant` setting, the resume check, and the `external_directory` default. The permission entry that restores the old ask is `{ "action": "external_directory", "resource": "*", "effect": "ask" }`. AAC does not write that entry into a user's config.
- No archive field is added or renamed.

## Testing Decisions

A good test observes behavior at the agreed seams and does not care which private helper parsed a line. The seams are startup validation and the worker and reviewer calls, plus the bilingual documentation tests that already lock user-visible sentences.

Drive startup validation and the worker and reviewer calls with a fake `opencode` on `PATH`. The fake answers the version request, the existence check, and `run` as separate invocations. Assert the startup error, the child's argv, the exact stdin bytes, whether `--session` was passed, the warning, a failed check versus a Fresh call, the captured id afterwards, the rendered text, the summed cost, the quota classification, and the exposed retry delay.

Do not add a new parser module or a test seam aimed at one. Existing event-rendering tests may change only where they would contradict the OpenCode 2 error status. New OpenCode 2 stories are asserted from the worker and reviewer calls.

Do not require a live `opencode` binary. Do not assert private function names or call order beyond the invocations the fake binary receives.

Prior art is the existing OpenCode argv and streaming tests, the existing 429 and spending-limit tests, and the bilingual documentation tests that already lock the OpenCode column and the session-lifecycle wording.

The documentation tests should fail if either language drops the OpenCode 2 prompt delivery, the still-legal `--variant` example, the resume check, or the `external_directory` note.

## Out of Scope

- Installing, upgrading, or configuring OpenCode for the user, including plugins and permission files.
- Persisting a captured conversation id across processes, or changing when a stage clears it.
- Treating `RESUME_RUN` as Resume of an OpenCode conversation.
- Accepting a version string with no major number.
- Speaking a custom command that happens to wrap `opencode`. That command stays pass-through.
- Changing the archive schema, the cost formula, or the spending-limit non-retry rule.
- Redesigning how stderr is merged into the OpenCode stream.
- Renaming tool summaries because OpenCode 2 renamed tools. The existing argument-key list still supplies the one-line summary.
- Adding an OpenCode HTTP client. The existence check is another invocation of the CLI.
- Running the workflow loop as a test of this contract. The workflow decides when a call is Fresh or Resume. It does not choose the OpenCode product line.

## Further Notes

The confirmed test seams are startup validation together with the worker and reviewer calls, and the existing bilingual documentation tests. The workflow loop is intentionally above this contract.

ADR 0002 and ADR 0003 are accepted. Where this spec and those ADRs disagree, the ADRs win and this spec should be corrected.
