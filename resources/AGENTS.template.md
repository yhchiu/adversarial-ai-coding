<!-- adversarial-ai-coding:begin -->
# adversarial-ai-coding Cross-Review Rules

This project is developed with adversarial-ai-coding: one AI works, another AI reviews
and writes adversarial acceptance tests.
All AI participants must follow these rules.

## Tool discipline

- Prefer your built-in file read/search tools over shell commands such as
  `cat`, `ls`, or `cd`-prefixed pipelines. Shell commands are restricted by an
  allowlist; a blocked command wastes a turn and burns tokens.
- Write and edit files with your built-in file editing tools. Never create or
  modify files through shell writes such as PowerShell `Set-Content` or output
  redirection: on Windows, sandboxed shell writes can produce files that other
  participants and the workflow cannot read.

## Review and replies (.workflow/review.md)

- Reviewer: list findings one by one. For each finding, name the file and describe
  the problem. When starting a new review round, overwrite old content, but keep
  items the worker has not replied to yet.
- Worker: reply under each finding. If you agree, fix it and write "Fixed: <summary>".
  If you disagree, write "Disagree: <reason>". Never ignore a finding silently.
- Reviewer: in the next round, verify each reply first, then look for new problems.

## Verdict (.workflow/verdict.json)

- Write one line of JSON: `{"approved": bool, "blockers": [...], "suggestions": [...]}`
- A blocker must be fixed before approval: correctness bugs, spec violations,
  weakened tests, security problems.
- A suggestion does not block approval. Suggestions are collected and evaluated
  in the final stage.
- Set `"approved": true` only when there are zero blockers.

## Phased suggestion (.workflow/phased-suggestion.json)

When the review prompt asks for it, the spec reviewer also writes
.workflow/phased-suggestion.json as one line of JSON:
{"phased": true|false, "reason": "one or two sentences"}. This judgment
is separate from the verdict: never put it in verdict.json, and never
let it influence approved or blockers.

## Test integrity

- Files listed in `.workflow/protected-tests.txt` are acceptance tests.
  Never edit, delete, or skip them during implementation.
- If you believe a test is wrong, record your objection in the
  "Assumptions and Open Questions" section of the spec. Do not touch the test.
  The workflow verifies this with git diff and will force a revert.

## Spec and plan

- spec.md must contain: feature description, testable acceptance criteria,
  edge cases, out-of-scope items, and an "Assumptions and Open Questions"
  section that honestly lists every assumption made without asking a human.
- In dual spec mode, `spec-a.md` and `spec-b.md` are independent candidate
  specs. When asked to write one candidate, do not read the other candidate,
  candidate review files, or comparison files.
- In dual spec mode, comparison files must compare both candidates directly and
  call out strengths, weaknesses, missing requirements, stronger acceptance
  criteria, edge cases, assumptions, and a recommended owner.
- When `.workflow/spec-merge-request.md` exists, the selected spec owner must
  explicitly adopt the requested items into `spec.md` before implementation
  planning starts.
- The task list in plan.md uses `- [ ]` checkboxes. Each item must be small
  enough to implement and verify on its own, and maps to exactly one commit.
  Mark finished items as `- [x]`.
- Prefer ASCII in specs, plans, and test data. Write non-ASCII characters as
  Unicode escape sequences in source code (in Go: a backslash followed by
  `u4e0a` denotes U+4E0A). Some AI tools misdecode non-ASCII file content on
  Windows and will report phantom corruption.

## Phased mode (PHASES=1)

- The plan splits into `## Phase N: <title>` sections. Every phase needs an
  `Acceptance:` line with observable behavior at a stable boundary and at
  least one `- [ ]` task. Phases are vertical functional slices, never
  technical layers.
- A title ending in `(regression-guard)` marks tests that must pass
  immediately; all other phase tests must be red before implementation.
- The default test level is the phase acceptance test at a stable boundary
  (component or contract level is fine). Add lower-level implementation
  tests only when a trigger holds: many input combinations or edge cases;
  parser, state machine, algorithm, or data-transformation logic;
  concurrency, timeout, retry, or cancellation behavior; failures that
  acceptance tests cannot localize or reproduce cheaply.

## Commits

- Use Conventional Commits (feat: / fix: / chore(scope): ...) in simple English.
- The body must describe in detail what was done.
- Do not add a Co-Authored-By trailer.
<!-- adversarial-ai-coding:end -->
