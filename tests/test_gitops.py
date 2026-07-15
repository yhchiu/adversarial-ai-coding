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
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
    protected = frozenset({"acc_test.go"})

    assert protected_violations(protected, base, new_repo) == []
    (new_repo / "acc_test.go").write_text("weakened\n", encoding="utf-8")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    git(new_repo, "add", "-A")
    git(new_repo, "commit", "-qm", "hack")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    assert protected_violations(frozenset(), base, new_repo) == []


def test_protected_violations_fails_closed_when_git_diff_fails(new_repo):
    with pytest.raises(subprocess.CalledProcessError):
        protected_violations(
            frozenset({"acc_test.go"}), "not-a-valid-base", new_repo
        )


def test_ensure_committed_fallback_commit(new_repo):
    (new_repo / "base.txt").write_text("left dirty\n", encoding="utf-8")
    warnings = []
    ensure_committed(new_repo, "write-code", warnings.append)
    assert status_porcelain(new_repo) == ""
    assert "fallback commit" in warnings[0]
    subject = git(new_repo, "log", "-1", "--format=%s")
    assert subject == "chore: commit remaining write-code changes"
    ensure_committed(new_repo, "write-code", warnings.append)
    assert len(warnings) == 1


def test_verify_last_head_ancestor_warns(new_repo):
    st = claimed(new_repo)
    first = head_sha(new_repo)
    st.record_stage("s1", first)
    git(new_repo, "commit", "--allow-empty", "-qm", "second")
    warnings = []
    verify_last_head(st, new_repo, warnings.append)
    assert any("new commits" in warning for warning in warnings)


def test_verify_last_head_unreachable_fails_closed(new_repo):
    st = claimed(new_repo)
    st.record_stage("s1", "0123456789abcdef0123456789abcdef01234567")
    with pytest.raises(RunStateError, match="not reachable"):
        verify_last_head(st, new_repo, lambda _msg: None)


def test_verify_last_head_ledger_without_checkpoint_fails(new_repo):
    st = claimed(new_repo)
    st.record_stage("s1", head_sha(new_repo))
    (st.state_dir / "last-head").unlink()
    with pytest.raises(RunStateError, match="no last-head checkpoint"):
        verify_last_head(st, new_repo, lambda _msg: None)


def test_verify_last_head_fresh_state_passes(new_repo):
    st = claimed(new_repo)
    verify_last_head(st, new_repo, lambda _msg: None)


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
    assert any(
        "absorbed into the next automatic commit" in warning for warning in warnings
    )


def test_resume_workspace_no_recorded_branch_warns_and_stays(new_repo):
    st = claimed(new_repo)
    warnings = []
    resume_workspace("", st, new_repo, warnings.append)
    assert any("no branch record" in warning for warning in warnings)


def test_setup_workspace_branch_mode(new_repo):
    settings = Settings.from_env({}, run_id="20260711-010101")
    workspace = setup_workspace(settings, "20260711-010101", new_repo)
    assert workspace == new_repo
    assert current_branch(new_repo) == "auto/20260711-010101"


def test_setup_workspace_worktree_mode(new_repo):
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
