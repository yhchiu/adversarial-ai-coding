# Authoring Prompt for External Tools

Paste the prompt below into your own AI tool (Claude Code, Codex, a chat
session) after you have finished clarifying requirements. It produces
files that pass the adversarial-ai-coding import checks in
docs/import-format.md.

---

Write two files from our discussion above.

1. `spec.md`: a specification with these sections:
   - Feature description.
   - Testable acceptance criteria.
   - Edge cases.
   - Out of scope.
   - `## Assumptions and Open Questions`: list every assumption we did
     not settle explicitly. This exact topic must appear as a Markdown
     heading; automation rejects the file without it. If everything is
     settled, write `- none`.

2. `plan.md`: an implementation plan as a Markdown checkbox list. Every
   task line must start exactly with `- [ ] `. One task becomes one git
   commit, so keep tasks small and independently buildable.

   If I tell you the run uses phased mode (PHASES=1), group the tasks
   into `## Phase N: <title>` sections instead; give every phase an
   observable `Acceptance:` line and at least one `- [ ] ` task, and
   mark pure regression phases with `(regression-guard)` at the end of
   the title.

Do not include anything else in the two files.
