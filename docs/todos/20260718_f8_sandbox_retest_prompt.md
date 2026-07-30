# Task: Re-test and document the Codex Windows sandbox failures (F8)

## Context

Verified 2026-07-17/18 on this machine (codex-cli 0.144.5, Windows 11):
three failure modes made every headless
`codex exec --sandbox workspace-write` call unusable for the
adversarial-ai-coding workflow.

- Mode A - `[windows] sandbox = "elevated"` + workspace path trusted in
  `~/.codex/config.toml`: codex can write, but files sometimes come out
  with a deny-all ACL the launching user cannot read (icacls cannot even
  read the ACL; deletion works only through parent-directory rights).
  Nondeterministic within a single run: round 1 clean, round 2 poisoned.
  Preserved evidence: `%TEMP%\wf-e2e-h_vxb0b4` (poisoned
  `.workflow/review.md` / `verdict.json`).
- Mode B - elevated + workspace NOT trusted: the sandbox comes up
  read-only; codex reports "writing is blocked by read-only sandbox" and
  cannot write outputs at all.
- Mode C - `sandbox = "unelevated"`: codex cannot even READ the
  workspace (restricted token: Get-Content/git all return Access denied,
  and the shell workdir falls back to System32).

Full history: `docs/todos/20260717_phased_atdd_followups.md` (item F8).
Official docs: https://developers.openai.com/codex/windows and
https://github.com/openai/codex/blob/main/docs/sandbox.md

## Goals

1. Build a minimal, workflow-independent reproduction matrix and re-test
   on the CURRENT codex version (`codex --version`; note whether it moved
   past 0.144.5 - if an update is available, test the updated version,
   that is what an upstream report needs).
2. Collect evidence strong enough for an upstream report, and draft it.
3. Conclude whether this repo still needs the planned opt-in
   `danger-full-access` escape hatch for the codex slot (F8 first item).

## Hard constraints

- NEVER launch `codex` from this session's sandboxed shell - results are
  invalid (documented local lesson: Claude Code's sandbox distorts codex
  sandbox behavior). Instead, generate a self-contained driver script
  (PowerShell or .cmd) and ask the human to run it from a normal
  terminal, then analyze the artifacts it collects.
- Back up `~/.codex/config.toml` before any edit and restore it when
  done. Only two keys may be varied: `[windows] sandbox` and
  `[projects.'<path>'] trust_level`.
- Keep codex prompts tiny and single-turn; every call costs quota.

## Suggested matrix

Dimensions: sandbox {elevated, unelevated} x trust {trusted, untrusted}
x location {C:\tmp\..., %TEMP%\...}. Fresh directory per case.

Per case, the driver should:

1. `git init` a repo in the case directory, commit one file.
2. Run: `codex exec --sandbox workspace-write -m gpt-5.5 -c model_reasoning_effort=low "Create a file out.txt containing the word OK in the current directory, using your file editing tool."`
3. Record: codex exit code and stdout; then, as the normal user:
   `type out.txt` (readable?), `icacls out.txt` (ACL readable? owner?),
   and whether deletion works.
4. Append one summary line per case to a results file.

Repeat the elevated+trusted case at least 3 times - Mode A is
nondeterministic per session.

## Deliverables

1. `C:\tmp\codex-sandbox-retest-<date>\` with per-case artifacts and a
   summary table (case, exit code, wrote?, readable?, ACL state).
2. A draft upstream report (ASCII English) covering modes A/B/C with
   repro steps, version/config info, and icacls evidence, ready to post
   to github.com/openai/codex issues (reference Discussion #6065 "Help
   test experimental Windows sandbox").
3. An updated recommendation for F8 in
   `docs/todos/20260717_phased_atdd_followups.md` (do not commit it; the
   file is deliberately untracked).
