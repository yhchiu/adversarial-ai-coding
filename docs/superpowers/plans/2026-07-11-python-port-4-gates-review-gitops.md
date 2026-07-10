# Python Port — Plan 4 of 6: Gates, Review, and Git Ops Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the middle layer between engines/state and the stage flow: git operations (workspace setup/resume, protected files, fallback commits, checkpoint verification), deterministic quality gates, the worker call wrapper `work()`, and the review machinery (`run_review`, `review_loop`).

**Architecture:** Plan 4 of the series implementing
`docs/superpowers/specs/2026-07-10-python-rewrite-design.md`. Builds on
plans 1-3. This plan creates `gitops.py`, `gates.py`, `review.py`, and
starts `workflow.py` with the shared `WorkflowContext` plus `work()` /
`check_protected()`; plan 5 adds the stage orchestration to the same file.
The shared typed-exit exception `WorkflowAbort` lives in `config.py` (the
common leaf) so gates/review/workflow can all raise it without import
cycles.

**Tech Stack:** Python 3.12+, stdlib only, pytest with the `new_repo` fixture from plan 3.

## Global Constraints

- Runtime dependencies: none (stdlib only); pytest dev-only.
- Bash files are FROZEN; cited lines are the behavior reference. Preserve
  bash's silent-failure semantics and its user-facing error wording.
- Every human-intervention stop in bash (`exit 1`) becomes
  `raise WorkflowAbort(message)` (rc 1); every quota give-up becomes
  `raise WorkflowAbort(message, rc=QUOTA_ABORT_RC)`. Nothing in these
  modules calls `sys.exit` — only `cli.py` (plan 5) exits.
- Commits: Conventional Commit format, detailed body, NO Co-Authored-By.
- `uv run pytest -q` green after every task; git-touching tests use
  throwaway repos only.
- Machine note: clear `PYTHONHOME`/`PYTHONPATH` if `uv run` misbehaves.

## File Structure

```
src/adversarial_ai_coding/config.py     # Task 1 (WorkflowAbort, ~6 lines)
src/adversarial_ai_coding/gitops.py     # Task 1
src/adversarial_ai_coding/gates.py      # Task 2
src/adversarial_ai_coding/workflow.py   # Task 3 (WorkflowContext, work, check_protected)
src/adversarial_ai_coding/review.py     # Task 4
tests/test_gitops.py                    # Task 1
tests/test_gates.py                     # Task 2
tests/test_work.py                      # Task 3
tests/test_review.py                    # Task 4
```

## Bash-Function Mapping (this plan's parity ledger)

| bash | Python |
|---|---|
| `protected_violations` :789 | `gitops.protected_violations` |
| `ensure_committed` :1449 | `gitops.ensure_committed` |
| `verify_last_head` :1750 | `gitops.verify_last_head` (raises `RunStateError`) |
| `resume_workspace` :1772 | `gitops.resume_workspace` |
| `setup_workspace` :1793 | `gitops.setup_workspace` |
| `detect_gate` :771 / `detect_build_gate` :781 | `gates.detect_gate` / `gates.detect_build_gate` |
| `gate_loop` :1356 | `gates.gate_loop` |
| `work` :1171 | `workflow.work` |
| `check_protected` :1199 / `CHECKING_PROTECTED` :1197 | `workflow.check_protected` (flag on `WorkflowContext`) |
| `verdict_approved` :698 | `review.verdict_approved` |
| `review_prompt` :1226 / `verdict_file_instr` :1230 / `compose_review_prompt` :1234 | same names in `review` |
| `collect_suggestions` :1295 / `show_blockers` :1307 | same names in `review` |
| `run_review` :1313 | `review.run_review` |
| `review_loop` :1420 | `review.review_loop` |
| `exit 1` human stops / `exit 75` quota | `config.WorkflowAbort(msg, rc)` |

Deliberate divergences (document in code, pin with tests):
- `gate_loop` runs the gate command with `subprocess.run(cmd, shell=True)`.
  Bash used `bash -c "$cmd"`; on Windows the platform shell is cmd.exe.
  Detected gate commands only chain with `&&`, which cmd.exe supports.
- `setup_workspace` returns the workspace path instead of `cd`-ing; the
  caller (cli, plan 5) changes directory. Same observable layout.

---

### Task 1: `gitops.py` — git helpers, workspace lifecycle, protected files

Bash reference: `adversarial-ai-coding.sh:789-792` (protected_violations),
`:1449-1456` (ensure_committed), `:1750-1810` (verify_last_head,
resume_workspace, setup_workspace).
Bash tests ported: `tests/helpers.test.sh:274-294` (protected),
`:534-538` (worktree), `:858-898` (last-head, resume workspace).

**Files:**
- Modify: `src/adversarial_ai_coding/config.py` (add WorkflowAbort)
- Create: `src/adversarial_ai_coding/gitops.py`
- Test: `tests/test_gitops.py`

**Interfaces:**
- Consumes: `runstate.RunState`, `runstate.RunStateError`.
- Produces:
  - `config.WorkflowAbort(Exception)` with `rc: int = 1` attribute — the
    typed exit for every workflow stop; cli (plan 5) maps it to the exit
    code. Constructor: `WorkflowAbort(message: str, rc: int = 1)`.
  - `gitops.git_out(args: list[str], cwd: Path) -> str` — stdout of a git
    command, raising `CalledProcessError` on failure (internal helper, but
    exported for plan 5's small call sites like `git rev-parse HEAD`).
  - `gitops.is_inside_work_tree(cwd: Path) -> bool`
  - `gitops.current_branch(cwd: Path) -> str` — `--abbrev-ref HEAD`.
  - `gitops.head_sha(cwd: Path) -> str`
  - `gitops.status_porcelain(cwd: Path) -> str`
  - `gitops.protected_violations(protected_file: Path, base: str, cwd: Path) -> list[str]`
    — empty when the list file is missing/empty; otherwise the exact-name
    intersection of `git diff --name-only <base> --` with the list.
  - `gitops.ensure_committed(cwd: Path, stage: str, echo_err: Callable[[str], None]) -> None`
    — fallback `git add -A` + two-paragraph commit when the tree is dirty.
  - `gitops.verify_last_head(state: RunState, cwd: Path, echo_err) -> None`
    — C6-light; raises `RunStateError` on damaged state or unreachable
    checkpoint; warns (echo_err) when HEAD moved past the checkpoint.
  - `gitops.resume_workspace(resumed_branch: str, state: RunState, cwd: Path, echo_err) -> None`
    — switches back to the recorded branch (missing branch raises
    `RunStateError` with the worktree hint), then `verify_last_head`, then
    warns about a dirty tree.
  - `gitops.setup_workspace(settings: Settings, run_id: str, cwd: Path) -> Path`
    — worktree mode creates `../<name>-auto-<run_id>` on branch
    `auto/<run_id>` and returns the worktree path; branch mode
    `git switch -c auto/<run_id>` returns `cwd`; otherwise returns `cwd`.
    (Resume never reaches this function; cli routes to `resume_workspace`.)

- [ ] **Step 1: Write the failing tests**

`tests/test_gitops.py`:

```python
"""Ports helpers.test.sh:274-294, 534-538, 858-898 (git ops)."""

import subprocess

import pytest

from adversarial_ai_coding.config import Settings, WorkflowAbort
from adversarial_ai_coding.gitops import (
    current_branch,
    ensure_committed,
    head_sha,
    protected_violations,
    resume_workspace,
    setup_workspace,
    status_porcelain,
    verify_last_head,
)
from adversarial_ai_coding.runstate import RunState, RunStateError


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def claimed(repo):
    return RunState.create(repo / ".workflow" / "state", "r", "task\n")


def test_workflow_abort_carries_rc():
    err = WorkflowAbort("stop", rc=75)
    assert err.rc == 75
    assert WorkflowAbort("stop").rc == 1


def test_protected_violations_lifecycle(new_repo):
    (new_repo / "acc_test.go").write_text("func TestAcc\n", encoding="utf-8")
    git(new_repo, "add", "-A")
    git(new_repo, "commit", "-qm", "tests")
    base = head_sha(new_repo)
    protected = new_repo / "protected.txt"
    protected.write_text("acc_test.go\n", encoding="utf-8")

    assert protected_violations(protected, base, new_repo) == []
    (new_repo / "acc_test.go").write_text("weakened\n", encoding="utf-8")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    git(new_repo, "add", "-A")
    git(new_repo, "commit", "-qm", "hack")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    protected.write_text("", encoding="utf-8")
    assert protected_violations(protected, base, new_repo) == []
    assert protected_violations(new_repo / "absent.txt", base, new_repo) == []


def test_ensure_committed_fallback_commit(new_repo):
    (new_repo / "base.txt").write_text("left dirty\n", encoding="utf-8")
    warnings = []
    ensure_committed(new_repo, "write-code", warnings.append)
    assert status_porcelain(new_repo) == ""
    assert "fallback commit" in warnings[0]
    subject = git(new_repo, "log", "-1", "--format=%s")
    assert subject == "chore: commit remaining write-code changes"
    ensure_committed(new_repo, "write-code", warnings.append)  # clean: no-op
    assert len(warnings) == 1


def test_verify_last_head_ancestor_warns(new_repo):
    st = claimed(new_repo)
    first = head_sha(new_repo)
    st.record_stage("s1", first)
    git(new_repo, "commit", "--allow-empty", "-qm", "second")
    warnings = []
    verify_last_head(st, new_repo, warnings.append)  # must not raise
    assert any("new commits" in w for w in warnings)


def test_verify_last_head_unreachable_fails_closed(new_repo):
    st = claimed(new_repo)
    st.record_stage("s1", "0123456789abcdef0123456789abcdef01234567")
    with pytest.raises(RunStateError, match="not reachable"):
        verify_last_head(st, new_repo, lambda _msg: None)


def test_verify_last_head_ledger_without_checkpoint_fails(new_repo):
    st = claimed(new_repo)
    # Simulate damage: a recorded stage but no last-head file.
    st.record_stage("s1", head_sha(new_repo))
    (st.state_dir / "last-head").unlink()
    with pytest.raises(RunStateError, match="no last-head checkpoint"):
        verify_last_head(st, new_repo, lambda _msg: None)


def test_verify_last_head_fresh_state_passes(new_repo):
    st = claimed(new_repo)
    verify_last_head(st, new_repo, lambda _msg: None)  # no ledger: fine


def test_resume_workspace_switches_to_recorded_branch(new_repo):
    git(new_repo, "branch", "auto-r")
    st = claimed(new_repo)
    resume_workspace("auto-r", st, new_repo, lambda _msg: None)
    assert current_branch(new_repo) == "auto-r"


def test_resume_workspace_missing_branch_fails(new_repo):
    st = claimed(new_repo)
    with pytest.raises(RunStateError, match="no longer exists"):
        resume_workspace("gone-branch", st, new_repo, lambda _msg: None)


def test_resume_workspace_dirty_tree_warns(new_repo):
    st = claimed(new_repo)
    (new_repo / "base.txt").write_text("dirty change\n", encoding="utf-8")
    warnings = []
    resume_workspace(current_branch(new_repo), st, new_repo, warnings.append)
    assert any("absorbed into the next automatic commit" in w for w in warnings)


def test_resume_workspace_no_recorded_branch_warns_and_stays(new_repo):
    st = claimed(new_repo)
    warnings = []
    resume_workspace("", st, new_repo, warnings.append)
    assert any("no branch record" in w for w in warnings)


def test_setup_workspace_branch_mode(new_repo):
    settings = Settings.from_env({}, run_id="20260711-010101")
    workspace = setup_workspace(settings, "20260711-010101", new_repo)
    assert workspace == new_repo
    assert current_branch(new_repo) == "auto/20260711-010101"


def test_setup_workspace_worktree_mode(new_repo):
    # helpers.test.sh: "worktree:creates and switches to auto/* branch"
    settings = Settings.from_env({"USE_WORKTREE": "1"}, run_id="wt1")
    workspace = setup_workspace(settings, "wt1", new_repo)
    assert workspace != new_repo
    assert workspace.name == f"{new_repo.name}-auto-wt1"
    assert current_branch(workspace) == "auto/wt1"
    git(new_repo, "worktree", "prune")


def test_setup_workspace_no_branch_mode(new_repo):
    settings = Settings.from_env({"AUTO_BRANCH": "0"}, run_id="r")
    assert setup_workspace(settings, "r", new_repo) == new_repo
    assert current_branch(new_repo) == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gitops.py -q`
Expected: FAIL — no module `gitops`, no `WorkflowAbort`.

- [ ] **Step 3: Add `WorkflowAbort` to `config.py`**

```python
class WorkflowAbort(Exception):
    """Typed workflow stop; cli maps rc to the process exit code.

    rc=1 mirrors bash's human-intervention exits; rc=QUOTA_ABORT_RC (75)
    mirrors resumable quota aborts. Lives here (the common leaf) so gates,
    review, and workflow can raise it without import cycles.
    """

    def __init__(self, message: str, rc: int = 1):
        super().__init__(message)
        self.rc = rc
```

- [ ] **Step 4: Write `src/adversarial_ai_coding/gitops.py`**

```python
"""Git operations: workspace lifecycle, protected files, fallback commits.

Port of adversarial-ai-coding.sh:789-792, 1449-1456, and 1750-1810.
Everything takes an explicit cwd; nothing changes the process directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from .config import Settings
from .runstate import RunState, RunStateError


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def git_out(args: list[str], cwd: Path) -> str:
    proc = _git(args, cwd)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, ["git", *args],
                                            proc.stdout, proc.stderr)
    return proc.stdout.strip()


def is_inside_work_tree(cwd: Path) -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"], cwd).returncode == 0


def current_branch(cwd: Path) -> str:
    return git_out(["rev-parse", "--abbrev-ref", "HEAD"], cwd)


def head_sha(cwd: Path) -> str:
    return git_out(["rev-parse", "HEAD"], cwd)


def status_porcelain(cwd: Path) -> str:
    return git_out(["status", "--porcelain"], cwd)


def protected_violations(protected_file: Path, base: str, cwd: Path) -> list[str]:
    # Empty/missing list disables protection (sh:790).
    if not protected_file.is_file() or protected_file.stat().st_size == 0:
        return []
    protected = {line for line in
                 protected_file.read_text(encoding="utf-8").splitlines() if line}
    diff = _git(["diff", "--name-only", base, "--"], cwd)
    if diff.returncode != 0:
        return []
    changed = [line for line in diff.stdout.splitlines() if line]
    return [name for name in changed if name in protected]


def ensure_committed(cwd: Path, stage: str, echo_err: Callable[[str], None]) -> None:
    if not status_porcelain(cwd):
        return
    echo_err("(worker left uncommitted changes; script is creating a fallback commit)")
    git_out(["add", "-A"], cwd)
    git_out(["commit", "-m", f"chore: commit remaining {stage} changes",
             "-m", "Auto-committed by adversarial-ai-coding because the worker "
                   "left uncommitted changes."], cwd)


def verify_last_head(state: RunState, cwd: Path,
                     echo_err: Callable[[str], None]) -> None:
    recorded = state.read_last_head()
    if recorded is None:
        if state.completed_stages():
            raise RunStateError(
                f"!! Run {state.run_id} has completed stages but no last-head "
                "checkpoint; the state is damaged.\n   Start a fresh run, or "
                f"delete {state.state_dir} if you no longer need it."
            )
        return
    head = head_sha(cwd)
    if head == recorded:
        return
    if _git(["merge-base", "--is-ancestor", recorded, "HEAD"], cwd).returncode == 0:
        echo_err(f"(warning: new commits exist after the resume checkpoint "
                 f"{recorded}; continuing)")
        return
    raise RunStateError(
        f"!! The resume checkpoint {recorded} is not reachable from HEAD "
        "(branch reset/rebase, or the wrong repository).\n"
        f"   Fix the branch first, or delete {state.state_dir / 'last-head'} "
        "to force the resume."
    )


def resume_workspace(resumed_branch: str, state: RunState, cwd: Path,
                     echo_err: Callable[[str], None]) -> None:
    current = current_branch(cwd)
    if not resumed_branch:
        echo_err(f"(warning: the resume snapshot has no branch record; "
                 f"staying on {current})")
    elif current != resumed_branch:
        exists = _git(["show-ref", "--verify", "--quiet",
                       f"refs/heads/{resumed_branch}"], cwd).returncode == 0
        if not exists:
            raise RunStateError(
                f"!! The resumed run's branch {resumed_branch} no longer exists "
                "in this repository.\n   If the run used USE_WORKTREE=1, cd "
                "into its worktree and resume there."
            )
        git_out(["switch", resumed_branch], cwd)
    verify_last_head(state, cwd, echo_err)
    if status_porcelain(cwd):
        echo_err("!! The working tree is dirty. These changes will be absorbed "
                 "into the next automatic commit (git add -A):")
        echo_err(git_out(["status", "--short"], cwd))


def setup_workspace(settings: Settings, run_id: str, cwd: Path) -> Path:
    if settings.use_worktree:
        root = Path(git_out(["rev-parse", "--show-toplevel"], cwd))
        worktree = root.parent / f"{root.name}-auto-{run_id}"
        git_out(["worktree", "add", "-b", f"auto/{run_id}", str(worktree)], cwd)
        return worktree
    if settings.auto_branch:
        git_out(["switch", "-c", f"auto/{run_id}"], cwd)
    return cwd
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_gitops.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/config.py src/adversarial_ai_coding/gitops.py tests/test_gitops.py
git commit -m "feat: port git ops and workspace lifecycle

Port protected-file violation detection, the fallback commit for
uncommitted worker changes, last-head checkpoint verification that
fails closed on unreachable checkpoints (C6 light), resume workspace
branch switching with dirty-tree warnings, and workspace setup for
worktree/branch/current modes. setup_workspace returns the workspace
path instead of chdir-ing; the cli owns the directory change. Adds the
shared WorkflowAbort typed-exit exception to config."
```

---

### Task 2: `gates.py` — gate detection and the deterministic gate loop

Bash reference: `adversarial-ai-coding.sh:771-787` (detect),
`:1356-1380` (gate_loop).
Bash tests ported: `tests/helpers.test.sh:40-61` (detect_gate).

**Files:**
- Create: `src/adversarial_ai_coding/gates.py`
- Test: `tests/test_gates.py`

**Interfaces:**
- Consumes: `config.WorkflowAbort`, `prompts.render_prompt`.
- Produces:
  - `gates.detect_gate(cwd: Path) -> str` — go/npm-with-test-script/cargo
    detection, `""` when unknown.
  - `gates.detect_build_gate(cwd: Path) -> str` — go/cargo build-only.
  - `gates.run_shell(cmd: str, cwd: Path) -> tuple[int, str]` — merged
    output via `subprocess.run(cmd, shell=True, ...)`; the default
    executor, injectable in tests and by gate_loop's caller.
  - `gates.gate_loop(cmd: str, *, cwd: Path, prompts_dir: Path, max_rounds: int, do_work: Callable[[str], None], log: Callable[[str], None], notify: Callable[[str], None], stage: str, run_shell=run_shell) -> None`
    — empty cmd returns immediately; failure output (tail 150 lines) is
    rendered into the `quality-gate-failed` prompt and sent to `do_work`;
    after `max_rounds` failures raises `WorkflowAbort` (rc 1) with the tail
    50 lines, after calling `notify`.

- [ ] **Step 1: Write the failing tests**

`tests/test_gates.py`:

```python
"""Ports helpers.test.sh:40-61 (detect_gate) and adds gate_loop unit tests."""

import json

import pytest

from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gates import detect_build_gate, detect_gate, gate_loop
from adversarial_ai_coding.prompts import default_prompts_dir

PROMPTS = default_prompts_dir({})


def test_detect_gate_go_project(tmp_path):
    (tmp_path / "go.mod").touch()
    assert detect_gate(tmp_path) == "go build ./... && go vet ./... && go test ./..."
    assert detect_build_gate(tmp_path) == "go build ./..."


def test_detect_gate_npm_with_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8")
    assert detect_gate(tmp_path) == "npm test"


def test_detect_gate_npm_without_test_script(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {}}), encoding="utf-8")
    assert detect_gate(tmp_path) == ""
    (tmp_path / "package.json").write_text("broken json", encoding="utf-8")
    assert detect_gate(tmp_path) == ""


def test_detect_gate_cargo_and_unknown(tmp_path):
    (tmp_path / "Cargo.toml").touch()
    assert detect_gate(tmp_path) == "cargo test"
    assert detect_build_gate(tmp_path) == "cargo build"
    for f in tmp_path.iterdir():
        f.unlink()
    assert detect_gate(tmp_path) == ""
    assert detect_build_gate(tmp_path) == ""


def run_gate(tmp_path, results, max_rounds=3):
    """results: list of (rc, output) returned per shell invocation."""
    calls = {"shell": 0, "work": []}

    def fake_shell(cmd, cwd):
        rc, out = results[calls["shell"]]
        calls["shell"] += 1
        return rc, out

    def fake_work(prompt):
        calls["work"].append(prompt)

    gate_loop("make check", cwd=tmp_path, prompts_dir=PROMPTS,
              max_rounds=max_rounds, do_work=fake_work,
              log=lambda _m: None, notify=lambda _m: None,
              stage="write-code", run_shell=fake_shell)
    return calls


def test_gate_loop_empty_cmd_skips(tmp_path):
    gate_loop("", cwd=tmp_path, prompts_dir=PROMPTS, max_rounds=3,
              do_work=lambda p: pytest.fail("must not be called"),
              log=lambda _m: None, notify=lambda _m: None, stage="s",
              run_shell=lambda c, w: pytest.fail("must not run"))


def test_gate_loop_pass_first_try(tmp_path):
    calls = run_gate(tmp_path, [(0, "all good")])
    assert calls["shell"] == 1
    assert calls["work"] == []


def test_gate_loop_failure_repair_then_pass(tmp_path):
    calls = run_gate(tmp_path, [(1, "FAIL: acc_test"), (0, "ok")])
    assert calls["shell"] == 2
    assert len(calls["work"]) == 1
    assert "make check" in calls["work"][0]      # COMMAND placeholder
    assert "FAIL: acc_test" in calls["work"][0]  # OUTPUT placeholder


def test_gate_loop_max_rounds_aborts(tmp_path):
    with pytest.raises(WorkflowAbort) as exc:
        run_gate(tmp_path, [(1, "boom")] * 3, max_rounds=3)
    assert exc.value.rc == 1
    assert "Quality gate failed" in str(exc.value)


def test_gate_loop_output_tail_truncated(tmp_path):
    long_out = "\n".join(f"line{i}" for i in range(400))
    calls = run_gate(tmp_path, [(1, long_out), (0, "ok")])
    prompt = calls["work"][0]
    assert "line399" in prompt      # tail is kept
    assert "line100" not in prompt  # only the last 150 lines are sent
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gates.py -q`
Expected: FAIL — no module `gates`.

- [ ] **Step 3: Write `src/adversarial_ai_coding/gates.py`**

```python
"""Deterministic quality gates (sh:771-787, 1356-1380).

Anything machine-verifiable is run by the script; AI claims about test
status are only hints.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable

from .config import WorkflowAbort
from .prompts import render_prompt


def detect_gate(cwd: Path) -> str:
    if (cwd / "go.mod").is_file():
        return "go build ./... && go vet ./... && go test ./..."
    package = cwd / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
        except (json.JSONDecodeError, UnicodeDecodeError):
            scripts = {}
        if scripts.get("test"):
            return "npm test"
        return ""
    if (cwd / "Cargo.toml").is_file():
        return "cargo test"
    return ""


def detect_build_gate(cwd: Path) -> str:
    if (cwd / "go.mod").is_file():
        return "go build ./..."
    if (cwd / "Cargo.toml").is_file():
        return "cargo build"
    return ""


def run_shell(cmd: str, cwd: Path) -> tuple[int, str]:
    # Divergence: bash ran `bash -c "$cmd"`; shell=True uses the platform
    # shell (cmd.exe on Windows). Detected gates only chain with &&.
    proc = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.splitlines()[-lines:])


def gate_loop(
    cmd: str,
    *,
    cwd: Path,
    prompts_dir: Path,
    max_rounds: int,
    do_work: Callable[[str], None],
    log: Callable[[str], None],
    notify: Callable[[str], None],
    stage: str,
    run_shell: Callable[[str, Path], tuple[int, str]] = run_shell,
) -> None:
    if not cmd:
        return
    n = 1
    while True:
        log(f">>> Quality gate:{cmd}")
        rc, out = run_shell(cmd, cwd)
        if rc == 0:
            log("Quality gate passed")
            return
        log(f"Quality gate failed (attempt {n})")
        if n >= max_rounds:
            notify(f"adversarial-ai-coding:[{stage}] quality gate failed "
                   "repeatedly; human intervention required")
            raise WorkflowAbort(
                f"!! [{stage}] Quality gate failed {max_rounds} times; stopping "
                f"for human intervention. Output:\n{_tail(out, 50)}"
            )
        n += 1
        prompt = render_prompt(prompts_dir, "quality-gate-failed",
                               {"COMMAND": cmd, "OUTPUT": _tail(out, 150)})
        do_work(prompt)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gates.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/gates.py tests/test_gates.py
git commit -m "feat: port quality gate detection and repair loop

Port detect_gate/detect_build_gate (go, npm-with-test-script, cargo)
and gate_loop: failures feed the tail of the output into the
quality-gate-failed prompt for a worker repair round, and repeated
failures raise WorkflowAbort for human intervention. The shell executor
is injectable; the default uses the platform shell, a documented
divergence from bash -c."
```

---

### Task 3: `workflow.py` — WorkflowContext, `work()`, `check_protected()`

Bash reference: `adversarial-ai-coding.sh:1171-1220` (work,
check_protected), globals `:1036-1038`, `:1382-1384`, `:1197`.
Bash tests ported: `tests/helpers.test.sh:191-218` (work archives the full
prompt and sends the file reference).

**Files:**
- Create: `src/adversarial_ai_coding/workflow.py`
- Test: `tests/test_work.py`

**Interfaces:**
- Consumes: everything from plans 1-3 and Tasks 1-2.
- Produces:
  - `@dataclass workflow.WorkflowContext:` — the shared context (spec
    section "Workflow"); plan 5 extends the same file with the stage flow.
    Fields (binding for plan 5):
    ```python
    settings: Settings
    archive: RunArchive
    state: RunState | None
    session: EngineSession
    workspace: Path                 # git cwd
    wf: Path                        # .workflow
    prompts_dir: Path
    spec_dir: Path
    cur_stage: str = "startup"
    cur_round: int = 1
    collect_review_suggestions: bool = True
    checking_protected: bool = False
    gate_cmd: str = ""
    build_gate_cmd: str = ""
    echo: Callable[[str], None] = print
    echo_err: Callable[[str], None] = _print_err   # module helper, stderr
    ```
    Properties: `engine_out = wf / "last-engine-output.txt"`,
    `verdict_path = wf / "verdict.json"`, `review_path = wf / "review.md"`,
    `suggestions_path = wf / "suggestions.md"`.
    Methods:
    - `log(text: str) -> None` — append to `archive.log_path` AND `echo`
      (the bash `tee -a "$LOG"`).
    - `engine_io() -> EngineIO` — engine_out/verdict_path/echo bundle.
    - `notify(message: str) -> None` — delegates to `engines.notify`.
  - `workflow.work(ctx: WorkflowContext, engine: str, instruction: str) -> None`
    — the full bash `work()` sequence: log_section, worker banner, prompt
    archived as `<slug>-prompt.md`, `engine_call` around
    `engines.run_worker` with the short prompt-file instruction, output
    tee'd to log + `<slug>-output.txt` artifact + console, ENGINE_OUT
    snapshot, git state archive, metric with duration and
    `session.last_cost`, then `check_protected` unless
    `ctx.checking_protected`. A quota abort from engine_call raises
    `WorkflowAbort(msg, rc=QUOTA_ABORT_RC)`; other engine failures follow
    bash (warning only — bash's `work` does not exit on engine rc != 0
    because engine_call's non-retry failures return the rc and the
    pipeline's `> >(tee ...)` masks it; preserve that no-raise behavior and
    document it).
  - `workflow.check_protected(ctx: WorkflowContext, engine: str) -> None`
    — loops while violations exist: render `protected-tests-modified`
    prompt, recursive `work` with `checking_protected` set, at most 2
    recovery rounds then `WorkflowAbort` (rc 1) + notify.

- [ ] **Step 1: Add the shared context factory to `tests/conftest.py`**

Both this task's tests and plan 5's reuse it, so it lives in conftest as a
fixture (test modules must not import each other):

```python
@pytest.fixture
def make_ctx(new_repo):
    """WorkflowContext over a throwaway repo with silenced console sinks."""
    from adversarial_ai_coding.archive import establish_run_archive
    from adversarial_ai_coding.config import Settings
    from adversarial_ai_coding.engines import EngineSession
    from adversarial_ai_coding.prompts import default_prompts_dir
    from adversarial_ai_coding.workflow import WorkflowContext

    def _make(env=None):
        settings = Settings.from_env(env or {"RETRY_ON_LIMIT": "0"}, run_id="test")
        wf = new_repo / ".workflow"
        wf.mkdir(exist_ok=True)
        archive = establish_run_archive(wf / "runs", "test", settings)
        return WorkflowContext(
            settings=settings, archive=archive, state=None,
            session=EngineSession(), workspace=new_repo, wf=wf,
            prompts_dir=default_prompts_dir({}), spec_dir=new_repo / "specs",
            cur_stage="stage", echo=lambda _l: None, echo_err=lambda _l: None,
        )

    return _make
```

- [ ] **Step 2: Write the failing tests**

`tests/test_work.py`:

```python
"""Ports helpers.test.sh:191-218 (work archives prompt, sends file reference)
and check_protected recovery/exhaustion behavior."""

import pytest

from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.engines import EngineResult
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC
from adversarial_ai_coding.workflow import check_protected, work


def test_work_archives_prompt_and_sends_file_reference(make_ctx, monkeypatch):
    ctx = make_ctx()
    seen = {}

    def fake_worker(name, prompt, settings, session, io):
        seen["prompt"] = prompt
        io.engine_out.write_text("ok\n", encoding="utf-8")
        return EngineResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    work(ctx, "claude", "FULL_PROMPT_SENTINEL for worker")

    # The engine got the short file-reference instruction, not the full text.
    assert "Read the full workflow prompt" in seen["prompt"]
    assert "worker-stage-r1-prompt.md" in seen["prompt"]
    assert "FULL_PROMPT_SENTINEL" not in seen["prompt"]
    # The full prompt was archived.
    arts = list(ctx.archive.run_dir.glob("*-worker-stage-r1-prompt.md"))
    assert arts and "FULL_PROMPT_SENTINEL for worker" in arts[0].read_text(encoding="utf-8")
    # Worker output artifact and metrics row exist.
    outs = list(ctx.archive.run_dir.glob("*-worker-stage-r1-output.txt"))
    assert outs and "worker output" in outs[0].read_text(encoding="utf-8")
    assert ctx.archive.metrics_path.is_file()


def test_work_quota_abort_raises_resumable(make_ctx, monkeypatch):
    ctx = make_ctx()

    def limited_worker(name, prompt, settings, session, io):
        io.engine_out.write_text("You've hit your usage limit\n", encoding="utf-8")
        return EngineResult(1, "limited")

    monkeypatch.setattr(wf_mod, "run_worker", limited_worker)
    with pytest.raises(WorkflowAbort) as exc:
        work(ctx, "claude", "prompt")
    assert exc.value.rc == QUOTA_ABORT_RC


def test_work_ordinary_engine_failure_does_not_raise(make_ctx, monkeypatch):
    ctx = make_ctx()

    def failing_worker(name, prompt, settings, session, io):
        io.engine_out.write_text("undefined: IsPalindrome\n", encoding="utf-8")
        return EngineResult(1, "build error")

    monkeypatch.setattr(wf_mod, "run_worker", failing_worker)
    work(ctx, "claude", "prompt")  # bash parity: no exit on ordinary failure


def test_check_protected_repairs_then_stops(make_ctx, monkeypatch):
    ctx = make_ctx()
    (ctx.wf / "protected-tests.txt").write_text("acc_test.go\n", encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text("basesha\n", encoding="utf-8")
    monkeypatch.setattr(wf_mod, "protected_violations",
                        lambda f, base, cwd: ["acc_test.go"])
    repair_prompts = []
    monkeypatch.setattr(wf_mod, "work",
                        lambda c, e, p: repair_prompts.append(p))
    with pytest.raises(WorkflowAbort, match="human intervention"):
        check_protected(ctx, "claude")
    assert len(repair_prompts) == 2  # two recovery rounds, then stop


def test_check_protected_noop_without_protection_files(make_ctx):
    ctx = make_ctx()
    check_protected(ctx, "claude")  # no files: returns silently
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_work.py -q`
Expected: FAIL — no module `workflow`.

- [ ] **Step 4: Write `src/adversarial_ai_coding/workflow.py`**

```python
"""Workflow context and the worker-call wrapper.

Port of adversarial-ai-coding.sh:1171-1220 (work, check_protected) plus the
shared mutable context replacing bash's globals (WORKER_SESSION, CUR_STAGE,
CUR_ROUND, CHECKING_PROTECTED, ...). Plan 5 adds the stage flow here.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .archive import RunArchive, safe_slug
from .config import Settings, WorkflowAbort
from .engines import EngineIO, EngineSession, notify as engines_notify, run_worker
from .gitops import protected_violations
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, RetryEvents, engine_call
from .runstate import RunState


def _print_err(text: str) -> None:
    print(text, file=sys.stderr)


@dataclass
class WorkflowContext:
    settings: Settings
    archive: RunArchive
    state: RunState | None
    session: EngineSession
    workspace: Path
    wf: Path
    prompts_dir: Path
    spec_dir: Path
    cur_stage: str = "startup"
    cur_round: int = 1
    collect_review_suggestions: bool = True
    checking_protected: bool = False
    gate_cmd: str = ""
    build_gate_cmd: str = ""
    echo: Callable[[str], None] = print
    echo_err: Callable[[str], None] = _print_err

    @property
    def engine_out(self) -> Path:
        return self.wf / "last-engine-output.txt"

    @property
    def verdict_path(self) -> Path:
        return self.wf / "verdict.json"

    @property
    def review_path(self) -> Path:
        return self.wf / "review.md"

    @property
    def suggestions_path(self) -> Path:
        return self.wf / "suggestions.md"

    def log(self, text: str) -> None:
        # bash `tee -a "$LOG"`: console and log file together.
        self.log_file(text)
        self.echo(text)

    def log_file(self, text: str) -> None:
        # Log file only — for engine output that already streamed to the
        # console via EngineIO.echo (avoids double-printing).
        self.archive.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.archive.log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")

    def engine_io(self) -> EngineIO:
        return EngineIO(engine_out=self.engine_out,
                        verdict_path=self.verdict_path, echo=self.echo)

    def notify(self, message: str) -> None:
        engines_notify(self.settings, message)


def _retry_events(ctx: WorkflowContext, role: str, engine: str,
                  slug: str) -> RetryEvents:
    return RetryEvents(
        archive_attempt=lambda attempt, rc: ctx.archive.archive_engine_attempt(
            role, engine, slug, attempt, rc, ctx.engine_out,
            stage=ctx.cur_stage, round=ctx.cur_round),
        log_retry=ctx.log,
        notify=ctx.notify,
        sleep=time.sleep,
    )


def work(ctx: WorkflowContext, engine: str, instruction: str) -> None:
    t0 = time.monotonic()
    ctx.session.last_cost = ""
    ctx.archive.log_section("AI call", "worker", engine, ctx.cur_stage,
                            ctx.cur_round, echo=ctx.echo)
    ctx.echo(f">>> Worker({engine}) is running...")
    slug = f"worker-{safe_slug(ctx.cur_stage or 'startup')}-r{ctx.cur_round}"
    prompt_art = ctx.archive.archive_text(f"{slug}-prompt.md", instruction,
                                          "worker", engine, ctx.cur_stage,
                                          ctx.cur_round)
    short_prompt = prompt_file_instruction(str(prompt_art))
    io = ctx.engine_io()
    result = engine_call(
        lambda: run_worker(engine, short_prompt, ctx.settings, ctx.session, io),
        engine_out=ctx.engine_out, settings=ctx.settings,
        events=_retry_events(ctx, "worker", engine, slug),
    )
    output_art = ctx.archive.art_path(f"{slug}-output.txt")
    output_art.write_text(result.text.rstrip("\n") + "\n", encoding="utf-8")
    ctx.archive.write_meta(output_art, "worker", engine, ctx.cur_stage, ctx.cur_round)
    # Streamed engines already echoed live via EngineIO; log file only here.
    ctx.log_file(result.text)
    if result.rc == QUOTA_ABORT_RC:
        raise WorkflowAbort(
            "!! Worker gave up on a quota/rate limit; aborting the run as resumable.",
            rc=QUOTA_ABORT_RC,
        )
    # bash parity: an ordinary engine failure logs upstream and continues;
    # the review/gate loops are the correctness net, not the exit code here.
    ctx.archive.archive_snapshot(ctx.engine_out, f"{slug}-final.raw", "worker",
                                 engine, ctx.cur_stage, ctx.cur_round)
    ctx.archive.archive_git_state("worker", engine, slug, ctx.cur_stage,
                                  ctx.cur_round, cwd=ctx.workspace)
    ctx.archive.metric("worker", engine, ctx.cur_round,
                       int(time.monotonic() - t0), ctx.session.last_cost,
                       stage=ctx.cur_stage)
    if not ctx.checking_protected:
        check_protected(ctx, engine)


def check_protected(ctx: WorkflowContext, engine: str) -> None:
    protected_file = ctx.wf / "protected-tests.txt"
    base_file = ctx.wf / "protected-base.sha"
    if not (protected_file.is_file() and base_file.is_file()):
        return
    ctx.archive.log_section("protected check", "workflow", "workflow",
                            ctx.cur_stage, ctx.cur_round, echo=ctx.echo)
    base = base_file.read_text(encoding="utf-8").strip()
    n = 0
    while True:
        violations = protected_violations(protected_file, base, ctx.workspace)
        if not violations:
            return
        listing = "\n".join(f"  - {v}" for v in violations)
        ctx.log(f"!! Protected acceptance test files were modified:\n{listing}")
        if n >= 2:
            ctx.notify(f"adversarial-ai-coding:[{ctx.cur_stage}] protected tests "
                       "were modified and not restored; human intervention required")
            raise WorkflowAbort(
                "!! Worker repeatedly modified protected tests and did not "
                "restore them; stopping for human intervention."
            )
        n += 1
        prompt = render_prompt(ctx.prompts_dir, "protected-tests-modified", {
            "VIOLATIONS": "\n".join(violations),
            "BASE": base,
            "SPEC_FILE": str(ctx.spec_dir / "spec.md"),
        })
        ctx.checking_protected = True
        try:
            work(ctx, engine, prompt)
        finally:
            ctx.checking_protected = False
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_work.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 6: Commit**

```bash
git add src/adversarial_ai_coding/workflow.py tests/conftest.py tests/test_work.py
git commit -m "feat: port worker call wrapper and protected-test recovery

Add WorkflowContext, the explicit replacement for bash's process
globals, with log/notify/engine-io helpers. work() archives the full
prompt, hands the engine only the short file reference, tees output to
log and artifact, snapshots ENGINE_OUT and git state, records metrics,
and maps quota give-ups to a resumable WorkflowAbort while keeping
bash's no-exit behavior for ordinary engine failures. check_protected
forces recovery rounds when protected acceptance tests are touched and
stops for human intervention after two failed recoveries."
```

---

### Task 4: `review.py` — verdicts, review prompts, run_review, review_loop

Bash reference: `adversarial-ai-coding.sh:698-700` (verdict_approved),
`:1226-1241` (prompt composition), `:1295-1352` (suggestions, blockers,
run_review), `:1420-1439` (review_loop).
Bash tests ported: `tests/helpers.test.sh:260-272` (verdict),
`:364-374` (compose_review_prompt), `:220-250` (run_review handoff),
`:1042-1049` (quota abort).

**Files:**
- Create: `src/adversarial_ai_coding/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: WorkflowContext, engines.run_reviewer, ratelimit.engine_call,
  prompts.render_prompt, gates.gate_loop, workflow.work.
- Produces:
  - `review.verdict_approved(path: Path) -> bool` — False on missing file
    or broken JSON (bash `jq -e` semantics).
  - `review.compose_review_prompt(engine: str, scope: str, prompts_dir: Path, wf: Path) -> str`
    — the `review` template plus, for every non-claude engine, the
    `verdict-file-instruction` template appended (claude gets the verdict
    via `--json-schema` instead).
  - `review.collect_suggestions(ctx) -> None` — appends this round's
    verdict suggestions to `suggestions.md` under a
    `## <stage>(round <n>)` heading.
  - `review.show_blockers(ctx) -> None` — logs "Review did not pass;
    blockers:" with the bullet list.
  - `review.run_review(ctx, engine: str, scope: str) -> bool` — the full
    bash sequence including the pre-written failed verdict sentinel, quota
    abort (raises `WorkflowAbort` rc 75 with the reviewer wording), the
    review.md / verdict.json snapshots, suggestion collection honoring
    `ctx.collect_review_suggestions`, and the approved/blockers outcome.
  - `review.review_loop(ctx, reviewer: str, worker: str, scope: str, gate_cmd: str = "") -> None`
    — rounds until approved; after `settings.max_rounds` raises
    `WorkflowAbort` (rc 1) + notify; each repair round renders
    `review-findings-repair`, calls `workflow.work`, archives the review
    copy, and re-runs `gates.gate_loop` when a gate command is given.

- [ ] **Step 1: Write the failing tests**

`tests/test_review.py`:

```python
"""Ports helpers.test.sh:260-272, 364-374, 220-250, 1042-1049 (review layer)."""

import json

import pytest

from adversarial_ai_coding import review as review_mod
from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.engines import EngineResult
from adversarial_ai_coding.prompts import default_prompts_dir
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC
from adversarial_ai_coding.review import (
    compose_review_prompt,
    review_loop,
    run_review,
    verdict_approved,
)

PROMPTS = default_prompts_dir({})


def test_verdict_approved_cases(tmp_path):
    v = tmp_path / "v.json"
    v.write_text('{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8")
    assert verdict_approved(v)
    v.write_text('{"approved":false,"blockers":["x"],"suggestions":[]}', encoding="utf-8")
    assert not verdict_approved(v)
    assert not verdict_approved(tmp_path / "nothere.json")
    v.write_text("not json at all", encoding="utf-8")
    assert not verdict_approved(v)


def test_compose_review_prompt_verdict_instruction(tmp_path):
    # claude gets the verdict via --json-schema; others via the file instruction.
    claude = compose_review_prompt("claude", "scope", PROMPTS, tmp_path / ".workflow")
    codex = compose_review_prompt("codex", "scope", PROMPTS, tmp_path / ".workflow")
    custom = compose_review_prompt("custom-agent", "scope", PROMPTS, tmp_path / ".workflow")
    assert "Finally write the verdict" not in claude
    assert "Finally write the verdict" in codex
    assert "Finally write the verdict" in custom
    assert "scope" in claude


def approving_reviewer(verdict=None):
    def fake(name, prompt, settings, session, io):
        io.engine_out.write_text("reviewed\n", encoding="utf-8")
        payload = verdict or {"approved": True, "blockers": [], "suggestions": []}
        io.verdict_path.write_text(json.dumps(payload), encoding="utf-8")
        return EngineResult(0, "review text")
    return fake


def test_run_review_archives_and_approves(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage, ctx.cur_round = "review", 2
    seen = {}

    def fake_reviewer(name, prompt, settings, session, io):
        seen["prompt"] = prompt
        io.engine_out.write_text("reviewed\n", encoding="utf-8")
        io.verdict_path.write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}', encoding="utf-8")
        (ctx.wf / "review.md").write_text("approved\n", encoding="utf-8")
        return EngineResult(0, "review text")

    monkeypatch.setattr(review_mod, "run_reviewer", fake_reviewer)
    assert run_review(ctx, "codex", "FULL_PROMPT_SENTINEL review scope") is True
    # File-reference handoff, full prompt archived (helpers.test.sh:220-250).
    assert "Read the full workflow prompt" in seen["prompt"]
    assert "reviewer-review-r2-prompt.md" in seen["prompt"]
    assert "FULL_PROMPT_SENTINEL" not in seen["prompt"]
    arts = list(ctx.archive.run_dir.glob("*-reviewer-review-r2-prompt.md"))
    assert arts and "FULL_PROMPT_SENTINEL review scope" in arts[0].read_text(encoding="utf-8")


def test_run_review_prewrites_failed_sentinel(make_ctx, monkeypatch):
    ctx = make_ctx()

    def silent_reviewer(name, prompt, settings, session, io):
        io.engine_out.write_text("said nothing structured\n", encoding="utf-8")
        return EngineResult(0, "prose only")   # never writes verdict.json

    monkeypatch.setattr(review_mod, "run_reviewer", silent_reviewer)
    assert run_review(ctx, "codex", "scope") is False
    verdict = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is False
    assert verdict["blockers"] == ["reviewer did not write a verdict"]


def test_run_review_quota_abort(make_ctx, monkeypatch):
    # helpers.test.sh: "run_review:quota abort exits 75 instead of repair rounds"
    ctx = make_ctx()

    def limited_reviewer(name, prompt, settings, session, io):
        io.engine_out.write_text("You've hit your usage limit\n", encoding="utf-8")
        return EngineResult(1, "limited")

    monkeypatch.setattr(review_mod, "run_reviewer", limited_reviewer)
    with pytest.raises(WorkflowAbort) as exc:
        run_review(ctx, "codex", "scope")
    assert exc.value.rc == QUOTA_ABORT_RC


def test_run_review_collects_suggestions(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-spec"
    monkeypatch.setattr(review_mod, "run_reviewer", approving_reviewer(
        {"approved": True, "blockers": [], "suggestions": ["tighten naming"]}))
    run_review(ctx, "codex", "scope")
    text = ctx.suggestions_path.read_text(encoding="utf-8")
    assert "## write-spec(round 1)" in text
    assert "- tighten naming" in text
    # Candidate-spec reviews disable collection (bash COLLECT_REVIEW_SUGGESTIONS=0).
    ctx.collect_review_suggestions = False
    run_review(ctx, "codex", "scope")
    assert text == ctx.suggestions_path.read_text(encoding="utf-8")


def test_review_loop_repair_round_then_approval(make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.cur_stage = "write-code"
    outcomes = iter([False, True])
    monkeypatch.setattr(review_mod, "run_review",
                        lambda c, e, s: next(outcomes))
    repairs = []
    monkeypatch.setattr(review_mod, "work", lambda c, e, p: repairs.append(p))
    gates_run = []
    monkeypatch.setattr(review_mod, "gate_loop",
                        lambda cmd, **kw: gates_run.append(cmd))
    review_loop(ctx, "codex", "claude", "scope", gate_cmd="go test ./...")
    assert len(repairs) == 1
    assert "review.md" in repairs[0]        # review-findings-repair template
    assert gates_run == ["go test ./..."]   # repairs re-run the gate
    assert ctx.cur_round == 2


def test_review_loop_max_rounds_aborts(make_ctx, monkeypatch):
    ctx = make_ctx()
    monkeypatch.setattr(review_mod, "run_review", lambda c, e, s: False)
    monkeypatch.setattr(review_mod, "work", lambda c, e, p: None)
    with pytest.raises(WorkflowAbort, match="Review still failed"):
        review_loop(ctx, "codex", "claude", "scope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_review.py -q`
Expected: FAIL — no module `review`.

- [ ] **Step 3: Write `src/adversarial_ai_coding/review.py`**

```python
"""Reviewer machinery: verdicts, prompts, the review call, the review loop.

Port of adversarial-ai-coding.sh:698-700, 1226-1241, 1295-1352, 1420-1439.
Verdict grading: blockers must be fixed; suggestions do not block and are
evaluated at the end.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .archive import safe_slug
from .config import WorkflowAbort
from .engines import run_reviewer
from .gates import gate_loop
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, engine_call
from .workflow import WorkflowContext, _retry_events, work

FAILED_VERDICT = ('{"approved": false, "blockers": ["reviewer did not write a '
                  'verdict"], "suggestions": []}\n')


def verdict_approved(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return payload.get("approved") is True


def compose_review_prompt(engine: str, scope: str, prompts_dir: Path,
                          wf: Path) -> str:
    prompt = render_prompt(prompts_dir, "review", {"SCOPE": scope, "WF": str(wf)})
    if engine == "claude":
        return prompt
    instr = render_prompt(prompts_dir, "verdict-file-instruction", {"WF": str(wf)})
    return prompt + instr


def collect_suggestions(ctx: WorkflowContext) -> None:
    if not ctx.verdict_path.is_file():
        return
    try:
        payload = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    suggestions = [s for s in payload.get("suggestions") or [] if s]
    if not suggestions:
        return
    block = (f"## {ctx.cur_stage}(round {ctx.cur_round})\n"
             + "".join(f"- {s}\n" for s in suggestions) + "\n")
    with ctx.suggestions_path.open("a", encoding="utf-8") as f:
        f.write(block)


def show_blockers(ctx: WorkflowContext) -> None:
    if not ctx.verdict_path.is_file():
        return
    ctx.log("Review did not pass; blockers:")
    try:
        payload = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    for blocker in payload.get("blockers") or []:
        ctx.log(f"  - {blocker}")


def run_review(ctx: WorkflowContext, engine: str, scope: str) -> bool:
    t0 = time.monotonic()
    ctx.session.last_cost = ""
    ctx.archive.log_section("review", "reviewer", engine, ctx.cur_stage,
                            ctx.cur_round, echo=ctx.echo)
    ctx.echo(f">>> Reviewer({engine}) is reviewing...")
    slug = f"reviewer-{safe_slug(ctx.cur_stage or 'startup')}-r{ctx.cur_round}"
    prompt = compose_review_prompt(engine, scope, ctx.prompts_dir, ctx.wf)
    prompt_art = ctx.archive.archive_text(f"{slug}-prompt.md", prompt,
                                          "reviewer", engine, ctx.cur_stage,
                                          ctx.cur_round)
    # Prewrite a failed sentinel instead of deleting the file: if the reviewer
    # does not write a verdict, the run stays failed (sh:1324-1327).
    ctx.verdict_path.write_text(FAILED_VERDICT, encoding="utf-8")
    io = ctx.engine_io()
    result = engine_call(
        lambda: run_reviewer(engine, prompt_file_instruction(str(prompt_art)),
                             ctx.settings, ctx.session, io),
        engine_out=ctx.engine_out, settings=ctx.settings,
        events=_retry_events(ctx, "reviewer", engine, slug),
    )
    output_art = ctx.archive.art_path(f"{slug}-output.txt")
    output_art.write_text(result.text.rstrip("\n") + "\n", encoding="utf-8")
    ctx.archive.write_meta(output_art, "reviewer", engine, ctx.cur_stage,
                           ctx.cur_round)
    ctx.log_file(result.text)
    if result.rc == QUOTA_ABORT_RC:
        # A quota give-up must not masquerade as "reviewer did not write a
        # verdict": that would burn worker repair rounds on a problem no code
        # change can fix (sh:1330-1334).
        raise WorkflowAbort(
            "!! Reviewer gave up on a quota/rate limit; aborting the run as resumable.",
            rc=QUOTA_ABORT_RC,
        )
    if result.rc != 0:
        ctx.echo_err("(warning: reviewer execution failed)")
    ctx.archive.archive_snapshot(ctx.engine_out, f"{slug}-final.raw", "reviewer",
                                 engine, ctx.cur_stage, ctx.cur_round)
    ctx.archive.metric("reviewer", engine, ctx.cur_round,
                       int(time.monotonic() - t0), ctx.session.last_cost,
                       stage=ctx.cur_stage)
    if not ctx.verdict_path.is_file():
        ctx.echo_err("(reviewer did not write verdict.json; treating as failed)")
        return False
    if ctx.collect_review_suggestions:
        collect_suggestions(ctx)
    stage_slug = safe_slug(ctx.cur_stage)
    ctx.archive.archive_snapshot(ctx.review_path,
                                 f"review-{stage_slug}-r{ctx.cur_round}.md",
                                 "reviewer", engine, ctx.cur_stage, ctx.cur_round)
    ctx.archive.archive_snapshot(ctx.verdict_path,
                                 f"verdict-{stage_slug}-r{ctx.cur_round}.json",
                                 "reviewer", engine, ctx.cur_stage, ctx.cur_round)
    if not verdict_approved(ctx.verdict_path):
        show_blockers(ctx)
        return False
    return True


def review_loop(ctx: WorkflowContext, reviewer: str, worker: str, scope: str,
                gate_cmd: str = "") -> None:
    ctx.cur_round = 1
    while not run_review(ctx, reviewer, scope):
        if ctx.cur_round >= ctx.settings.max_rounds:
            ctx.notify(f"adversarial-ai-coding:[{ctx.cur_stage}] review failed "
                       f"after {ctx.settings.max_rounds} rounds; human "
                       "intervention required")
            raise WorkflowAbort(
                f"!! [{ctx.cur_stage}] Review still failed after "
                f"{ctx.settings.max_rounds} rounds; stopping. Read "
                f"{ctx.review_path} and handle it manually."
            )
        ctx.cur_round += 1
        ctx.log(f"--- [{ctx.cur_stage}] round {ctx.cur_round}: worker updates "
                "from review findings ---")
        prompt = render_prompt(ctx.prompts_dir, "review-findings-repair", {
            "REVIEW_FILE": str(ctx.review_path),
            "STAGE": ctx.cur_stage,
        })
        work(ctx, worker, prompt)
        ctx.archive.archive_snapshot(
            ctx.review_path,
            f"review-{safe_slug(ctx.cur_stage)}-r{ctx.cur_round}-worker.md",
            "worker", worker, ctx.cur_stage, ctx.cur_round)
        # Repairs must pass the deterministic gate before review resumes.
        gate_loop(gate_cmd, cwd=ctx.workspace, prompts_dir=ctx.prompts_dir,
                  max_rounds=ctx.settings.max_rounds,
                  do_work=lambda p: work(ctx, worker, p),
                  log=ctx.log, notify=ctx.notify, stage=ctx.cur_stage)
    ctx.log(f"[{ctx.cur_stage}] Review approved")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_review.py -q` then `uv run pytest -q`
Expected: all PASS, suite green.

- [ ] **Step 5: Commit**

```bash
git add src/adversarial_ai_coding/review.py tests/test_review.py
git commit -m "feat: port review machinery and the review loop

Port verdict parsing with jq -e semantics, review prompt composition
(claude uses --json-schema, others get the verdict-file instruction),
the failed-verdict sentinel, suggestion accumulation per round, blocker
display, run_review with quota aborts raised as resumable, and
review_loop with repair rounds that re-run the deterministic gate
before review resumes."
```

---

## Verification at the End of This Plan

Run: `uv run pytest -q`
Expected: whole suite green.

## Not in This Plan (deliberately)

- Stage flow (`begin_stage`/`end_stage`, the main pipeline), `commit_work`
  / `commit_if_dirty` (they render prompts + call work), human gates,
  dual-spec, `finish`, `bootstrap_agents_md`, cli: plan 5.
- `_retry_events` is exported from workflow for review.py; plan 5 reuses it.
