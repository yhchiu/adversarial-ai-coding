# Import Format Contract

`IMPORT_SPEC` and `IMPORT_PLAN` let the workflow start from spec/plan
files written outside the tool (for example, from a brainstorming session
in your own AI CLI). The workflow copies the file into the run's spec
directory; the original is never modified. Everything below is enforced
deterministically at startup, before any AI call.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `IMPORT_SPEC=path` | Use this file as `spec.md`; skip the "worker writes the spec" step. |
| `IMPORT_PLAN=path` | Use this file as `plan.md`; skip the "worker writes the plan" step. Requires `IMPORT_SPEC`. |
| `IMPORT_REVIEW=0/1` | Default `1`: imported artifacts still go through the reviewer's review loop. `0` skips the AI review of imported artifacts only. Requires `IMPORT_SPEC`. |

Rules the workflow enforces:

- `IMPORT_PLAN` without `IMPORT_SPEC` is an error.
- `IMPORT_REVIEW` without `IMPORT_SPEC` is an error.
- `IMPORT_SPEC` with `DUAL_SPEC=1` is an error.
- Import settings are frozen into the run's resume snapshot; they cannot
  be changed when resuming.
- `IMPORT_REVIEW=0` never skips human gates, format checks, or commits.

## Spec file (`IMPORT_SPEC`)

- Markdown, UTF-8, non-empty.
- Must contain a heading line (starting with `#`) whose text includes
  both "Assumptions" and "Open Questions" (case-insensitive), for
  example `## Assumptions and Open Questions`. Headless stages cannot
  ask a human questions, so unresolved decisions must be written down.
- Recommended sections (mirroring the in-run spec prompt): feature
  description, testable acceptance criteria, edge cases, and
  out-of-scope items.

## Plan file (`IMPORT_PLAN`), basic mode

- Must contain at least one task line starting exactly with `- [ ] `.
- One task becomes one commit; keep tasks small and independently
  buildable (the per-task gate only compiles).

## Plan file (`IMPORT_PLAN`), phased mode (`PHASES=1`)

- Tasks are grouped into `## Phase N: <title>` sections.
- Each phase needs an observable `Acceptance:` line and at least one
  `- [ ] ` task.
- A trailing `(regression-guard)` on the title marks a phase whose tests
  must pass immediately instead of starting red.

## What still happens in-run

Imported artifacts are reviewed by the reviewer agent (unless
`IMPORT_REVIEW=0`), pass the human gates (`HUMAN_GATE`,
`HUMAN_GATE_PLAN`), are committed by the owner agent, and are archived
(originals as `imported-spec.md` / `imported-plan.md` in the run
archive). `pr-body.md` records what was imported and whether the AI
review ran. See `resources/import-authoring-prompt.md` for a prompt you
can paste into your own tool to produce compliant files.
