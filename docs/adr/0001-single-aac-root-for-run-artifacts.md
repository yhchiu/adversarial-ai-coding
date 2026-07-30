# Keep all run artifacts under a single `aac/` root

Date: 2026-07-30
Status: Accepted; implementation pending

## Context

The workflow used to write into two top-level directories of the target
repository: `specs/<RUN_ID>/` for the committed spec and plan, and
`.workflow/` for the git-ignored live state, resume state, and run archive.

Both names are generic. `specs/` is the problem: it claims a name many
projects already use for their own specifications, and every run adds one
more permanently committed `<RUN_ID>/` subdirectory to it. After fifty runs
the user's `specs/` holds fifty timestamp directories mixed in with their own
work. Nothing overwrote user files (`<RUN_ID>` is a timestamp), and there was
no collision check either: `cli.py` only did `mkdir(parents=True,
exist_ok=True)`.

We therefore claim exactly one branded top-level name instead of two generic
ones. It is `aac/`, matching the `aac` launcher in `scripts/`, so a reader who
wonders what the directory is can look up the command they typed.

```text
your-project/
`-- aac/                          <- the only name this tool claims (visible)
    |-- docs/<RUN_ID>/            <- committed; no .gitignore at this level
    |   |-- spec.md
    |   |-- plan.md
    |   `-- spec-*.md/.json       DUAL_SPEC=1 candidates and deliberation
    `-- .run/                     <- hidden; carries .gitignore = "*"
        |-- review.md, verdict.json, suggestions.md, pr-body.md,
        |   protected-tests.txt, protected-base.sha,
        |   phased-suggestion.json, spec-merge-request.md,
        |   last-agent-output.txt, last-agent-cli.raw, latest-run.txt
        |-- state/<RUN_ID>/       <- resume state
        `-- archive/<RUN_ID>/     <- per-run archive
```

One root serves two opposite visibility needs because the ignored half is
nested inside the visible half. The committed half must be visible: `spec.md`
and `plan.md` are what a human reads at the `HUMAN_GATE` and
`HUMAN_GATE_PLAN` checkpoints, and most editors hide dot-directories. The
machine half is only ever read by the tool, so hiding it is correct.

`SPEC_DIR` keeps its name even though the directory is now `docs/`. `RUNS_DIR`
is removed. Existing runs from the old layout are not resumable; there is no
migration.

## Considered options

**Rejected: `docs/aac/<RUN_ID>/` for the committed half plus a separate
top-level `.aac-workflow/`.** This nests the committed half inside an existing
conventional directory rather than claiming a new top-level name, which is
appealing. It was rejected because it makes the default location depend on a
convention the tool cannot verify. Some repositories use `doc/`,
`documentation/`, or `website/`, and some deliberately have none, so the
default would either create `docs/` unconditionally in repositories that do
not want one, or need detection logic with a fallback. Detection is
particularly bad here: `SPEC_DIR` is in `IMMUTABLE_KEYS` and is recorded in
the resume snapshot, so whatever the detection concluded at startup would be
frozen for the life of the run. The nested layout above also gets the same
visibility outcome without the guesswork.

**Rejected: one root with `.gitignore` negation.** Putting a single
`.gitignore` at `aac/` containing `*`, `!docs/`, `!docs/**` also yields one
root. It was rejected because its failure mode is silent: anyone who later
adds a new kind of committed artifact and forgets the matching negation gets a
file that never enters git, while the run proceeds normally and the human gate
still displays the file before it vanishes from the branch. Isolating the
ignored subtree in `.run/` with its own `*` preserves the self-contained,
negation-free mechanism the old `.workflow/.gitignore` already had.

**Rejected: renaming `SPEC_DIR` to `DOCS_DIR`.** It would match the new
directory name, but this tool reads a completely unprefixed environment
surface (`GATE_CMD`, `PHASES`, `RESUME_RUN`, `SPEC_DIR`, ...), and `DOCS_DIR`
is a name real documentation toolchains use. A `DOCS_DIR` exported for an
unrelated purpose would silently redirect where `spec.md` is written, and
because `SPEC_DIR` is immutable across resume, that wrong path would be frozen
into the snapshot. That is the same name-squatting problem this ADR is
fixing, moved from the filesystem namespace into the environment namespace.
The directory rename is kept because `docs/` is accurate — `plan.md` and
`spec-decision.md` are not specs — but the variable keeps the rarer name.
Prefixing the whole environment surface with `AAC_` remains open.

**Removed: `RUNS_DIR`.** It was documented as configuring the archive
location but was never wired: `config.py` read it into `Settings.runs_dir`
while the only archive construction site passed a hardcoded `wf / "runs"`, and
nothing read `settings.runs_dir`. Rather than implement the knob, it is
removed. `aac/.run/` is an implementation detail and is deliberately not
configurable; a user who wants the tree elsewhere can move `aac/` itself. The
archive directory is also renamed from `runs/` to `archive/`, because
`.run/runs/` reads badly.

## Consequences

- **Breaking.** Runs started under the old layout cannot be resumed. This was
  accepted deliberately rather than carrying a compatibility path.
- Every user's repository gains a visible, committed `aac/` directory that
  teammates who do not use the tool will also see. This is the price of
  claiming an unambiguous name instead of leaving no trace.
- Resume needs no logic change. `.workflow` appears in only three functional
  places, the state directory's internal layout is unchanged, `RunState.resume`
  receives its root as an argument, and `RESUME_RUN=last` is resolved by
  scanning that root rather than by reading `latest-run.txt`.
- Agents are unaffected by stale documentation. The reviewer learns where to
  write `review.md`, `verdict.json`, and `phased-suggestion.json` from the
  `{{WF}}` placeholder substituted at render time, not from `AGENTS.md`. A
  user's existing `AGENTS.md` still names `.workflow/` and is never rewritten
  by the tool — only reported as out of date — so it degrades the reviewer's
  background knowledge without misdirecting any write.
- The correctness of the whole layout rests on one invariant that nothing
  previously tested: `aac/docs/**` is reachable by git and `aac/.run/**` is
  not. A test now pins it.
