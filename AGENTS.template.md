<!-- auto-workflow:begin -->
# auto-workflow Cross-Review Rules

This project is developed with auto-workflow: one AI works, another AI reviews.
All AI participants must follow these rules.

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

## Test integrity

- Files listed in `.workflow/protected-tests.txt` are acceptance tests.
  Never edit, delete, or skip them during implementation.
- If you believe a test is wrong, record your objection in the
  "Assumptions and Open Questions" section of the spec. Do not touch the test.
  The script verifies this with git diff and will force a revert.

## Spec and plan

- spec.md must contain: feature description, testable acceptance criteria,
  edge cases, out-of-scope items, and an "Assumptions and Open Questions"
  section that honestly lists every assumption made without asking a human.
- The task list in plan.md uses `- [ ]` checkboxes. Each item must be small
  enough to implement and verify on its own, and maps to exactly one commit.
  Mark finished items as `- [x]`.
- Prefer ASCII in specs, plans, and test data. Write non-ASCII characters as
  Unicode escape sequences in source code (in Go: a backslash followed by
  `u4e0a` denotes U+4E0A). Some AI tools misdecode non-ASCII file content on
  Windows and will report phantom corruption.

## Commits

- Use Conventional Commits (feat: / fix: / chore(scope): ...) in simple English.
- The body must describe in detail what was done.
- Do not add a Co-Authored-By trailer.
<!-- auto-workflow:end -->
