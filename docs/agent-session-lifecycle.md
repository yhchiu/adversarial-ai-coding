# Agent Session Lifecycle

English | [Traditional Chinese](agent-session-lifecycle.zh-TW.md)

This document explains when each workflow agent starts a fresh conversation
and when it resumes an existing one. It covers the default pipeline, Phased
ATDD, Dual Spec, imported artifacts, and `RESUME_RUN`.

## Process versus conversation

Every agent call launches a new CLI process. A resumed worker call is still a
new process, but the CLI receives an exact session, thread, or conversation ID
and reconnects that process to earlier conversation context.

In this document:

- **Fresh** means the call receives no workflow-managed conversation ID.
- **Resume** means the call receives the active worker's captured ID.
- **O** is the selected owner. It is slot A in the default flow.
- **R** is the other slot acting as reviewer. It is slot B in the default flow.
- **I** is the implementation ref created when any `IMPL_*` customization is
  set. With no `IMPL_*` customization, the implementer is O itself rather than
  a separate I ref.

## Core rules

1. **Every stage starts with no active worker session.** Entering a stage clears
   the captured ID and its owner, even when the same slot worked in the previous
   stage.
2. **There is one active worker session for the whole workflow process.** The
   workflow does not retain one session per A, B, and I slot.
3. **Only the same complete worker ref can resume.** A ref includes its slot,
   command name, and implementation base-slot identity. Changing the ref
   discards the active ID. Changing only the model does not change the ref and
   therefore does not itself discard the ID.
4. **Every reviewer call is fresh.** This includes later rounds of the same
   review loop. A reviewer call neither reads nor replaces the active worker
   session, so the worker can resume after receiving review findings.
5. **`RESUME_RUN` resumes workflow state, not agent context.** A new workflow
   process creates a new in-memory session holder. Captured agent IDs are not
   written to run state.
6. **Prompts do not depend on chat history.** Each call receives a pointer to a
   complete archived prompt, so a fresh handoff can reconstruct its task from
   files and repository state.

The usual review loop therefore behaves like this:

```text
worker fresh or resume
  -> reviewer fresh
  -> blocker found
  -> same worker resumes
  -> reviewer fresh again
```

## Built-in adapter behavior

The automatic lifecycle rules apply to the built-in agents:

| Agent | Fresh worker | Resumed worker | Reviewer |
| --- | --- | --- | --- |
| Claude | `claude -p ...` | `claude -p ... --resume <session-id>` | Always fresh; no `--resume` |
| Codex | `codex exec --json ...` | `codex exec resume --json ... <thread-id> <prompt>` | Always fresh; plain `codex exec` |
| Agy | `agy --print ...` | `agy --print ... --conversation <conversation-id>` | Always fresh; no `--conversation` |
| OpenCode | `opencode run --format json --auto ...` | `opencode run --format json --auto ... --session <session-id>` | Always fresh; no `--session` |

If a fresh worker call does not report an ID, the next worker call is fresh
again. If an established worker call omits an ID, the last known ID is retained.
The workflow never guesses by using Codex `--last`, Agy `--continue`, or
OpenCode `-c` / `--continue`.

Custom agent commands do not receive automatic session management. From the
workflow's perspective every custom call is independent. A custom wrapper may
implement its own continuity, but that behavior is outside the guarantees in
this document.

## Default stage matrix

The following table uses O=A and R=B unless Dual Spec selected different roles.

| Stage | Worker lifecycle | Reviewer lifecycle |
| --- | --- | --- |
| `write-spec` | O starts fresh. If review finds blockers, O resumes for repairs. With `IMPORT_SPEC`, the initial O call is absent. | Every R review round is fresh. |
| `commit-spec` | O starts fresh because this is a new stage, even if O wrote the spec. | No reviewer call. |
| `write-implementation-plan` | O starts fresh. Plan repairs, phased-format repairs, and the plan commit resume O after its first call in this stage. With `IMPORT_PLAN`, authoring is absent, so the first O call may instead be a repair or the commit itself; that call is fresh. | Every R review round is fresh. |
| `write-acceptance-tests` | Roles reverse: R starts fresh as the test author. Test repairs and the acceptance-test commit resume R. | Every O review round is fresh. |
| `write-code` | The implementer starts fresh and resumes across task implementation, build-gate repairs, and task commits. The later complete-gate and branch-review repair behavior depends on whether the implementer is O or a separate I ref; see below. | Every R branch-review round is fresh. |
| `final-review-and-fixes` | O starts fresh for self-review. Gate repairs, review repairs, and any final commit resume O. | Every R final-acceptance round is fresh. |
| `finish` | No agent call. | No agent call. |

### The O-to-I handoff inside `write-code`

With no `IMPL_AGENT`, `IMPL_MODEL`, or `IMPL_ARGS`, the implementation ref is O.
All worker calls in `write-code` therefore share the same ref, including O's
slot arguments (`AGENT_A_ARGS` or `AGENT_B_ARGS`), and can resume the same
conversation.

Setting any `IMPL_*` option creates a distinct I ref, even if I uses the same CLI
command as O. That independent I slot uses only `IMPL_ARGS`. I starts fresh and resumes throughout the per-task implementation
loop. The complete quality gate runs deterministically and calls O only when a
repair is needed. The branch review likewise calls O only when a blocker needs
repair. The first such O call changes the active worker ref, discards I's ID,
and starts O fresh; later O repairs in the same stage resume that new O session.
The old O session from an earlier stage is never restored.

If the complete gate and branch review both pass without repairs, there is no O
worker call in this stage. The active I session is simply cleared when the next
stage begins.

## Phased ATDD matrix

The spec, `commit-spec`, and plan stages follow the default matrix. Phased ATDD
then creates two separate stages for every phase:

| Stage | Worker lifecycle | Reviewer lifecycle |
| --- | --- | --- |
| `phase-N-write-tests` | R starts fresh as test author. Review repairs, red-check repairs, and the test commit resume R within this stage. | Every O acceptance-test review is fresh. |
| `phase-N-implement` | I, or O when there is no separate I ref, starts fresh for this phase. All phase tasks, gate repairs, and commits resume that worker. | With `PHASE_REVIEW=1`, every R phase review is fresh. |

The boundary between the two stages clears R's test-writing session before
implementation begins. The next phase has two new stage boundaries, so neither
the previous phase's test-writing session nor its implementation session is
reused:

```text
phase-01-write-tests: R fresh -> R resume as needed
phase-01-implement:   I fresh -> I resume as needed
phase-02-write-tests: R fresh -> R resume as needed
phase-02-implement:   I fresh -> I resume as needed
```

After all phases, the normal `write-code` stage still runs the complete quality
gate and branch review. There is no per-task implementation loop in this stage
for phased mode. The stage boundary has already cleared the last phase session,
so the first O repair, if one is needed, starts fresh. The subsequent
`final-review-and-fixes` stage starts fresh again.

## Dual Spec matrix

Dual Spec replaces `write-spec` with these stages:

| Stage | Lifecycle |
| --- | --- |
| `write-spec-a` | A starts a fresh worker session. |
| `write-spec-b` | B starts a fresh worker session. |
| `review-spec-a` | B makes one fresh reviewer call. |
| `review-spec-b` | A makes one fresh reviewer call. |
| `compare-specs-a` | A starts a fresh worker session. |
| `compare-specs-b` | B starts a fresh worker session. |
| `select-spec` | No agent call; a human selects the owner. |
| `finalize-spec` | For a merge decision, O starts fresh to merge the candidates. R reviews fresh on every round, and O resumes for blocker repairs. For a non-merge decision, the initial O call is absent; R still reviews fresh, and the first blocker repair starts O fresh. |

The following `commit-spec` stage starts O fresh again. If the human selected B,
then O=B and R=A for every later stage; the lifecycle rules do not otherwise
change.

## Imported artifacts

Importing a spec or plan removes only its initial authoring call:

- With `IMPORT_REVIEW=1`, the reviewer still starts fresh. If it finds a
  blocker, the artifact owner's first repair call starts fresh and later repairs
  in that stage resume it.
- With `IMPORT_REVIEW=0`, the import stage has no agent calls. `commit-spec` or
  the plan commit still occurs in its own stage, so its worker starts fresh.

## Workflow resume and retries

`RESUME_RUN` restores durable workflow state such as completed stages, phase and
task queues, snapshots, and base commits. Each CLI launch nevertheless creates
a new empty `AgentSession`. Completed stages are skipped, while the first worker
call in an incomplete stage starts fresh:

```text
first process:  write-code task 1 uses I session abc -> process stops
RESUME_RUN:     task 1 stays complete; task 2 starts in a fresh I session
```

This is intentionally different from retrying a worker call inside the same
workflow process. A same-process retry can resume an exact ID that the adapter
already captured. Reviewer attempts remain fresh.

## Implementation references

- [`agents.py`](../src/adversarial_ai_coding/agents.py) defines `AgentSession`,
  worker ownership changes, built-in resume arguments, and fresh reviewer calls.
- [`workflow.py`](../src/adversarial_ai_coding/workflow.py) resets the active
  worker session in `begin_stage` and defines the default stage graph.
- [`phaseflow.py`](../src/adversarial_ai_coding/phaseflow.py) defines the two
  stages created for every Phased ATDD phase.
- [`dual_spec.py`](../src/adversarial_ai_coding/dual_spec.py) defines the Dual
  Spec stage graph and owner selection.
- [`cli.py`](../src/adversarial_ai_coding/cli.py) creates a new in-memory
  `AgentSession` for every workflow process.
