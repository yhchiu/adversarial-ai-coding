"""Ports helpers.test.sh:352-362 (archive_git_state side effects)."""

import subprocess

from adversarial_ai_coding.archive import establish_run_archive
from adversarial_ai_coding.agents import agent_ref
from adversarial_ai_coding.config import Settings


def porcelain(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_archive_git_state_no_side_effects_and_untracked_content(new_repo):
    workflow = new_repo / "aac/.run"
    workflow.mkdir(parents=True)
    (workflow / ".gitignore").write_text("*\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(new_repo), "add", "-f", "aac/.run/.gitignore"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(new_repo), "commit", "-qm", "workflow-ignore"],
        capture_output=True,
        text=True,
        check=True,
    )
    (new_repo / "base.txt").write_text("changed\n", encoding="utf-8")
    (new_repo / "new.txt").write_text("new content\n", encoding="utf-8")
    settings = Settings.from_env({}, run_id="test")
    archive = establish_run_archive(
        new_repo / "aac/.run" / "archive", "test", settings
    )
    before = porcelain(new_repo)
    archive.archive_git_state(
        "worker",
        agent_ref("A", settings),
        "worker-code-r2",
        stage="code",
        round=2,
        cwd=new_repo,
    )
    after = porcelain(new_repo)
    assert before == after
    patch = (archive.run_dir / "002-worker-code-r2-git-diff.patch").read_text(
        encoding="utf-8"
    )
    assert "new content" in patch
    assert "changed" in patch
    status = (archive.run_dir / "001-worker-code-r2-git-status.txt").read_text(
        encoding="utf-8"
    )
    assert "base.txt" in status and "new.txt" in status


def test_archive_git_state_outside_repo_is_noop(tmp_path):
    settings = Settings.from_env({}, run_id="test")
    archive = establish_run_archive(tmp_path / "archive", "test", settings)
    archive.archive_git_state(
        "worker",
        agent_ref("A", settings),
        "slug",
        stage="s",
        round=1,
        cwd=tmp_path,
    )
    assert not list(archive.run_dir.glob("*git-status*"))
