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


def ignore_work_dir(repo):
    """Commit the nested ignore file a real run writes into aac/.run."""
    workflow = repo / "aac/.run"
    workflow.mkdir(parents=True, exist_ok=True)
    (workflow / ".gitignore").write_text("*\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-f", "aac/.run/.gitignore"],
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "workflow-ignore"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_archive_git_state_no_side_effects_and_untracked_content(new_repo):
    ignore_work_dir(new_repo)
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


def spy_on_git(monkeypatch):
    """Record every git argv archive_git_state spawns, and still run it."""
    seen = []
    real_run = subprocess.run

    def counting_run(args, *rest, **kwargs):
        if isinstance(args, list) and args[:1] == ["git"]:
            seen.append(list(args))
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "run", counting_run)
    return seen


def make_archive(repo):
    settings = Settings.from_env({}, run_id="test")
    return establish_run_archive(repo / "aac/.run" / "archive", "test", settings), settings


def test_work_tree_probe_is_resolved_once_per_archive(new_repo, monkeypatch):
    # archive_git_state runs after every agent call, and whether the
    # workspace is a work tree cannot change within a run, so the probe
    # must not cost one git process per call.
    archive, settings = make_archive(new_repo)
    seen = spy_on_git(monkeypatch)

    for round in (1, 2, 3):
        archive.archive_git_state(
            "worker",
            agent_ref("A", settings),
            f"worker-code-r{round}",
            stage="code",
            round=round,
            cwd=new_repo,
        )

    probes = [argv for argv in seen if argv[1] == "rev-parse"]
    assert len(probes) == 1


def test_clean_tree_skips_the_scratch_index(new_repo, monkeypatch):
    # The scratch index only exists to fold untracked files into the
    # diff. With nothing untracked, the status output already read says
    # so, and the extra copy and git add must not happen.
    ignore_work_dir(new_repo)
    (new_repo / "base.txt").write_text("changed\n", encoding="utf-8")
    archive, settings = make_archive(new_repo)
    seen = spy_on_git(monkeypatch)

    archive.archive_git_state(
        "worker",
        agent_ref("A", settings),
        "worker-code-r1",
        stage="code",
        round=1,
        cwd=new_repo,
    )

    assert [argv for argv in seen if argv[1] == "add"] == []
    patch = (archive.run_dir / "002-worker-code-r1-git-diff.patch").read_text(
        encoding="utf-8"
    )
    assert "changed" in patch
    assert porcelain(new_repo) == " M base.txt\n"


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
