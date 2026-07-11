"""Shared fixtures. new_repo ports the bash suite's temp-repo helper."""

import subprocess

import pytest


@pytest.fixture
def new_repo(tmp_path):
    """A throwaway git repo with one commit, like helpers.test.sh new_repo."""

    def _git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "test")
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    return tmp_path
