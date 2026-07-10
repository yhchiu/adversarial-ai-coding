# Python Port — Plan 5 of 6: Workflow, Dual-Spec, and CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the stage orchestration (begin/end stage, commit helpers, human gates, finish), the DUAL_SPEC mode, the AGENTS.md bootstrap, and the CLI entry point — after this plan the Python tool runs complete workflows end to end.

**Architecture:** Plan 5 of the series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. Extends
`workflow.py` (from plan 4) with the stage flow, adds `dual_spec.py` and
`cli.py`, and finishes with the port of `tests/resume.test.sh` as an
offline integration suite driven by cross-platform Python fake agents.
Import-cycle rule: `workflow.py` never imports `review`/`dual_spec` at
module level — the stage functions import them lazily inside the function
body (review imports workflow at module level; the lazy direction breaks
the cycle and is commented at each site).

**Tech Stack:** Python 3.12+, stdlib only, pytest.

## Global Constraints

- Runtime dependencies: none (stdlib only); pytest dev-only.
- Bash files are FROZEN; cited lines are the behavior reference; keep the
  user-facing wording of prompts, banners, hints, and errors.
- Human-gate reads go through `ctx.ask` (injectable). The default asks on
  stdin only when it is a TTY; otherwise it aborts with the bash "No
  interactive terminal" wording. Divergence from bash (which read
  /dev/tty directly): a piped-stdin interactive run is not supported —
  document it in code.
- `jq` is no longer a runtime requirement (bash `need jq`); the port's
  startup checks only git. Document as divergence.
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By.
- `uv run pytest -q` green after every task.
- Machine note: clear `PYTHONHOME`/`PYTHONPATH` if `uv run` misbehaves.

## File Structure

```
src/adversarial_ai_coding/workflow.py   # Tasks 1, 3, 4 (extend)
src/adversarial_ai_coding/dual_spec.py  # Task 2
src/adversarial_ai_coding/prompts.py    # Task 3 (agents template additions)
src/adversarial_ai_coding/cli.py        # Task 5
pyproject.toml                          # Task 5 ([project.scripts])
tests/test_stageflow.py                 # Task 1
tests/test_dual_spec.py                 # Task 2
tests/test_agents_bootstrap.py          # Task 3
tests/test_finish_pipeline.py           # Task 4
tests/test_cli.py                       # Task 5
tests/test_resume_integration.py        # Task 6
tests/fake_agent.py                     # Task 6 (cross-platform fake engine)
```

## Bash-Function Mapping (this plan's parity ledger)

| bash | Python |
|---|---|
| `begin_stage` :1390 / `end_stage` :1413 / `stage_done` :1386 | `workflow.begin_stage` / `end_stage` (ledger primitives from plan 3) |
| `commit_work` :1441 / `commit_if_dirty` :1458 | `workflow.commit_work` / `commit_if_dirty` |
| `human_gate_spec` :1465 | `workflow.human_gate_spec` |
| `SPEC_OWNER_SLOT...` :326-330 / `set_spec_roles_from_slot` :887 | `workflow.SpecRoles` + `workflow.set_spec_roles_from_slot` |
| `finish` :1701 | `workflow.finish` |
| `main` stage pipeline :1896-2003 | `workflow.run_workflow` |
| `normalize_dual_spec_decision` :851 ... `run_dual_spec_spec_stage` :1622 | same names in `dual_spec` |
| `dual_spec_preflight` :993 | `dual_spec.dual_spec_preflight` |
| `write_agents_section` :1014 / `bootstrap_agents_md` :1022 / `AGENTS_MARKER` :1008 | `prompts.write_agents_section` / `bootstrap_agents_md` / `AGENTS_MARKER` |
| `usage` :332 / `main` startup :1813-1894 | `cli.main` |
| `print_resume_hint` :91 / `on_workflow_exit` :106 / `install_run_traps` :116 | `cli` abort handling (`_print_resume_hint`, exception mapping, finally-release) |
| `abs_path` :585 | `Path.resolve()` in cli |
| `tests/resume.test.sh` scenarios 1-4, 6 | `tests/test_resume_integration.py` |
| `tests/resume.test.sh` scenario 5 (real SIGINT) | plan 6 manual acceptance (in-process KeyboardInterrupt covered in test_cli) |
| `tests/resume.test.sh` scenario 7 (pty dual-spec) | covered by test_dual_spec unit ports (bash did the same on Windows) |

---

### Task 1: Stage flow — `begin_stage`/`end_stage`, commit helpers, human gate, spec roles

Bash reference: `adversarial-ai-coding.sh:326-330, 887-892, 1386-1418,
1441-1461, 1465-1483`.
Bash tests ported: `tests/helpers.test.sh:792-844` (ledger skip flow),
`:540-549` (human gate).

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py`
- Test: `tests/test_stageflow.py`

**Interfaces:**
- New `WorkflowContext` fields (append to the plan-4 dataclass, binding):
  ```python
  spec_roles: SpecRoles = field(default_factory=lambda: SpecRoles())
  dual_spec_decision: str = ""
  ask: Callable[[str], str] = _default_ask
  run_id: str = ""            # bash RUN_ID; set by cli
  ```
- `@dataclass workflow.SpecRoles: owner_slot: str = "A"; reviewer_slot: str = "B"; owner_engine: str = ""; reviewer_engine: str = ""`
- `workflow.set_spec_roles_from_slot(ctx, slot: str) -> None` — mirrors
  bash :887-892 using `dual_spec.engine_for_slot`.
- `workflow.begin_stage(ctx, name: str, *artifacts: Path) -> bool` — False
  when the ledger records the stage (after verifying every artifact exists,
  else `WorkflowAbort` pointing at the run archive); True resets
  `cur_stage`/`cur_round`/worker session and logs the banner.
- `workflow.end_stage(ctx) -> None` — no-op without claimed state;
  otherwise `state.record_stage(cur_stage, head_sha(workspace))`.
- `workflow.commit_work(ctx, engine, description) -> None` — render
  `commit-approved-work`, `work`, then `gitops.ensure_committed`.
- `workflow.commit_if_dirty(ctx, engine, description) -> None`.
- `workflow.human_gate_spec(ctx) -> None` — HUMAN_GATE off returns;
  notify + prompt via `ctx.ask`; a non-y answer raises `WorkflowAbort`
  ("Aborted: spec was not approved.").
- `workflow._default_ask(prompt: str) -> str` — TTY-guarded `input`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stageflow.py`:

```python
"""Ports helpers.test.sh:792-844 (stage skip flow) and 540-549 (human gate)."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gitops import head_sha
from adversarial_ai_coding.runstate import RunState
from adversarial_ai_coding.workflow import (
    begin_stage,
    commit_if_dirty,
    commit_work,
    end_stage,
    human_gate_spec,
    set_spec_roles_from_slot,
)


def with_state(ctx, new_repo):
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    return ctx


def test_begin_end_records_stage_and_checkpoint(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    assert begin_stage(ctx, "stage-one") is True
    assert ctx.cur_stage == "stage-one"
    assert ctx.cur_round == 1
    end_stage(ctx)
    assert ctx.state.stage_done("stage-one")
    assert not ctx.state.stage_done("stage-two")
    assert ctx.state.read_last_head() == head_sha(new_repo)


def test_begin_stage_skips_completed_and_logs(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    logged = []
    ctx.echo = logged.append
    begin_stage(ctx, "stage-one")
    end_stage(ctx)
    assert begin_stage(ctx, "stage-one") is False
    assert any("== skip [stage-one] (already completed in run" in l for l in logged)


def test_begin_stage_skip_verifies_artifacts(make_ctx, new_repo):
    ctx = with_state(make_ctx(), new_repo)
    artifact = new_repo / "artifact.md"
    artifact.touch()
    begin_stage(ctx, "stage-one")
    end_stage(ctx)
    assert begin_stage(ctx, "stage-one", artifact) is False   # artifact exists
    with pytest.raises(WorkflowAbort, match="run archive"):
        begin_stage(ctx, "stage-one", new_repo / "missing-artifact.md")


def test_begin_stage_resets_worker_session(make_ctx):
    ctx = make_ctx()
    ctx.session.worker_session = "old-session"
    begin_stage(ctx, "next-stage")
    assert ctx.session.worker_session == ""


def test_begin_end_without_claimed_state(make_ctx):
    # helpers.test.sh: "ledger:without claimed run state begin/end behave as before"
    ctx = make_ctx()
    assert ctx.state is None
    assert begin_stage(ctx, "some-stage") is True
    end_stage(ctx)  # must not raise or write anything
    assert ctx.cur_stage == "some-stage"


def test_commit_work_ensures_commit(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-code"
    (new_repo / "dirty.txt").write_text("x\n", encoding="utf-8")
    prompts = []
    monkeypatch.setattr(wf_mod, "work", lambda c, e, p: prompts.append(p))
    commit_work(ctx, "claude", "Task done")
    # Worker was asked to commit; since the stub did not, the fallback commit ran.
    assert prompts and "Task done" in prompts[0]
    from adversarial_ai_coding.gitops import status_porcelain
    assert status_porcelain(new_repo) == ""


def test_commit_if_dirty_skips_clean_tree(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(wf_mod, "work",
                        lambda c, e, p: pytest.fail("clean tree: no AI call"))
    commit_if_dirty(ctx, "claude", "nothing")


def test_human_gate_disabled_passes(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "0", "RETRY_ON_LIMIT": "0"})
    human_gate_spec(ctx)  # returns silently


def test_human_gate_approval_and_abort(make_ctx):
    ctx = make_ctx({"HUMAN_GATE": "1", "RETRY_ON_LIMIT": "0"})
    ctx.ask = lambda prompt: "y"
    human_gate_spec(ctx)
    ctx.ask = lambda prompt: "n"
    with pytest.raises(WorkflowAbort, match="spec was not approved"):
        human_gate_spec(ctx)


def test_set_spec_roles_from_slot(make_ctx):
    ctx = make_ctx({"AGENT_A": "claude", "AGENT_B": "codex", "RETRY_ON_LIMIT": "0"})
    set_spec_roles_from_slot(ctx, "B")
    assert ctx.spec_roles.owner_engine == "codex"
    assert ctx.spec_roles.reviewer_engine == "claude"
    assert ctx.spec_roles.owner_slot == "B"
    assert ctx.spec_roles.reviewer_slot == "A"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stageflow.py -q`
Expected: FAIL — ImportError on the new names.

- [ ] **Step 3: Extend `src/adversarial_ai_coding/workflow.py`**

```python
import sys


def _default_ask(prompt: str) -> str:
    # Divergence: bash read /dev/tty; the port supports interactive stdin only.
    if not sys.stdin.isatty():
        raise WorkflowAbort(
            "!! No interactive terminal is available for approval. Run from an "
            "interactive terminal, or set HUMAN_GATE=0 to skip this gate (not "
            "recommended)."
        )
    return input(prompt)


@dataclass
class SpecRoles:
    owner_slot: str = "A"
    reviewer_slot: str = "B"
    owner_engine: str = ""
    reviewer_engine: str = ""


# WorkflowContext gains (add to the dataclass from plan 4):
#   spec_roles: SpecRoles = field(default_factory=SpecRoles)
#   dual_spec_decision: str = ""
#   ask: Callable[[str], str] = _default_ask
#   run_id: str = ""
# and __post_init__ fills empty spec_roles engines from settings:
#   if not self.spec_roles.owner_engine:
#       self.spec_roles.owner_engine = self.settings.engine_a
#       self.spec_roles.reviewer_engine = self.settings.engine_b


def set_spec_roles_from_slot(ctx: WorkflowContext, slot: str) -> None:
    from .dual_spec import engine_for_slot, reviewer_slot_for_owner_slot

    reviewer = reviewer_slot_for_owner_slot(slot)
    ctx.spec_roles = SpecRoles(
        owner_slot=slot,
        reviewer_slot=reviewer,
        owner_engine=engine_for_slot(ctx, slot),
        reviewer_engine=engine_for_slot(ctx, reviewer),
    )


def begin_stage(ctx: WorkflowContext, name: str, *artifacts: Path) -> bool:
    if ctx.state is not None and ctx.state.stage_done(name):
        for artifact in artifacts:
            if not artifact.exists():
                raise WorkflowAbort(
                    f"!! Stage {name} is recorded complete but its artifact "
                    f"{artifact} is missing.\n   Restore it from the run archive "
                    f"under {ctx.archive.run_dir.parent}, or delete "
                    f"{ctx.state.state_dir} to start over."
                )
        ctx.log(f"== skip [{name}] (already completed in run {ctx.run_id})")
        return False
    ctx.cur_stage = name
    # Worker session resumes within a stage and resets across stages (sh:1407).
    ctx.session.worker_session = ""
    ctx.cur_round = 1
    ctx.archive.log_section("stage begin", "workflow", "workflow",
                            ctx.cur_stage, ctx.cur_round, echo=ctx.echo)
    ctx.log(f"\n================ [{name}] ================")
    return True


def end_stage(ctx: WorkflowContext) -> None:
    if ctx.state is None:
        return
    from .gitops import head_sha
    ctx.state.record_stage(ctx.cur_stage, head_sha(ctx.workspace))


def commit_work(ctx: WorkflowContext, engine: str, description: str) -> None:
    from .gitops import ensure_committed

    ctx.archive.log_section("commit", "worker", engine, ctx.cur_stage,
                            ctx.cur_round, echo=ctx.echo)
    prompt = render_prompt(ctx.prompts_dir, "commit-approved-work",
                           {"DESCRIPTION": description})
    work(ctx, engine, prompt)
    ensure_committed(ctx.workspace, ctx.cur_stage, ctx.echo_err)


def commit_if_dirty(ctx: WorkflowContext, engine: str, description: str) -> None:
    from .gitops import status_porcelain

    if not status_porcelain(ctx.workspace):
        return  # skip if clean to avoid a wasted AI call (sh:1458-1461)
    commit_work(ctx, engine, description)


def human_gate_spec(ctx: WorkflowContext) -> None:
    # A bad spec amplifies into many bad changes, so approval happens before
    # costly implementation (sh:1463-1483).
    if not ctx.settings.human_gate:
        return
    ctx.archive.log_section("human gate", "workflow", "workflow",
                            ctx.cur_stage, ctx.cur_round, echo=ctx.echo)
    ctx.notify(f"adversarial-ai-coding: spec awaits human approval "
               f"({ctx.spec_dir / 'spec.md'})")
    ctx.echo("")
    ctx.echo(f"### Human checkpoint: review {ctx.spec_dir / 'spec.md'}, "
             "especially the Assumptions and Open Questions section.")
    ctx.echo("### You may edit the file before continuing; your edits will be "
             "committed with the spec.")
    answer = ctx.ask("Enter y to approve and continue; anything else aborts:")
    if answer not in ("y", "Y"):
        raise WorkflowAbort("Aborted: spec was not approved.")
    ctx.log("Spec approved by human")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stageflow.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py tests/test_stageflow.py
git commit -m "feat: port stage flow, commit helpers, and human gate

begin_stage skips ledger-recorded stages after verifying their
artifacts still exist (failing closed toward the run archive), resets
the worker session and round at stage boundaries, and end_stage records
the stage with a HEAD checkpoint. commit_work renders the commit prompt
and backs it with the fallback commit; commit_if_dirty avoids wasted AI
calls on clean trees. The human spec gate reads through an injectable
ask callback whose default only accepts interactive stdin."
```

---

### Task 2: `dual_spec.py`

Bash reference: `adversarial-ai-coding.sh:851-1003` (helpers, preflight),
`:1485-1697` (candidate review, comparison index, decision file, human
selection, restore, stage runner).
Bash tests ported: `tests/helpers.test.sh:376-493` and `:900-940`
(decision restore).

**Files:**
- Create: `src/adversarial_ai_coding/dual_spec.py`
- Test: `tests/test_dual_spec.py`

**Interfaces:**
- Consumes: WorkflowContext, workflow.begin_stage/end_stage/work/
  set_spec_roles_from_slot/human_gate_spec, review.run_review/review_loop.
- Produces (module functions; ctx is always the first parameter where used):
  - `normalize_dual_spec_decision(raw: str) -> str | None` — a/b/ma/mb
    (case-insensitive) to adopt-a/adopt-b/merge-a/merge-b, None otherwise.
  - `dual_spec_owner_slot(decision: str) -> str | None`
  - `engine_for_slot(ctx, slot: str) -> str`
  - `reviewer_slot_for_owner_slot(slot: str) -> str`
  - `candidate_spec_for_slot(ctx, slot: str) -> Path`
  - `dual_spec_final_review_scope(ctx, decision: str) -> str`
  - `write_spec_merge_request_template(ctx, base_slot: str, other_slot: str) -> None`
  - `merge_request_has_content(ctx) -> bool`
  - `apply_dual_spec_decision(ctx, decision: str, task: str) -> None`
  - `dual_spec_preflight(settings: Settings, stdin_isatty: bool) -> None` —
    raises `WorkflowAbort` when DUAL_SPEC without HUMAN_GATE, or no
    interactive terminal. Takes `Settings` (not ctx) because cli runs it
    before the context exists.
  - `run_candidate_spec_review(ctx, reviewer: str, scope: str, review_out: Path, verdict_out: Path) -> None`
  - `write_spec_comparison_index(ctx) -> None`
  - `write_dual_spec_decision_file(ctx, decision: str) -> None`
  - `human_gate_dual_spec_decision(ctx) -> None` — loops `ctx.ask` until a
    valid decision; merge decisions require the edited merge request.
  - `restore_dual_spec_decision(ctx) -> None` — C5 restore from
    `spec-decision.md`.
  - `run_dual_spec_spec_stage(ctx, task: str) -> None` — the eight-stage
    dual-spec pipeline (write a/b, cross reviews, comparisons, index,
    select, finalize).

- [ ] **Step 1: Write the failing tests**

`tests/test_dual_spec.py`:

```python
"""Ports helpers.test.sh:376-493 and 900-940 (dual-spec helpers and restore)."""

import json

import pytest

from adversarial_ai_coding import dual_spec as ds
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.runstate import RunStateError


def dual_ctx(make_ctx, **extra_env):
    env = {"AGENT_A": "claude", "AGENT_B": "codex", "DUAL_SPEC": "1",
           "RETRY_ON_LIMIT": "0"}
    env.update(extra_env)
    ctx = make_ctx(env)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    return ctx


def test_normalize_decision():
    assert ds.normalize_dual_spec_decision("A") == "adopt-a"
    assert ds.normalize_dual_spec_decision("mb") == "merge-b"
    assert ds.normalize_dual_spec_decision("nope") is None
    assert ds.normalize_dual_spec_decision("") is None


def test_owner_slot_and_roles(make_ctx):
    ctx = dual_ctx(make_ctx)
    assert ds.dual_spec_owner_slot("adopt-a") == "A"
    assert ds.dual_spec_owner_slot("merge-b") == "B"
    assert ds.dual_spec_owner_slot("bogus") is None
    assert ds.engine_for_slot(ctx, "B") == "codex"
    assert ds.reviewer_slot_for_owner_slot("A") == "B"


def test_preflight_requires_human_gate_and_tty(make_ctx):
    ctx = dual_ctx(make_ctx, HUMAN_GATE="0")
    with pytest.raises(WorkflowAbort, match="requires HUMAN_GATE=1"):
        ds.dual_spec_preflight(ctx.settings, stdin_isatty=True)
    ctx2 = dual_ctx(make_ctx, HUMAN_GATE="1")
    with pytest.raises(WorkflowAbort, match="interactive terminal"):
        ds.dual_spec_preflight(ctx2.settings, stdin_isatty=False)
    ds.dual_spec_preflight(ctx2.settings, stdin_isatty=True)  # ok
    ctx3 = dual_ctx(make_ctx, DUAL_SPEC="0", HUMAN_GATE="0")
    ds.dual_spec_preflight(ctx3.settings, stdin_isatty=False)  # disabled: no checks


def test_merge_request_template_is_not_content(make_ctx):
    ctx = dual_ctx(make_ctx)
    ds.write_spec_merge_request_template(ctx, "A", "B")
    assert ds.merge_request_has_content(ctx) is False


def test_merge_request_accepts_real_instructions(make_ctx):
    ctx = dual_ctx(make_ctx)
    (ctx.wf / "spec-merge-request.md").write_text(
        "# Dual Spec Merge Request\n\n## Items to adopt from B\n\n"
        "- adopt from Candidate B the stricter timeout acceptance criterion.\n"
        "- edge cases, especially empty task files, must be covered.\n",
        encoding="utf-8")
    assert ds.merge_request_has_content(ctx) is True
    # Paragraph form without bullets also counts (helpers.test.sh:425-434).
    (ctx.wf / "spec-merge-request.md").write_text(
        "# Dual Spec Merge Request\n\n## Items to adopt from B\n\n"
        "adopt from Candidate B the stricter timeout acceptance criterion.\n"
        "edge cases, especially empty task files, must be covered.\n",
        encoding="utf-8")
    assert ds.merge_request_has_content(ctx) is True
    assert ds.merge_request_has_content(dual_ctx(make_ctx)) is False  # missing file


def test_final_review_scope_merge_checks_adoption(make_ctx):
    ctx = dual_ctx(make_ctx)
    scope = ds.dual_spec_final_review_scope(ctx, "merge-b")
    assert "spec-merge-request.md" in scope
    assert "block approval" in scope
    plain = ds.dual_spec_final_review_scope(ctx, "adopt-a")
    assert "spec-merge-request.md" not in plain


def test_apply_adopt_reviews_and_gates(make_ctx, monkeypatch):
    # helpers.test.sh: "dual_spec:direct adopt reviews final spec and asks human"
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-a.md").write_text("candidate A\n", encoding="utf-8")
    (ctx.spec_dir / "spec-b.md").write_text("candidate B\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ds, "review_loop",
                        lambda c, r, w, s: calls.append(("review", r, w)))
    monkeypatch.setattr(ds, "human_gate_spec", lambda c: calls.append(("human",)))
    monkeypatch.setattr(ds, "work",
                        lambda c, e, p: pytest.fail("adopt path must not call work"))
    ds.apply_dual_spec_decision(ctx, "adopt-a", "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "candidate A\n"
    assert calls == [("review", "codex", "claude"), ("human",)]


def test_apply_merge_calls_owner_then_reviews(make_ctx, monkeypatch):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-a.md").write_text("candidate A\n", encoding="utf-8")
    (ctx.spec_dir / "spec-b.md").write_text("candidate B\n", encoding="utf-8")
    (ctx.wf / "spec-merge-request.md").write_text("adopt item\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(ds, "review_loop",
                        lambda c, r, w, s: calls.append(("review", r, w, s)))
    monkeypatch.setattr(ds, "human_gate_spec", lambda c: calls.append(("human",)))

    def fake_work(c, engine, prompt):
        calls.append(("work", engine))
        (ctx.spec_dir / "spec.md").write_text("merged B\n", encoding="utf-8")

    monkeypatch.setattr(ds, "work", fake_work)
    ds.apply_dual_spec_decision(ctx, "merge-b", "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "merged B\n"
    assert calls[0] == ("work", "codex")             # selected owner merges
    assert calls[1][0:3] == ("review", "claude", "codex")
    assert "block approval" in calls[1][3]
    assert calls[2] == ("human",)


def test_restore_decision_adopt_b(make_ctx):
    # helpers.test.sh: "dual_spec restore:adopt-b restores owner engine B"
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-decision.md").write_text(
        "# Dual Spec Decision\n\n- decision: adopt-b\n- selected owner slot: B\n",
        encoding="utf-8")
    ds.restore_dual_spec_decision(ctx)
    assert ctx.dual_spec_decision == "adopt-b"
    assert ctx.spec_roles.owner_engine == "codex"
    assert ctx.spec_roles.reviewer_engine == "claude"


def test_restore_merge_requires_merge_request(make_ctx):
    ctx = dual_ctx(make_ctx)
    (ctx.spec_dir / "spec-decision.md").write_text("- decision: merge-b\n",
                                                   encoding="utf-8")
    with pytest.raises(WorkflowAbort, match="run archive"):
        ds.restore_dual_spec_decision(ctx)
    (ctx.wf / "spec-merge-request.md").write_text("adopt item\n", encoding="utf-8")
    ds.restore_dual_spec_decision(ctx)
    assert ctx.dual_spec_decision == "merge-b"
    assert ctx.spec_roles.owner_engine == "codex"


def test_restore_invalid_or_missing_decision(make_ctx):
    ctx = dual_ctx(make_ctx)
    with pytest.raises(WorkflowAbort, match="no decision yet"):
        ds.restore_dual_spec_decision(ctx)
    (ctx.spec_dir / "spec-decision.md").write_text("- decision: bogus\n",
                                                   encoding="utf-8")
    with pytest.raises(WorkflowAbort, match="Invalid decision"):
        ds.restore_dual_spec_decision(ctx)


def test_restore_existing_decision_left_alone(make_ctx):
    ctx = dual_ctx(make_ctx)
    ctx.dual_spec_decision = "adopt-a"
    ctx.spec_roles.owner_engine = "claude"
    ds.restore_dual_spec_decision(ctx)  # no spec-decision.md needed
    assert ctx.dual_spec_decision == "adopt-a"
    assert ctx.spec_roles.owner_engine == "claude"


def test_run_dual_spec_stage_uses_decision_variable(make_ctx, monkeypatch):
    # helpers.test.sh: "dual_spec:runner uses decision variable, not log output"
    ctx = dual_ctx(make_ctx)
    calls = []

    def fake_work(c, engine, prompt):
        for name in ("spec-a.md", "spec-b.md",
                     "spec-comparison-a.md", "spec-comparison-b.md"):
            if name in prompt:
                (ctx.spec_dir / name).write_text(f"made {name}\n", encoding="utf-8")
                return
    monkeypatch.setattr(ds, "work", fake_work)
    monkeypatch.setattr(ds, "run_candidate_spec_review",
                        lambda c, r, s, ro, vo: (ro.write_text("review\n", encoding="utf-8"),
                                                 vo.write_text("{}", encoding="utf-8")))
    def fake_selection(c):
        print("log noise")
        c.dual_spec_decision = "adopt-a"
        ds.write_dual_spec_decision_file(c, "adopt-a")
    monkeypatch.setattr(ds, "human_gate_dual_spec_decision", fake_selection)
    monkeypatch.setattr(ds, "review_loop", lambda c, r, w, s: calls.append("review"))
    monkeypatch.setattr(ds, "human_gate_spec", lambda c: calls.append("human"))
    ds.run_dual_spec_spec_stage(ctx, "task text")
    assert (ctx.spec_dir / "spec.md").read_text(encoding="utf-8") == "made spec-a.md\n"
    assert calls == ["review", "human"]
    assert (ctx.spec_dir / "spec-comparison.md").is_file()   # index always written
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_dual_spec.py -q`
Expected: FAIL — no module `dual_spec`.

- [ ] **Step 3: Write `src/adversarial_ai_coding/dual_spec.py`**

```python
"""DUAL_SPEC mode: independent candidates, cross review, human selection.

Port of adversarial-ai-coding.sh:851-1003 and 1485-1697. All eight
sub-stages go through begin_stage/end_stage so resume skips paid work; the
human decision is restored from spec-decision.md on resume (C5).
"""

from __future__ import annotations

from pathlib import Path

from .config import WorkflowAbort
from .prompts import render_prompt
from .review import review_loop, run_review
from .workflow import (
    WorkflowContext,
    begin_stage,
    end_stage,
    human_gate_spec,
    set_spec_roles_from_slot,
    work,
)

_DECISIONS = {"a": "adopt-a", "b": "adopt-b", "ma": "merge-a", "mb": "merge-b"}
_MERGE_TEMPLATE_PREFIX = (
    "Replace this paragraph with the concrete requirements, acceptance criteria,"
)


def normalize_dual_spec_decision(raw: str) -> str | None:
    return _DECISIONS.get((raw or "").lower())


def dual_spec_owner_slot(decision: str) -> str | None:
    return {"adopt-a": "A", "merge-a": "A",
            "adopt-b": "B", "merge-b": "B"}.get(decision)


def engine_for_slot(ctx: WorkflowContext, slot: str) -> str:
    return ctx.settings.engine_a if slot == "A" else ctx.settings.engine_b


def reviewer_slot_for_owner_slot(slot: str) -> str:
    return "B" if slot == "A" else "A"


def candidate_spec_for_slot(ctx: WorkflowContext, slot: str) -> Path:
    return ctx.spec_dir / f"spec-{slot.lower()}.md"


def dual_spec_preflight(settings, stdin_isatty: bool) -> None:
    # Settings-based (not ctx): cli runs this before the context exists.
    if not settings.dual_spec:
        return
    if not settings.human_gate:
        raise WorkflowAbort(
            "DUAL_SPEC=1 requires HUMAN_GATE=1 because a human must choose the "
            "final spec owner."
        )
    if not stdin_isatty:
        raise WorkflowAbort(
            "DUAL_SPEC=1 requires an interactive terminal for spec selection. "
            "Run interactively or set DUAL_SPEC=0."
        )


def dual_spec_final_review_scope(ctx: WorkflowContext, decision: str) -> str:
    merge_instruction = ""
    if decision.startswith("merge-"):
        merge_instruction = (
            f" Also compare {ctx.spec_dir / 'spec.md'} with "
            f"{ctx.wf / 'spec-merge-request.md'} and block approval if any "
            "requested adoption item is missing, distorted, or contradicted."
        )
    return render_prompt(ctx.prompts_dir, "review-scope-dual-final", {
        "SPEC_FILE": str(ctx.spec_dir / "spec.md"),
        "MERGE_INSTRUCTION": merge_instruction,
    })


def write_spec_merge_request_template(ctx: WorkflowContext, base_slot: str,
                                      other_slot: str) -> None:
    base_file = candidate_spec_for_slot(ctx, base_slot)
    other_file = candidate_spec_for_slot(ctx, other_slot)
    ctx.wf.mkdir(parents=True, exist_ok=True)
    (ctx.wf / "spec-merge-request.md").write_text(
        f"# Dual Spec Merge Request\n\n"
        f"- base owner: {base_slot}\n"
        f"- base spec: {base_file}\n"
        f"- adopt from owner: {other_slot}\n"
        f"- adopt from spec: {other_file}\n\n"
        f"## Items to adopt from {other_slot}\n\n"
        "Replace this paragraph with the concrete requirements, acceptance criteria,\n"
        "edge cases, non-goals, assumptions, or wording that the final spec owner must\n"
        f"adopt from {other_file}.\n",
        encoding="utf-8",
    )


def merge_request_has_content(ctx: WorkflowContext) -> bool:
    path = ctx.wf / "spec-merge-request.md"
    if not path.is_file():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[str] = []
    in_items = False
    for line in lines:
        if line.startswith("## Items to adopt "):
            in_items = True
            continue
        if in_items:
            items.append(line)
    while items and not items[0].strip():
        items.pop(0)
    while items and not items[-1].strip():
        items.pop()
    body = "\n".join(items)
    if not body:
        return False
    return not body.startswith(_MERGE_TEMPLATE_PREFIX)


def run_candidate_spec_review(ctx: WorkflowContext, reviewer: str, scope: str,
                              review_out: Path, verdict_out: Path) -> None:
    # Candidate reviews are advisory: a non-approved verdict continues to the
    # comparison instead of blocking (sh:1485-1503).
    ctx.review_path.unlink(missing_ok=True)
    ctx.verdict_path.unlink(missing_ok=True)
    old_collect = ctx.collect_review_suggestions
    ctx.collect_review_suggestions = False
    try:
        if not run_review(ctx, reviewer, scope):
            ctx.log("(candidate spec review recorded a non-approved verdict; "
                    "continuing to comparison)")
    finally:
        ctx.collect_review_suggestions = old_collect
    if not ctx.review_path.is_file():
        ctx.review_path.write_text("(reviewer did not write review.md)\n",
                                   encoding="utf-8")
    if not ctx.verdict_path.is_file():
        ctx.verdict_path.write_text(
            '{"approved": false, "blockers": ["reviewer did not write a '
            'verdict"], "suggestions": []}\n', encoding="utf-8")
    review_out.write_text(ctx.review_path.read_text(encoding="utf-8"), encoding="utf-8")
    verdict_out.write_text(ctx.verdict_path.read_text(encoding="utf-8"), encoding="utf-8")
    ctx.archive.archive_snapshot(review_out, review_out.name, "reviewer",
                                 reviewer, ctx.cur_stage, ctx.cur_round)
    ctx.archive.archive_snapshot(verdict_out, verdict_out.name, "reviewer",
                                 reviewer, ctx.cur_stage, ctx.cur_round)


def write_spec_comparison_index(ctx: WorkflowContext) -> None:
    (ctx.spec_dir / "spec-comparison.md").write_text(
        "# Dual Spec Comparison\n\n"
        "Review these files before choosing the final spec owner:\n\n"
        f"- Candidate A: {ctx.spec_dir / 'spec-a.md'}\n"
        f"- Candidate B: {ctx.spec_dir / 'spec-b.md'}\n"
        f"- A's review of B: {ctx.spec_dir / 'spec-b.review-by-a.md'}\n"
        f"- B's review of A: {ctx.spec_dir / 'spec-a.review-by-b.md'}\n"
        f"- A's comparison table: {ctx.spec_dir / 'spec-comparison-a.md'}\n"
        f"- B's comparison table: {ctx.spec_dir / 'spec-comparison-b.md'}\n\n"
        "Decision commands:\n\n"
        "- a: adopt Candidate A as the base final spec\n"
        "- b: adopt Candidate B as the base final spec\n"
        "- ma: use Candidate A as base and explicitly adopt selected items from Candidate B\n"
        "- mb: use Candidate B as base and explicitly adopt selected items from Candidate A\n",
        encoding="utf-8",
    )
    ctx.archive.archive_snapshot(ctx.spec_dir / "spec-comparison.md",
                                 "spec-comparison.md", "workflow", "workflow",
                                 ctx.cur_stage, ctx.cur_round)


def write_dual_spec_decision_file(ctx: WorkflowContext, decision: str) -> None:
    owner_slot = dual_spec_owner_slot(decision)
    reviewer_slot = reviewer_slot_for_owner_slot(owner_slot)
    (ctx.spec_dir / "spec-decision.md").write_text(
        "# Dual Spec Decision\n\n"
        f"- decision: {decision}\n"
        f"- selected owner slot: {owner_slot}\n"
        f"- selected owner engine: {engine_for_slot(ctx, owner_slot)}\n"
        f"- reviewer slot: {reviewer_slot}\n"
        f"- reviewer engine: {engine_for_slot(ctx, reviewer_slot)}\n"
        f"- candidate A: {ctx.spec_dir / 'spec-a.md'}\n"
        f"- candidate B: {ctx.spec_dir / 'spec-b.md'}\n\n"
        f"The selected owner produces or owns the final {ctx.spec_dir / 'spec.md'}.\n"
        "The reviewer must approve the final spec before implementation "
        "planning starts.\n",
        encoding="utf-8",
    )
    ctx.archive.archive_snapshot(ctx.spec_dir / "spec-decision.md",
                                 "spec-decision.md", "workflow", "workflow",
                                 ctx.cur_stage, ctx.cur_round)


def human_gate_dual_spec_decision(ctx: WorkflowContext) -> None:
    ctx.archive.log_section("dual spec human selection", "workflow", "workflow",
                            ctx.cur_stage, ctx.cur_round, echo=ctx.echo)
    ctx.notify("adversarial-ai-coding: dual spec comparison awaits human "
               f"selection ({ctx.spec_dir / 'spec-comparison.md'})")
    ctx.echo("\n### Human checkpoint: compare dual spec candidates.")
    for name in ("spec-a.md", "spec-b.md", "spec-comparison-a.md",
                 "spec-comparison-b.md", "spec-comparison.md"):
        ctx.echo(f"### - {ctx.spec_dir / name}")
    ctx.echo("### Choose: a, b, ma, or mb. Final spec review and human "
             "approval run after this selection.")
    while True:
        decision = normalize_dual_spec_decision(
            ctx.ask("Dual spec decision [a/b/ma/mb]:"))
        if decision:
            break
        ctx.echo("Invalid decision. Enter a, b, ma, or mb.")
    owner_slot = dual_spec_owner_slot(decision)
    other_slot = reviewer_slot_for_owner_slot(owner_slot)
    if decision.startswith("merge-"):
        write_spec_merge_request_template(ctx, owner_slot, other_slot)
        ctx.echo(f"\n### Edit {ctx.wf / 'spec-merge-request.md'} now.")
        ctx.echo("### List the exact items the selected owner must adopt from "
                 f"{candidate_spec_for_slot(ctx, other_slot)}.")
        answer = ctx.ask("Enter y after editing the merge request; anything "
                         "else aborts:")
        if answer not in ("y", "Y"):
            raise WorkflowAbort("Aborted: merge request was not approved.")
        if not merge_request_has_content(ctx):
            raise WorkflowAbort(
                f"Aborted: {ctx.wf / 'spec-merge-request.md'} does not contain "
                "explicit adoption instructions."
            )
        ctx.archive.archive_snapshot(ctx.wf / "spec-merge-request.md",
                                     "spec-merge-request.md", "workflow",
                                     "workflow", ctx.cur_stage, ctx.cur_round)
    write_dual_spec_decision_file(ctx, decision)
    ctx.dual_spec_decision = decision


def restore_dual_spec_decision(ctx: WorkflowContext) -> None:
    # C5: on resume, a skipped select-spec stage leaves the decision empty;
    # every later stage reads the owner/reviewer roles (sh:1598-1620).
    if not ctx.settings.dual_spec or ctx.dual_spec_decision:
        return
    path = ctx.spec_dir / "spec-decision.md"
    if not path.is_file():
        raise WorkflowAbort(
            f"!! DUAL_SPEC run has no decision yet and no {path} to restore it from."
        )
    decision = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- decision: "):
            decision = line[len("- decision: "):]
            break
    slot = dual_spec_owner_slot(decision)
    if slot is None:
        raise WorkflowAbort(
            f"!! Invalid decision [{decision}] in {path}; cannot restore the "
            "dual-spec selection."
        )
    if decision.startswith("merge-") and not (ctx.wf / "spec-merge-request.md").is_file():
        raise WorkflowAbort(
            f"!! Decision {decision} needs {ctx.wf / 'spec-merge-request.md'}, "
            "which is missing.\n   Restore it from the run archive under "
            f"{ctx.archive.run_dir.parent}, then resume again."
        )
    ctx.dual_spec_decision = decision
    set_spec_roles_from_slot(ctx, slot)
    ctx.echo_err(f"(restored dual-spec decision: {decision}; owner "
                 f"{ctx.spec_roles.owner_slot}={ctx.spec_roles.owner_engine})")


def apply_dual_spec_decision(ctx: WorkflowContext, decision: str, task: str) -> None:
    owner_slot = dual_spec_owner_slot(decision)
    if owner_slot is None:
        raise WorkflowAbort(f"Unsupported dual spec decision:{decision}")
    set_spec_roles_from_slot(ctx, owner_slot)
    other_slot = ctx.spec_roles.reviewer_slot
    base_file = candidate_spec_for_slot(ctx, owner_slot)
    other_file = candidate_spec_for_slot(ctx, other_slot)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = ctx.spec_dir / "spec.md"
    spec_file.write_text(base_file.read_text(encoding="utf-8"), encoding="utf-8")
    if decision.startswith("merge-"):
        prompt = render_prompt(ctx.prompts_dir, "dual-spec-merge-final", {
            "BASE_FILE": str(base_file),
            "OTHER_FILE": str(other_file),
            "MERGE_REQUEST_FILE": str(ctx.wf / "spec-merge-request.md"),
            "SPEC_FILE": str(spec_file),
            "TASK": task,
        })
        work(ctx, ctx.spec_roles.owner_engine, prompt)
    review_loop(ctx, ctx.spec_roles.reviewer_engine, ctx.spec_roles.owner_engine,
                dual_spec_final_review_scope(ctx, decision))
    human_gate_spec(ctx)


def run_dual_spec_spec_stage(ctx: WorkflowContext, task: str) -> None:
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    spec_a = ctx.spec_dir / "spec-a.md"
    spec_b = ctx.spec_dir / "spec-b.md"

    if begin_stage(ctx, "write-spec-a", spec_a):
        work(ctx, ctx.settings.engine_a,
             render_prompt(ctx.prompts_dir, "dual-spec-write-candidate", {
                 "SPEC_FILE": str(spec_a), "OTHER_SPEC_FILE": str(spec_b),
                 "TASK": task}))
        end_stage(ctx)

    if begin_stage(ctx, "write-spec-b", spec_b):
        work(ctx, ctx.settings.engine_b,
             render_prompt(ctx.prompts_dir, "dual-spec-write-candidate", {
                 "SPEC_FILE": str(spec_b), "OTHER_SPEC_FILE": str(spec_a),
                 "TASK": task}))
        end_stage(ctx)

    review_a = ctx.spec_dir / "spec-a.review-by-b.md"
    verdict_a = ctx.spec_dir / "spec-a.verdict-by-b.json"
    if begin_stage(ctx, "review-spec-a", review_a, verdict_a):
        scope = render_prompt(ctx.prompts_dir, "review-scope-candidate-spec", {
            "SPEC_FILE": str(spec_a), "CANDIDATE": "A", "OTHER_CANDIDATE": "B"})
        run_candidate_spec_review(ctx, ctx.settings.engine_b, scope,
                                  review_a, verdict_a)
        end_stage(ctx)

    review_b = ctx.spec_dir / "spec-b.review-by-a.md"
    verdict_b = ctx.spec_dir / "spec-b.verdict-by-a.json"
    if begin_stage(ctx, "review-spec-b", review_b, verdict_b):
        scope = render_prompt(ctx.prompts_dir, "review-scope-candidate-spec", {
            "SPEC_FILE": str(spec_b), "CANDIDATE": "B", "OTHER_CANDIDATE": "A"})
        run_candidate_spec_review(ctx, ctx.settings.engine_a, scope,
                                  review_b, verdict_b)
        end_stage(ctx)

    for slot, engine in (("a", ctx.settings.engine_a), ("b", ctx.settings.engine_b)):
        comparison = ctx.spec_dir / f"spec-comparison-{slot}.md"
        if begin_stage(ctx, f"compare-specs-{slot}", comparison):
            work(ctx, engine,
                 render_prompt(ctx.prompts_dir, "dual-spec-compare", {
                     "OUTPUT_FILE": str(comparison),
                     "SPEC_A_FILE": str(spec_a), "SPEC_B_FILE": str(spec_b),
                     "SPEC_A_REVIEW_FILE": str(review_a),
                     "SPEC_B_REVIEW_FILE": str(review_b)}))
            end_stage(ctx)

    # Kept outside the stage guards: idempotent pure file write, no AI cost.
    write_spec_comparison_index(ctx)

    if begin_stage(ctx, "select-spec", ctx.spec_dir / "spec-decision.md"):
        human_gate_dual_spec_decision(ctx)
        end_stage(ctx)
    restore_dual_spec_decision(ctx)  # a skipped select-spec leaves it empty

    if begin_stage(ctx, "finalize-spec", ctx.spec_dir / "spec.md"):
        apply_dual_spec_decision(ctx, ctx.dual_spec_decision, task)
        end_stage(ctx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_dual_spec.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/dual_spec.py tests/test_dual_spec.py
git commit -m "feat: port dual-spec mode

Port the dual-spec pipeline: independent candidates, advisory cross
reviews, comparison tables and index, the interactive owner selection
with merge-request validation, the decision file, C5 decision restore
on resume, and the finalize path that reviews the selected spec and
passes the human gate. All eight sub-stages are resume-skippable."
```

---

### Task 3: AGENTS.md bootstrap (`prompts.py`)

Bash reference: `adversarial-ai-coding.sh:1005-1033`.
Bash tests ported: `tests/helpers.test.sh:510-532`.

**Files:**
- Modify: `src/adversarial_ai_coding/prompts.py` (append)
- Test: `tests/test_agents_bootstrap.py`

**Interfaces:**
- `prompts.AGENTS_MARKER = "<!-- adversarial-ai-coding:begin -->"`
- `prompts.default_agents_template(env: Mapping[str, str]) -> Path` —
  `AGENTS_TEMPLATE` env, else `RESOURCES_DIR`/`REPO_ROOT` +
  `resources/AGENTS.template.md`.
- `prompts.write_agents_section(template: Path) -> str` — template text;
  raises `PromptTemplateError` with the bash wording when missing.
- `prompts.bootstrap_agents_md(cwd: Path, template: Path, echo, echo_err) -> None`
  — create-only semantics: existing AGENTS.md without the marker gets the
  merge note; a missing template warns and leaves no empty files; CLAUDE.md
  pointer file is created only when absent.

- [ ] **Step 1: Write the failing tests**

`tests/test_agents_bootstrap.py`:

```python
"""Ports helpers.test.sh:510-532 (AGENTS.md bootstrap)."""

import pytest

from adversarial_ai_coding.prompts import (
    AGENTS_MARKER,
    PromptTemplateError,
    REPO_ROOT,
    bootstrap_agents_md,
    default_agents_template,
    write_agents_section,
)


def test_default_template_lives_under_resources():
    assert default_agents_template({}) == REPO_ROOT / "resources" / "AGENTS.template.md"
    assert default_agents_template({}).is_file()
    assert default_agents_template({"AGENTS_TEMPLATE": "X"}).name == "X"


def test_write_agents_section_has_marker():
    text = write_agents_section(default_agents_template({}))
    assert AGENTS_MARKER in text


def test_write_agents_section_missing_template_fails():
    with pytest.raises(PromptTemplateError, match="AGENTS.md template not found"):
        write_agents_section(default_agents_template({"AGENTS_TEMPLATE": "/nonexistent"}))


def sinks():
    out, err = [], []
    return out, err


def test_bootstrap_creates_agents_and_claude_md(tmp_path):
    out, err = sinks()
    bootstrap_agents_md(tmp_path, default_agents_template({}), out.append, err.append)
    assert AGENTS_MARKER in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert any("Created AGENTS.md" in line for line in out)


def test_bootstrap_does_not_overwrite_existing(tmp_path):
    (tmp_path / "AGENTS.md").write_text("my own rules\n", encoding="utf-8")
    out, err = sinks()
    bootstrap_agents_md(tmp_path, default_agents_template({}), out.append, err.append)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "my own rules\n"
    assert any("merge them manually" in line for line in err)


def test_bootstrap_missing_template_leaves_no_empty_files(tmp_path):
    out, err = sinks()
    bootstrap_agents_md(tmp_path, tmp_path / "nonexistent-template",
                        out.append, err.append)  # must not raise
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_agents_bootstrap.py -q`
Expected: FAIL — ImportError on the new names.

- [ ] **Step 3: Append to `src/adversarial_ai_coding/prompts.py`**

```python
AGENTS_MARKER = "<!-- adversarial-ai-coding:begin -->"


def default_agents_template(env: Mapping[str, str]) -> Path:
    if env.get("AGENTS_TEMPLATE"):
        return Path(env["AGENTS_TEMPLATE"])
    resources = Path(env.get("RESOURCES_DIR") or REPO_ROOT / "resources")
    return resources / "AGENTS.template.md"


def write_agents_section(template: Path) -> str:
    if not template.is_file():
        raise PromptTemplateError(
            f"(AGENTS.md template not found:{template}; keep "
            "resources/AGENTS.template.md with the script or set AGENTS_TEMPLATE)"
        )
    return template.read_text(encoding="utf-8")


def bootstrap_agents_md(cwd: Path, template: Path, echo, echo_err) -> None:
    # Create missing files only; never overwrite existing user rules (sh:1022).
    agents = cwd / "AGENTS.md"
    if agents.is_file():
        if AGENTS_MARKER not in agents.read_text(encoding="utf-8"):
            echo_err("(note: AGENTS.md exists but does not include "
                     "adversarial-ai-coding rules; run \"print-agents\" and "
                     "merge them manually)")
    else:
        try:
            agents.write_text(write_agents_section(template), encoding="utf-8")
        except PromptTemplateError as exc:
            echo_err(str(exc))
            return  # missing template: leave no empty files, keep the workflow going
        echo("Created AGENTS.md with adversarial-ai-coding cross-review rules.")
    claude = cwd / "CLAUDE.md"
    if not claude.is_file():
        claude.write_text(
            "Follow the adversarial-ai-coding cross-review rules in AGENTS.md.\n",
            encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass, then commit**

Run: `uv run pytest -q` — suite green.

```bash
git add src/adversarial_ai_coding/prompts.py tests/test_agents_bootstrap.py
git commit -m "feat: port AGENTS.md bootstrap

Create-only bootstrap of AGENTS.md from the resources template with the
marker check and merge note for pre-existing files, a CLAUDE.md pointer
created only when absent, and a missing template downgrading to a
warning that leaves no empty files behind."
```

---

### Task 4: `finish()` and the main stage pipeline (`run_workflow`)

Bash reference: `adversarial-ai-coding.sh:1701-1748` (finish),
`:1896-2003` (pipeline).
Bash tests ported: `tests/helpers.test.sh:1051-1084` (idempotent PR).

**Files:**
- Modify: `src/adversarial_ai_coding/workflow.py` (append)
- Test: `tests/test_finish_pipeline.py`

**Interfaces:**
- `workflow.finish(ctx, task: str, *, which=shutil.which, run_git=..., run_gh=...) -> None`
  — writes `pr-body.md` (exact bash template), prints completion +
  metrics summary, archives pr-body/suggestions; with OPEN_PR + gh +
  origin: push, then `gh pr view` (existing PR reported, create skipped)
  else `gh pr create`. The gh/git runners are injectable for tests;
  defaults use subprocess in `ctx.workspace`.
- `workflow.run_workflow(ctx, task: str) -> None` — the full pipeline:
  dual/single spec stage, decision restore, commit-spec, plan, acceptance
  tests (protected list build from `git diff --name-only base HEAD`
  excluding `spec_dir/`), write-code queue loop with build gate + commit +
  queue pop + checkbox tick, full gate + branch review + commit-if-dirty,
  final review and fixes, `finish`, completed marker.

- [ ] **Step 1: Write the failing tests**

`tests/test_finish_pipeline.py`:

```python
"""Ports helpers.test.sh:1051-1084 (idempotent finish) plus a pipeline smoke
test with everything below run_workflow stubbed out."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.runstate import RunState
from adversarial_ai_coding.workflow import finish, run_workflow


def test_finish_reports_existing_pr_instead_of_recreating(make_ctx):
    ctx = make_ctx({"OPEN_PR": "1", "RETRY_ON_LIMIT": "0"})
    lines = []
    ctx.echo = lines.append
    gh_calls = []

    def fake_gh(args, cwd):
        gh_calls.append(args)
        if args[:2] == ["pr", "view"]:
            return 0, "https://example.com/pr/1"
        pytest.fail("pr create must not run when a PR exists")

    finish(ctx, "task title", which=lambda n: "gh", run_gh=fake_gh,
           run_git=lambda args, cwd: (0, "origin-url" if "get-url" in args else ""))
    assert any("PR already exists: https://example.com/pr/1" in l for l in lines)
    assert (ctx.wf / "pr-body.md").is_file()


def test_finish_creates_pr_when_missing(make_ctx):
    ctx = make_ctx({"OPEN_PR": "1", "RETRY_ON_LIMIT": "0"})
    gh_calls = []

    def fake_gh(args, cwd):
        gh_calls.append(args)
        return (1, "") if args[:2] == ["pr", "view"] else (0, "CREATE-CALLED")

    finish(ctx, "task title", which=lambda n: "gh", run_gh=fake_gh,
           run_git=lambda args, cwd: (0, "origin-url" if "get-url" in args else ""))
    assert any(args[:2] == ["pr", "create"] for args in gh_calls)


def test_finish_without_open_pr_prints_commands(make_ctx):
    ctx = make_ctx({"RETRY_ON_LIMIT": "0"})
    lines = []
    ctx.echo = lines.append
    finish(ctx, "long task title\nsecond line",
           which=lambda n: None, run_gh=None, run_git=None)
    joined = "\n".join(lines)
    assert "git push -u origin" in joined
    assert "gh pr create --title" in joined
    assert "long task title" in joined       # first line only becomes the title
    assert "second line" in (ctx.wf / "pr-body.md").read_text(encoding="utf-8")


def test_run_workflow_single_spec_stage_order(make_ctx, new_repo, monkeypatch):
    ctx = make_ctx()
    ctx.state = RunState.create(new_repo / ".workflow" / "state", "run", "t\n")
    ctx.run_id = "run"
    order = []

    def fake_work(c, engine, prompt):
        order.append(("work", c.cur_stage))
        if c.cur_stage == "write-spec":
            c.spec_dir.mkdir(parents=True, exist_ok=True)
            (c.spec_dir / "spec.md").write_text("# Spec\n", encoding="utf-8")
        if c.cur_stage == "write-implementation-plan":
            (c.spec_dir / "plan.md").write_text("- [ ] only task\n", encoding="utf-8")

    monkeypatch.setattr(wf_mod, "work", fake_work)
    monkeypatch.setattr(wf_mod, "review_loop_ref",
                        lambda c, r, w, s, gate_cmd="": order.append(("review", c.cur_stage)))
    monkeypatch.setattr(wf_mod, "human_gate_spec", lambda c: order.append(("human", c.cur_stage)))
    monkeypatch.setattr(wf_mod, "gate_loop_ref",
                        lambda cmd, **kw: order.append(("gate", cmd)))
    monkeypatch.setattr(wf_mod, "finish", lambda c, t, **kw: order.append(("finish", t)))
    run_workflow(ctx, "demo task")
    stages = [s for s in ctx.state.completed_stages()]
    assert stages == ["write-spec", "commit-spec", "write-implementation-plan",
                      "write-acceptance-tests", "write-code",
                      "final-review-and-fixes"]
    assert ctx.state.is_completed()
    assert ("human", "write-spec") in order
    assert ("finish", "demo task") in order
    # The queue was consumed and the plan checkbox ticked.
    assert (ctx.spec_dir / "plan.md").read_text(encoding="utf-8").startswith("- [x]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_finish_pipeline.py -q`
Expected: FAIL — ImportError on `finish` / `run_workflow`.

- [ ] **Step 3: Append to `src/adversarial_ai_coding/workflow.py`**

Lazy references for cycle-free patching (module top, after imports):

```python
# review/gates are imported lazily to avoid the review->workflow cycle;
# these module-level names exist so the pipeline and tests share one seam.
def review_loop_ref(ctx, reviewer, worker, scope, gate_cmd=""):
    from .review import review_loop
    review_loop(ctx, reviewer, worker, scope, gate_cmd)


def gate_loop_ref(cmd, **kwargs):
    from .gates import gate_loop
    gate_loop(cmd, **kwargs)
```

Then `finish` and `run_workflow`:

```python
import shutil


def _run_git_default(args, cwd):
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout.strip()


def _run_gh_default(args, cwd):
    proc = subprocess.run(["gh", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout.strip()


def finish(ctx: WorkflowContext, task: str, *, which=shutil.which,
           run_git=_run_git_default, run_gh=_run_gh_default) -> None:
    from .archive import metrics_summary
    from .gitops import current_branch

    ctx.archive.log_section("finish", "workflow", "workflow", ctx.cur_stage,
                            ctx.cur_round, echo=ctx.echo)
    branch = current_branch(ctx.workspace)
    title = task.split("\n", 1)[0][:72]
    roles = ctx.spec_roles
    (ctx.wf / "pr-body.md").write_text(
        f"## Task\n\n{task}\n\n## Artifacts\n\n"
        f"- Spec with assumptions and open questions:`{ctx.spec_dir}/spec.md`\n"
        f"- Implementation plan:`{ctx.spec_dir}/plan.md`\n\n"
        f"Generated by adversarial-ai-coding, with original slots "
        f"A={ctx.settings.engine_a} and B={ctx.settings.engine_b}.\n"
        f"Final spec owner/worker: {roles.owner_slot}={roles.owner_engine}. "
        f"Reviewer: {roles.reviewer_slot}={roles.reviewer_engine}.\n"
        "Each stage passed deterministic quality gates and cross-review. "
        "Acceptance tests were written by the reviewer and protected against "
        "worker edits.\n",
        encoding="utf-8",
    )
    ctx.echo(f"\nAll stages complete. Spec and plan are in {ctx.spec_dir}/, "
             f"and the run log is at {ctx.archive.log_path}")
    if ctx.archive.metrics_path.is_file():
        ctx.echo("")
        ctx.echo(f"Run metrics (details:{ctx.archive.metrics_path}; review "
                 "rounds are a prompt-quality signal):")
        ctx.echo(metrics_summary(ctx.archive.metrics_path))
    ctx.archive.archive_snapshot(ctx.wf / "pr-body.md", "pr-body.md",
                                 "workflow", "workflow", ctx.cur_stage, ctx.cur_round)
    ctx.archive.archive_snapshot(ctx.suggestions_path, "suggestions.md",
                                 "workflow", "workflow", ctx.cur_stage, ctx.cur_round)
    has_origin = (run_git is not None
                  and run_git(["remote", "get-url", "origin"], ctx.workspace)[0] == 0)
    if ctx.settings.open_pr and which("gh") and has_origin:
        run_git(["push", "-u", "origin", branch], ctx.workspace)
        # Idempotent on resume: a PR may already exist from an interrupted
        # attempt, and a second create would fail the run forever (sh:1733-1739).
        rc, url = run_gh(["pr", "view", "--json", "url", "--jq", ".url"],
                         ctx.workspace)
        if rc == 0 and url:
            ctx.echo(f"PR already exists: {url} (skipping gh pr create)")
        else:
            run_gh(["pr", "create", "--title", title, "--body-file",
                    str(ctx.wf / "pr-body.md")], ctx.workspace)
    else:
        ctx.echo("")
        ctx.echo("Next steps, run manually:")
        ctx.echo(f"  git push -u origin {branch}")
        ctx.echo(f'  gh pr create --title "{title}" --body-file {ctx.wf / "pr-body.md"}')
        if ctx.settings.open_pr:
            ctx.echo_err("(OPEN_PR=1 but gh or origin remote is missing; "
                         "printed commands instead)")
    ctx.notify(f"adversarial-ai-coding: all stages complete ({branch})")


def run_workflow(ctx: WorkflowContext, task: str) -> None:
    from .gitops import head_sha, status_porcelain
    from .runstate import (ensure_task_queue, mark_plan_task_done,
                           pop_task_queue, remaining_tasks,
                           restore_or_record_acceptance_base)

    spec_file = ctx.spec_dir / "spec.md"
    plan_file = ctx.spec_dir / "plan.md"

    if ctx.settings.dual_spec:
        from .dual_spec import run_dual_spec_spec_stage
        run_dual_spec_spec_stage(ctx, task)
    else:
        set_spec_roles_from_slot(ctx, "A")
        if begin_stage(ctx, "write-spec", spec_file):
            work(ctx, ctx.spec_roles.owner_engine,
                 render_prompt(ctx.prompts_dir, "write-spec",
                               {"SPEC_FILE": str(spec_file), "TASK": task}))
            scope = render_prompt(ctx.prompts_dir, "review-scope-spec",
                                  {"SPEC_FILE": str(spec_file)})
            review_loop_ref(ctx, ctx.spec_roles.reviewer_engine,
                            ctx.spec_roles.owner_engine, scope)
            human_gate_spec(ctx)
            end_stage(ctx)

    # Covers the resume where finalize-spec was also skipped (C5).
    from .dual_spec import restore_dual_spec_decision
    restore_dual_spec_decision(ctx)

    if begin_stage(ctx, "commit-spec"):
        commit_work(ctx, ctx.spec_roles.owner_engine,
                    "Spec, approved by review and human gate")
        end_stage(ctx)

    if begin_stage(ctx, "write-implementation-plan", plan_file):
        work(ctx, ctx.spec_roles.owner_engine,
             render_prompt(ctx.prompts_dir, "write-implementation-plan",
                           {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)}))
        scope = render_prompt(ctx.prompts_dir, "review-scope-plan",
                              {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)})
        review_loop_ref(ctx, ctx.spec_roles.reviewer_engine,
                        ctx.spec_roles.owner_engine, scope)
        commit_work(ctx, ctx.spec_roles.owner_engine, "Implementation plan")
        end_stage(ctx)

    # Adversarial TDD: reviewer writes acceptance tests, worker reviews them.
    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    if begin_stage(ctx, "write-acceptance-tests", protected_list, protected_base):
        test_base = restore_or_record_acceptance_base(
            ctx.state, lambda: head_sha(ctx.workspace))
        work(ctx, ctx.spec_roles.reviewer_engine,
             render_prompt(ctx.prompts_dir, "write-acceptance-tests",
                           {"SPEC_FILE": str(spec_file), "SPEC_DIR": str(ctx.spec_dir)}))
        scope = render_prompt(ctx.prompts_dir, "review-scope-acceptance-tests",
                              {"TEST_BASE": test_base, "SPEC_FILE": str(spec_file)})
        review_loop_ref(ctx, ctx.spec_roles.owner_engine,
                        ctx.spec_roles.reviewer_engine, scope)
        commit_work(ctx, ctx.spec_roles.reviewer_engine, "Acceptance tests")
        from .gitops import git_out
        changed = git_out(["diff", "--name-only", test_base, "HEAD"], ctx.workspace)
        spec_prefix = str(ctx.spec_dir).replace("\\", "/") + "/"
        names = [n for n in changed.splitlines()
                 if n and not n.startswith(spec_prefix)]
        protected_list.write_text("".join(n + "\n" for n in names), encoding="utf-8")
        protected_base.write_text(head_sha(ctx.workspace) + "\n", encoding="utf-8")
        ctx.archive.archive_snapshot(protected_list, "protected-tests.txt",
                                     "workflow", "workflow", ctx.cur_stage, ctx.cur_round)
        ctx.archive.archive_snapshot(protected_base, "protected-base.sha",
                                     "workflow", "workflow", ctx.cur_stage, ctx.cur_round)
        if names:
            ctx.log("Protected acceptance test files:\n"
                    + "\n".join(f"  - {n}" for n in names))
        else:
            ctx.echo_err("(warning: acceptance-test stage produced no files; "
                         "test protection is disabled)")
        end_stage(ctx)

    # Small batches: one task per commit (sh:1958-1986).
    if begin_stage(ctx, "write-code"):
        if ctx.state is not None:  # the queue lives in claimed run state
            ensure_task_queue(ctx.state, plan_file)
            total = len(remaining_tasks(ctx.state))
            i = 1
            while remaining_tasks(ctx.state):
                task_line = remaining_tasks(ctx.state)[0]
                ctx.log(f"--- Task {i}/{total}:{task_line} ---")
                work(ctx, ctx.spec_roles.owner_engine,
                     render_prompt(ctx.prompts_dir, "implement-plan-task", {
                         "PLAN_FILE": str(plan_file), "TASK": task_line,
                         "PROTECTED_TESTS_FILE": str(protected_list)}))
                gate_loop_ref(ctx.build_gate_cmd, cwd=ctx.workspace,
                              prompts_dir=ctx.prompts_dir,
                              max_rounds=ctx.settings.max_rounds,
                              do_work=lambda p: work(ctx, ctx.spec_roles.owner_engine, p),
                              log=ctx.log, notify=ctx.notify, stage=ctx.cur_stage)
                commit_work(ctx, ctx.spec_roles.owner_engine, f'Task "{task_line}"')
                pop_task_queue(ctx.state)
                mark_plan_task_done(plan_file, task_line)  # UI only
                i += 1
        ctx.log("--- All tasks complete; running full quality gate. "
                "Acceptance tests must pass. ---")
        gate_loop_ref(ctx.gate_cmd, cwd=ctx.workspace, prompts_dir=ctx.prompts_dir,
                      max_rounds=ctx.settings.max_rounds,
                      do_work=lambda p: work(ctx, ctx.spec_roles.owner_engine, p),
                      log=ctx.log, notify=ctx.notify, stage=ctx.cur_stage)
        scope = render_prompt(ctx.prompts_dir, "review-scope-branch", {
            "SPEC_FILE": str(spec_file),
            "PROTECTED_TESTS_FILE": str(protected_list)})
        review_loop_ref(ctx, ctx.spec_roles.reviewer_engine,
                        ctx.spec_roles.owner_engine, scope, gate_cmd=ctx.gate_cmd)
        commit_if_dirty(ctx, ctx.spec_roles.owner_engine, "Review fixes")
        end_stage(ctx)

    if begin_stage(ctx, "final-review-and-fixes"):
        work(ctx, ctx.spec_roles.owner_engine,
             render_prompt(ctx.prompts_dir, "final-self-review",
                           {"SUGGESTIONS_FILE": str(ctx.suggestions_path)}))
        gate_loop_ref(ctx.gate_cmd, cwd=ctx.workspace, prompts_dir=ctx.prompts_dir,
                      max_rounds=ctx.settings.max_rounds,
                      do_work=lambda p: work(ctx, ctx.spec_roles.owner_engine, p),
                      log=ctx.log, notify=ctx.notify, stage=ctx.cur_stage)
        scope = render_prompt(ctx.prompts_dir, "review-scope-final-acceptance",
                              {"SPEC_FILE": str(spec_file)})
        review_loop_ref(ctx, ctx.spec_roles.reviewer_engine,
                        ctx.spec_roles.owner_engine, scope, gate_cmd=ctx.gate_cmd)
        commit_if_dirty(ctx, ctx.spec_roles.owner_engine, "Final fixes")
        end_stage(ctx)

    finish(ctx, task)

    # Mark the run complete: RESUME_RUN then refuses it, RESUME_RUN=last skips it.
    if ctx.state is not None:
        ctx.state.mark_completed()
```

Note for the implementer: the pipeline smoke test patches `wf_mod.finish`,
so `run_workflow` must call the module-global `finish(...)` (it does, as
written — do not capture it into a local variable).

- [ ] **Step 4: Run tests to verify they pass, then commit**

Run: `uv run pytest -q` — suite green.

```bash
git add src/adversarial_ai_coding/workflow.py tests/test_finish_pipeline.py
git commit -m "feat: port finish handoff and the main stage pipeline

finish writes the PR body, prints metrics and the manual push/PR
commands, and is idempotent about existing PRs under OPEN_PR=1.
run_workflow drives the full stage graph — spec (single or dual),
commit, plan, adversarial acceptance tests with the protected list
built from the persisted base, the one-commit-per-task write-code queue
with build gates, the full gate plus branch review, the final
self-review round, finish, and the completed marker."
```

---

### Task 5: `cli.py`, console script, resume hint and exit codes

Bash reference: `adversarial-ai-coding.sh:91-123` (hint, exit trap),
`:332-339` (usage), `:1813-1894` (main startup), `:2006-2008` (entry).
Bash tests ported: `tests/helpers.test.sh:551-574` (preflight),
`:1003-1040` (hint, exit codes).

**Files:**
- Create: `src/adversarial_ai_coding/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)
- Test: `tests/test_cli.py`

**Interfaces:**
- `cli.main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, *, stdin_isatty: bool | None = None) -> int`
  — returns the exit code (0 success, 1 errors, 75 resumable quota abort,
  130 KeyboardInterrupt). The console script wraps it in `sys.exit`.
- `pyproject.toml`:
  ```toml
  [project.scripts]
  adversarial-ai-coding = "adversarial_ai_coding.cli:main_entry"
  ```
  with `cli.main_entry()` doing `sys.exit(main())`.
- Behavior (port of bash main + traps, in order):
  1. No arg and no RESUME_RUN → usage on stderr, rc 1.
  2. `print-agents` → template to stdout (missing template: message, rc 1).
  3. Task file argument resolves to its content (`Path.resolve()` recorded).
  4. RESUME_RUN: resolve + `RunState.resume` + `load_snapshot` +
     `check_immutable`; task snapshot is the single source of truth (C6),
     conflicting task argument → rc 1 with the bash wording.
  5. `Settings.from_env(env, run_id, snapshot)`; `validate_engines`;
     git work-tree check ("Run this script from the root of the target git
     repository."); `dual_spec_preflight`.
  6. Workspace setup/resume (`os.chdir` into a worktree), archive
     establishment, state create (fresh) with `init_live_state`,
     `.workflow/.gitignore`, run/log metadata, `archive_task`,
     `latest-run.txt`, `bootstrap_agents_md`.
  7. Gate resolution: env, then snapshot (`GATE_CMD`/`BUILD_GATE_CMD`
     keys), then detection; snapshot written back before the first AI call.
  8. `run_workflow`.
  9. Exception mapping: `WorkflowAbort` → message on stderr, its rc;
     `SettingsError`/`RunStateError`/`PromptTemplateError` → stderr, rc 1;
     `KeyboardInterrupt` → rc 130. A non-zero exit of an unfinished
     claimed run prints the resume hint exactly once
     (`RESUME_RUN=<id> adversarial-ai-coding`, with a `cd` prefix for
     worktree runs). The lock is always released (finally).

- [ ] **Step 1: Write the failing tests**

`tests/test_cli.py`:

```python
"""Ports helpers.test.sh:551-574 (preflight) and 1003-1040 (hint, exit codes)."""

import pytest

from adversarial_ai_coding import cli
from adversarial_ai_coding.prompts import AGENTS_MARKER


def run_cli(args=None, env=None, cwd=None, monkeypatch=None, **kwargs):
    if cwd is not None and monkeypatch is not None:
        monkeypatch.chdir(cwd)
    return cli.main(args or [], env or {}, stdin_isatty=False, **kwargs)


def test_no_args_prints_usage_rc1(capsys):
    assert cli.main([], {}, stdin_isatty=False) == 1
    err = capsys.readouterr().err
    assert "Usage:" in err
    assert "print-agents" in err


def test_print_agents(capsys):
    assert cli.main(["print-agents"], {}, stdin_isatty=False) == 0
    assert AGENTS_MARKER in capsys.readouterr().out


def test_print_agents_missing_template_fails(capsys, tmp_path):
    rc = cli.main(["print-agents"], {"AGENTS_TEMPLATE": str(tmp_path / "gone")},
                  stdin_isatty=False)
    assert rc != 0
    assert "AGENTS.md template not found" in capsys.readouterr().err


def test_not_a_git_repo_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["task"], {"AGENT_A": "sh", "AGENT_B": "pwd"}, stdin_isatty=False)
    assert rc == 1
    assert "root of the target git repository" in capsys.readouterr().err


def test_same_engine_blocked_without_branch_side_effect(new_repo, monkeypatch, capsys):
    from adversarial_ai_coding.gitops import current_branch
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    rc = cli.main(["task"], {"AGENT_A": "codex", "AGENT_B": "codex"},
                  stdin_isatty=False)
    assert rc == 1
    assert "cannot both use codex" in capsys.readouterr().err
    assert current_branch(new_repo) == "main"


def test_dual_spec_human_gate_blocked_before_branch(new_repo, monkeypatch, capsys):
    from adversarial_ai_coding.gitops import current_branch
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    rc = cli.main(["task"], {"AGENT_A": "sh", "AGENT_B": "pwd",
                             "DUAL_SPEC": "1", "HUMAN_GATE": "0"},
                  stdin_isatty=False)
    assert rc == 1
    assert "requires HUMAN_GATE=1" in capsys.readouterr().err
    assert current_branch(new_repo) == "main"


def test_resume_hint_printed_once_and_lock_released(new_repo, monkeypatch, capsys):
    # Interrupt after state is claimed: hint + rc + lock release (bash traps).
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(cli, "run_workflow",
                        lambda ctx, task: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = cli.main(["task"], {"AGENT_A": "sh", "AGENT_B": "pwd",
                             "AUTO_BRANCH": "0"}, stdin_isatty=False)
    assert rc == 130
    err = capsys.readouterr().err
    assert err.count("RESUME_RUN=") == 1
    state_root = new_repo / ".workflow" / "state"
    run_dir = next(state_root.iterdir())
    assert not (run_dir / "lock").exists()      # released
    assert not (run_dir / "completed").exists() # unfinished


def test_completed_run_does_not_advertise_resume(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)

    def finishing_workflow(ctx, task):
        ctx.state.mark_completed()
        raise SystemExit  # simulate a crash AFTER completion

    monkeypatch.setattr(cli, "run_workflow", finishing_workflow)
    with pytest.raises(SystemExit):
        cli.main(["task"], {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
                 stdin_isatty=False)
    assert "RESUME_RUN=" not in capsys.readouterr().err


def test_quota_abort_maps_to_75(new_repo, monkeypatch, capsys):
    from adversarial_ai_coding.config import WorkflowAbort
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(cli, "run_workflow",
                        lambda ctx, task: (_ for _ in ()).throw(
                            WorkflowAbort("quota", rc=75)))
    rc = cli.main(["task"], {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
                  stdin_isatty=False)
    assert rc == 75
    assert "RESUME_RUN=" in capsys.readouterr().err


def test_task_file_argument_is_read(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    captured = {}
    monkeypatch.setattr(cli, "run_workflow",
                        lambda ctx, task: captured.update(task=task))
    (new_repo / "task.md").write_text("task from file\n", encoding="utf-8")
    rc = cli.main(["task.md"], {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
                  stdin_isatty=False)
    assert rc == 0
    assert captured["task"] == "task from file\n"


def test_resume_task_conflict_fails(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    captured = {}
    monkeypatch.setattr(cli, "run_workflow", lambda ctx, task: None)
    # Claim a run, leave it unfinished but unlocked.
    from adversarial_ai_coding.runstate import RunState, write_snapshot
    state = RunState.create(new_repo / ".workflow" / "state", "r1", "snapshot task\n")
    write_snapshot(state.state_dir, {"engine_a": "sh", "engine_b": "pwd",
                                     "branch": "main"})
    state.release_lock()
    rc = cli.main(["different task"], {"RESUME_RUN": "r1", "AUTO_BRANCH": "0"},
                  stdin_isatty=False)
    assert rc == 1
    assert "task snapshot" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL — no module `cli`.

- [ ] **Step 3: Write `src/adversarial_ai_coding/cli.py` and wire the script**

```python
"""CLI entry point: startup checks, state claiming, abort handling.

Port of adversarial-ai-coding.sh:91-123 (resume hint, exit routing),
332-339 (usage), 1813-1894 (main startup), 2006-2008 (entry). Divergences:
jq is no longer required, and the tool name in the resume hint is the
console script instead of a script path.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping

from .archive import establish_run_archive
from .config import Settings, SettingsError, WorkflowAbort
from .dual_spec import dual_spec_preflight
from .engines import EngineSession, validate_engines
from .gates import detect_build_gate, detect_gate
from .gitops import current_branch, is_inside_work_tree, resume_workspace, setup_workspace
from .prompts import (PromptTemplateError, bootstrap_agents_md,
                      default_agents_template, default_prompts_dir,
                      write_agents_section)
from .runstate import (RunState, RunStateError, check_immutable,
                       init_live_state, load_snapshot, snapshot_values,
                       write_snapshot)
from .workflow import WorkflowContext, run_workflow

USAGE = """Usage:adversarial-ai-coding "task description"
      adversarial-ai-coding task.md         # If the argument is a file, use its contents as the task
      adversarial-ai-coding print-agents    # Print the AGENTS.md rule template and exit"""


def _print_resume_hint(run_id: str, use_worktree: bool, workspace: Path,
                       printed: set) -> None:
    if printed:
        return  # once per abort (sh:91-104)
    printed.add(True)
    if use_worktree:
        print(f"To resume this run:\n  cd {workspace} && "
              f"RESUME_RUN={run_id} adversarial-ai-coding", file=sys.stderr)
    else:
        print(f"To resume this run:\n  RESUME_RUN={run_id} adversarial-ai-coding",
              file=sys.stderr)


def main(argv: list[str] | None = None,
         env: Mapping[str, str] | None = None, *,
         stdin_isatty: bool | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ) if env is None else dict(env)
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()

    task_arg = argv[0] if argv else ""
    resume_run = env.get("RESUME_RUN", "")
    if not task_arg and not resume_run:
        print(USAGE, file=sys.stderr)
        return 1
    if task_arg == "print-agents":
        try:
            print(write_agents_section(default_agents_template(env)), end="")
            return 0
        except PromptTemplateError as exc:
            print(exc, file=sys.stderr)
            return 1

    state: RunState | None = None
    hint_printed: set = set()
    run_id = ""
    use_worktree = False
    workspace = Path.cwd()
    try:
        # -- task resolution (sh:1822-1832) ---------------------------------
        task_source_kind, task_source_path = "literal", ""
        task = task_arg
        if task_arg and Path(task_arg).is_file():
            task_source_kind = "file"
            task_source_path = str(Path(task_arg).resolve())
            print(f"Reading task description from file:{task_arg}")
            task = Path(task_arg).read_text(encoding="utf-8")

        # -- resume load (sh:285-288, 1834-1851) -----------------------------
        snapshot: dict[str, str] = {}
        wf = Path(".workflow")
        if resume_run:
            state = RunState.resume(wf / "state", resume_run)
            run_id = state.run_id
            snapshot = load_snapshot(state.state_dir)
            check_immutable(env, snapshot)
            task_snapshot = state.task_text()
            if task and task != task_snapshot:
                print("!! The task argument resolves to different text than the "
                      "resumed run's task snapshot.", file=sys.stderr)
                print("   Resume without a task argument (the snapshot is used), "
                      "or start a fresh run.", file=sys.stderr)
                return 1
            task = task_snapshot
            task_arg = snapshot.get("TASK_ARG", "")
            task_source_kind = snapshot.get("TASK_SOURCE_KIND", "literal")
            task_source_path = snapshot.get("TASK_SOURCE_PATH", "")
            print(f"Resuming run {run_id} (state: {state.state_dir})", file=sys.stderr)
        else:
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        settings = Settings.from_env(env, run_id, snapshot)
        use_worktree = settings.use_worktree

        validate_engines(settings)
        if not is_inside_work_tree(Path.cwd()):
            print("Run this script from the root of the target git repository.",
                  file=sys.stderr)
            return 1
        dual_spec_preflight(settings, stdin_isatty)

        print(f"Workflow settings:A={settings.engine_a}  B={settings.engine_b}  "
              f"DUAL_SPEC={'1' if settings.dual_spec else '0'}  "
              f"MAX_ROUNDS={settings.max_rounds}  SPEC_DIR={settings.spec_dir}")
        print(f"Task:{task}")

        # -- workspace (sh:1867) ----------------------------------------------
        if resume_run:
            resume_workspace(snapshot.get("BRANCH", ""), state, Path.cwd(),
                             lambda m: print(m, file=sys.stderr))
        else:
            workspace = setup_workspace(settings, run_id, Path.cwd())
            if workspace != Path.cwd():
                os.chdir(workspace)
                print(f"Created worktree:{workspace} (branch auto/{run_id}; "
                      "remove later with git worktree remove)")
        workspace = Path.cwd()
        wf = workspace / ".workflow"

        # -- archive and state (sh:1869-1881) -----------------------------------
        archive = establish_run_archive(wf / "runs", run_id, settings)
        if resume_run:
            init_live_state(wf, resume=True)
        else:
            state = RunState.create(wf / "state", run_id, task)
            init_live_state(wf, resume=False)
        (wf / ".gitignore").write_text("*\n", encoding="utf-8")
        archive.write_run_metadata(spec_dir=settings.spec_dir, wf=str(wf))
        archive.write_log_metadata()
        archive.archive_task(task_arg, task_source_kind, task_source_path, task)
        (wf / "latest-run.txt").write_text(str(archive.run_dir) + "\n",
                                           encoding="utf-8")
        archive.log_section("startup settings", "workflow", "workflow",
                            "startup", 0)

        bootstrap_agents_md(workspace, default_agents_template(env),
                            print, lambda m: print(m, file=sys.stderr))

        gate_cmd = env.get("GATE_CMD") or snapshot.get("GATE_CMD") or detect_gate(workspace)
        build_gate_cmd = (env.get("BUILD_GATE_CMD") or snapshot.get("BUILD_GATE_CMD")
                          or detect_build_gate(workspace))
        if gate_cmd:
            print(f"Quality gate:{gate_cmd}")
        else:
            print("(warning: no quality gate command detected; deterministic "
                  "gates are disabled. Set GATE_CMD to enable one.)",
                  file=sys.stderr)

        ctx = WorkflowContext(
            settings=settings, archive=archive, state=state,
            session=EngineSession(), workspace=workspace, wf=wf,
            prompts_dir=default_prompts_dir(env),
            spec_dir=workspace / settings.spec_dir,
            gate_cmd=gate_cmd, build_gate_cmd=build_gate_cmd, run_id=run_id,
        )
        ctx.spec_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot the effective settings before the first AI call; rewritten
        # every attempt so explicit resume overrides carry forward (sh:1892-1894).
        write_snapshot(state.state_dir, snapshot_values(
            settings, branch=current_branch(workspace), gate_cmd=gate_cmd,
            build_gate_cmd=build_gate_cmd, task_arg=task_arg,
            task_source_kind=task_source_kind, task_source_path=task_source_path))

        run_workflow(ctx, task)
        return 0

    except KeyboardInterrupt:
        _abort_message(130, state, run_id, use_worktree, workspace, hint_printed)
        return 130
    except WorkflowAbort as exc:
        print(exc, file=sys.stderr)
        _abort_message(exc.rc, state, run_id, use_worktree, workspace, hint_printed)
        return exc.rc
    except (SettingsError, RunStateError, PromptTemplateError) as exc:
        print(exc, file=sys.stderr)
        _abort_message(1, state, run_id, use_worktree, workspace, hint_printed)
        return 1
    finally:
        if state is not None:
            state.release_lock()


def _abort_message(rc: int, state, run_id, use_worktree, workspace,
                   hint_printed) -> None:
    # Single abort path (sh:106-114): unfinished claimed runs advertise resume.
    if rc != 0 and state is not None and not state.is_completed():
        print(f"!! Workflow interrupted (exit={rc}).", file=sys.stderr)
        _print_resume_hint(run_id, use_worktree, workspace, hint_printed)


def main_entry() -> None:
    sys.exit(main())
```

Add to `pyproject.toml`:

```toml
[project.scripts]
adversarial-ai-coding = "adversarial_ai_coding.cli:main_entry"
```

Run `uv sync` afterwards so the console script is installed.

- [ ] **Step 4: Run tests to verify they pass, then commit**

Run: `uv run pytest -q` — suite green.
Run: `uv run adversarial-ai-coding` — prints usage, exit code 1.
Run: `uv run adversarial-ai-coding print-agents | head -3` — template text.

```bash
git add src/adversarial_ai_coding/cli.py pyproject.toml uv.lock tests/test_cli.py src/adversarial_ai_coding/dual_spec.py tests/test_dual_spec.py
git commit -m "feat: port cli entry point with resumable abort handling

Port the startup sequence: usage, print-agents, task file resolution,
resume loading with the task-snapshot conflict check (C6), settings and
engine validation before any branch side effect, workspace setup or
resume, archive and state claiming, AGENTS bootstrap, gate resolution
from env/snapshot/detection, and the settings snapshot write before the
first AI call. Exceptions map to bash exit codes (1, 75, 130); an
unfinished claimed run prints the paste-ready resume hint exactly once
and always releases the run lock. Adds the console script; jq is no
longer a runtime requirement."
```

---

### Task 6: Offline interrupt-resume integration suite

Bash reference: `tests/resume.test.sh` scenarios 1-4 and 6 (scenario 5's
real SIGINT moves to plan 6's manual acceptance; scenario 7's pty flow is
covered by the dual-spec unit ports, as it already was on Windows).

**Files:**
- Create: `tests/fake_agent.py`
- Test: `tests/test_resume_integration.py`

**Interfaces:**
- `tests/fake_agent.py` — a standalone script (run via `sys.executable`)
  porting `write_fake_agent` (resume.test.sh:39-118): reads the prompt
  file named in the final argument, classifies the template by its first
  sentence, performs the minimal real work (spec/plan/acceptance/implement
  files, review verdicts), honors `FAKE_CALLS_LOG`, `FAKE_ABORT_ON`
  (quota message with an absolute reset 2 days out), `FAKE_ABORT_MODE=plain`,
  and `FAKE_ROLE` (worker/reviewer name for the call log).
- The integration tests call `cli.main` IN-PROCESS with
  `monkeypatch.chdir(repo)` and an env dict wiring
  `AGENT_A`/`AGENT_B` to `sys.executable` with
  `AGENT_A_ARGS`/`AGENT_B_ARGS` pointing at `fake_agent.py` plus a role
  flag. `GATE_CMD`/`BUILD_GATE_CMD` are set to a trivially-true command
  (`"exit 0"` under shell=True works on cmd.exe and POSIX shells).

- [ ] **Step 1: Write `tests/fake_agent.py`**

```python
"""Cross-platform fake engine for the resume integration suite.

Port of resume.test.sh write_fake_agent. Invoked as:
  python fake_agent.py --role fake-worker <extra args...> "<prompt>"
The prompt is the final argument; when it names a prompt file
("...follow it exactly: <path>"), the file's content is the template.
"""

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def classify(prompt: str) -> str:
    if prompt.startswith("You are a strict code reviewer.") and \
            "after dual spec selection" in prompt:
        return "review-dual-final"
    if prompt.startswith("You are a strict code reviewer."):
        return "review"
    if prompt.startswith("Write a spec for the following request"):
        return "write-spec"
    if prompt.startswith("Write an independent candidate spec"):
        return "write-candidate"
    if prompt.startswith("Write an implementation plan"):
        return "write-plan"
    if prompt.startswith("Write acceptance tests"):
        return "write-acceptance"
    if prompt.startswith("Implement this task from"):
        return "implement"
    if prompt.startswith("Compare the dual spec candidates"):
        return "compare"
    if prompt.startswith("Do a complete self-review"):
        return "final-review"
    if "is complete and approved. Commit all current changes" in prompt:
        return "commit"
    return "other"


def main() -> int:
    args = sys.argv[1:]
    name = "fake-agent"
    if "--role" in args:
        i = args.index("--role")
        name = args[i + 1]
        del args[i:i + 2]
    last = args[-1] if args else ""
    prompt = last
    match = re.search(r"follow it exactly: (.+)$", last.strip())
    if match and Path(match.group(1)).is_file():
        prompt = Path(match.group(1)).read_text(encoding="utf-8")

    kind = classify(prompt)
    log = os.environ.get("FAKE_CALLS_LOG", "calls.log")
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{name} {kind}\n")

    abort_on = os.environ.get("FAKE_ABORT_ON", "")
    if abort_on and Path(abort_on).is_file() and \
            Path(abort_on).read_text(encoding="utf-8").strip() == kind:
        if os.environ.get("FAKE_ABORT_MODE", "quota") == "plain":
            print("fake agent plain failure")
            return 1
        reset_at = (datetime.now() + timedelta(days=2)).strftime("%b %d, %Y %I:%M %p")
        print(f"You've hit your usage limit. Please try again at {reset_at}.")
        return 1

    def grep_target(pattern: str) -> str:
        m = re.search(pattern, prompt)
        return m.group(0) if m else ""

    if kind in ("review", "review-dual-final"):
        Path(".workflow").mkdir(exist_ok=True)
        Path(".workflow/review.md").write_text(f"approved by {name}\n", encoding="utf-8")
        Path(".workflow/verdict.json").write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}\n', encoding="utf-8")
    elif kind == "write-spec":
        target = grep_target(r"specs/[^ ]+/spec\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(
            "# Spec\n\nDemo feature.\n\n## Assumptions and Open Questions\n\n- none\n",
            encoding="utf-8")
    elif kind == "write-candidate":
        target = grep_target(r"specs/[^ ]+/spec-[ab]\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(f"# Candidate spec {target}\n", encoding="utf-8")
    elif kind == "write-plan":
        target = grep_target(r"specs/[^ ]+/plan\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(
            "# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n", encoding="utf-8")
    elif kind == "write-acceptance":
        Path("acc").mkdir(exist_ok=True)
        Path("acc/acceptance.txt").write_text("ACCEPTANCE CHECK\n", encoding="utf-8")
    elif kind == "implement":
        with open("src.txt", "a", encoding="utf-8") as f:
            f.write("implemented\n")
    elif kind == "compare":
        target = grep_target(r"specs/[^ ]+/spec-comparison-[ab]\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text("comparison table\n", encoding="utf-8")

    print(f"{name} did {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note: the classification sentences must match the real templates in
`resources/prompts/*.md` — they are the same strings the bash fake matched;
verify against the template files while implementing and adjust ONLY the
fake (never the templates).

- [ ] **Step 2: Write the integration tests**

`tests/test_resume_integration.py`:

```python
"""Port of tests/resume.test.sh scenarios 1-4 and 6 (offline, in-process)."""

import os
import sys
from pathlib import Path

import pytest

from adversarial_ai_coding import cli

FAKE = str(Path(__file__).parent / "fake_agent.py")


def _make_wrapper(work: Path, role: str) -> str:
    """One executable wrapper command per role.

    validate_engines rejects identical non-claude commands on both slots, so
    the two roles need two distinct command strings — exactly like the bash
    suite's fake-worker/fake-reviewer scripts. Windows gets a .cmd batch
    file (CreateProcess runs those natively); POSIX gets a shell script.
    """
    if os.name == "nt":
        path = work / f"fake-{role}.cmd"
        path.write_text(
            f'@"{sys.executable}" "{FAKE}" --role fake-{role} %*\r\n',
            encoding="utf-8")
    else:
        path = work / f"fake-{role}"
        path.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{FAKE}" --role fake-{role} "$@"\n',
            encoding="utf-8")
        path.chmod(0o755)
    return str(path)


def wf_env(work: Path, **overrides) -> dict:
    env = {
        "HUMAN_GATE": "0", "DUAL_SPEC": "0", "AUTO_BRANCH": "1",
        "USE_WORKTREE": "0", "OPEN_PR": "0",
        "GATE_CMD": "exit 0", "BUILD_GATE_CMD": "exit 0",
        "RETRY_ON_LIMIT": "1", "NOTIFY_CMD": "",
        "FAKE_CALLS_LOG": str(work / "calls.log"),
        "FAKE_ABORT_ON": str(work / "abort-on"),
        "AGENT_A": _make_wrapper(work, "worker"),
        "AGENT_B": _make_wrapper(work, "reviewer"),
    }
    env.update(overrides)
    return env


def run_cli(repo, env, args=None, monkeypatch=None):
    monkeypatch.chdir(repo)
    monkeypatch.setenv("PYTHONPATH", "")  # keep the fake agent's env clean
    return cli.main(args or ["demo task"], env, stdin_isatty=False)


def state_dir_of(repo: Path) -> Path:
    return next((repo / ".workflow" / "state").iterdir())


def calls(work: Path, pattern: str) -> int:
    log = work / "calls.log"
    if not log.is_file():
        return 0
    return sum(1 for line in log.read_text(encoding="utf-8").splitlines()
               if line == pattern)


def test_scenario1_quota_abort_then_resume(new_repo, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    env = wf_env(work)
    (work / "abort-on").write_text("write-plan\n", encoding="utf-8")

    rc = run_cli(new_repo, env, monkeypatch=monkeypatch)
    assert rc == 75                                     # typed quota abort
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState
    st = RunState(state_dir=state, run_id=state.name)
    stages = st.completed_stages()
    assert "write-spec" in stages and "commit-spec" in stages
    assert "write-implementation-plan" not in stages
    spec_calls_before = calls(work, "fake-worker write-spec")

    (work / "abort-on").unlink()
    env_resume = dict(env, RESUME_RUN=state.name)
    rc = run_cli(new_repo, env_resume, args=[], monkeypatch=monkeypatch)
    assert rc == 0
    assert calls(work, "fake-worker write-spec") == spec_calls_before
    assert (state / "completed").is_file()
    plan = next((new_repo / "specs").glob("*/plan.md"))
    text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in text and "- [x]" in text

    # Completed run refuses resume; unknown id lists the real one.
    assert run_cli(new_repo, dict(env, RESUME_RUN="last"), args=[],
                   monkeypatch=monkeypatch) == 1
    assert run_cli(new_repo, dict(env, RESUME_RUN="nonexistent"), args=[],
                   monkeypatch=monkeypatch) == 1


def test_scenario2_lost_ledger_line_reruns_stage(new_repo, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState
    st = RunState(state_dir=state, run_id=state.name)
    (state / "completed").unlink()
    stages = st.completed_stages()
    st._write_ledger(stages[:-1])           # drop the last recorded stage
    before = calls(work, "fake-worker final-review")
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker final-review") == before + 1  # at-least-once


def test_scenario3_acceptance_window_keeps_base(new_repo, tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState
    st = RunState(state_dir=state, run_id=state.name)
    base_before = (state / "acceptance-test-base").read_text(encoding="utf-8")
    (state / "completed").unlink()
    st._write_ledger([s for s in st.completed_stages()
                      if s != "write-acceptance-tests"])
    (new_repo / ".workflow" / "protected-tests.txt").unlink()
    (new_repo / ".workflow" / "protected-base.sha").unlink()
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 0
    rebuilt = (new_repo / ".workflow" / "protected-tests.txt").read_text(encoding="utf-8")
    assert "acc/acceptance.txt" in rebuilt
    assert (state / "acceptance-test-base").read_text(encoding="utf-8") == base_before


def test_scenario4_empty_queue_no_fallback(new_repo, tmp_path, monkeypatch, capsys):
    work = tmp_path / "work"
    work.mkdir()
    env = wf_env(work)
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    state = state_dir_of(new_repo)
    from adversarial_ai_coding.runstate import RunState
    st = RunState(state_dir=state, run_id=state.name)
    (state / "completed").unlink()
    st._write_ledger([s for s in st.completed_stages() if s != "write-code"])
    before = calls(work, "fake-worker implement")
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker implement") == before
    assert "falling back to one whole-plan implementation task" not in \
        capsys.readouterr().err


def test_scenario6_damaged_snapshot_refused_then_restored(new_repo, tmp_path,
                                                          monkeypatch):
    import json
    work = tmp_path / "work"
    work.mkdir()
    env = wf_env(work)
    (work / "abort-on").write_text("implement\n", encoding="utf-8")
    rc = run_cli(new_repo, dict(env, FAKE_ABORT_MODE="plain"),
                 monkeypatch=monkeypatch)
    assert rc != 0
    state = state_dir_of(new_repo)
    snapshot_path = state / "settings.json"
    backup = snapshot_path.read_text(encoding="utf-8")

    payload = json.loads(backup)
    payload["evil_key"] = "1"
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 1     # unknown key refused

    payload = json.loads(backup)
    del payload["schema"]
    snapshot_path.write_text(json.dumps(payload), encoding="utf-8")
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 1     # schemaless refused

    snapshot_path.write_text(backup, encoding="utf-8")
    (work / "abort-on").unlink()
    assert run_cli(new_repo, dict(env, RESUME_RUN=state.name), args=[],
                   monkeypatch=monkeypatch) == 0
```

Implementer notes (binding):
- `RunState(state_dir=..., run_id=...)` constructs an unlocked handle for
  ledger surgery; `_write_ledger` is intentionally reached into (tests may
  use private helpers for damage simulation, mirroring the bash suite's
  direct file edits).
- The fake wrappers' engine names resolve through `shutil.which` in
  `_resolve_argv0`; a full path with an extension passes through unchanged
  on both platforms.

- [ ] **Step 3: Run the suite**

Run: `uv run pytest tests/test_resume_integration.py -q`
Expected: 5 scenarios PASS (each does a full offline workflow; expect a few
seconds each).

Run: `uv run pytest -q`
Expected: whole suite green.

- [ ] **Step 4: Commit**

```bash
git add tests/fake_agent.py tests/test_resume_integration.py
git commit -m "test: port the offline interrupt-resume integration suite

Port resume.test.sh scenarios 1-4 and 6 onto pytest with a
cross-platform Python fake agent: quota abort mid-run with typed exit
75 and stage-skipping resume, at-least-once re-run after a lost ledger
entry, the acceptance crash window reusing its persisted base, the
empty task queue refusing the whole-plan fallback, and damaged
snapshots refusing to resume until restored. The real-SIGINT scenario
moves to plan 6 manual acceptance; the dual-spec pty scenario is
covered by the dual-spec unit ports, as it already was on Windows."
```

---

## Verification at the End of This Plan

Run: `uv run pytest -q` — whole suite green.
Manual smoke: `uv run adversarial-ai-coding print-agents`, and a full fake
run in a scratch repo using the Task 6 environment (optional; the
integration tests do the same in-process).

## Not in This Plan (deliberately)

- E2E fixture driver port, CI cutover, real-engine acceptance, bash
  removal, README updates: plan 6.
