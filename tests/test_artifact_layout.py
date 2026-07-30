"""The aac/ artifact layout's git-reachability invariant.

docs/adr/0001-single-aac-root-for-run-artifacts.md puts everything the
workflow writes under one visible top-level directory, and keeps the
machine-only half out of git with a nested .gitignore containing "*"
instead of negation patterns. The whole design rests on one invariant:
aac/docs/** is reachable by git and aac/.run/** is not.

Nothing else pins that. Commits are made by the agent ("commit all
current changes") or by the ensure_committed fallback, both of which mean
git add -A, so what lands in a branch is decided entirely by ignore
rules. A mistake there is close to silent: the run still passes its
gates, and the human gate still shows a spec that later vanishes from the
branch. These tests fail loudly instead.
"""

import subprocess
from pathlib import Path

from adversarial_ai_coding.config import ARTIFACT_ROOT, DOCS_ROOT, WORK_DIR


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )


def tracked(repo: Path, path: str) -> list[str]:
    out = git(repo, "ls-files", path).stdout
    return [line for line in out.splitlines() if line]


def is_ignored(repo: Path, path: str) -> bool:
    # check-ignore exits 0 when the path is ignored, 1 when it is not.
    return git(repo, "check-ignore", "-q", path).returncode == 0


def test_layout_constants_match_the_adr():
    assert ARTIFACT_ROOT == "aac"
    assert DOCS_ROOT == "aac/docs"
    assert WORK_DIR == "aac/.run"
    # The committed half must be visible: a human reads the spec and plan
    # at the human gates, and editors hide dot-directories.
    assert not ARTIFACT_ROOT.startswith(".")
    assert not DOCS_ROOT.split("/")[-1].startswith(".")
    # The machine half must be hidden, and nested inside the one root so
    # only a single top-level name is claimed.
    assert WORK_DIR.split("/")[-1].startswith(".")
    assert WORK_DIR.startswith(f"{ARTIFACT_ROOT}/")
    assert DOCS_ROOT.startswith(f"{ARTIFACT_ROOT}/")


def test_work_dir_is_ignored_and_docs_are_committed(basic_run):
    # Both tests below only read the result of a plain run, so they share
    # one: see the basic_run fixture in conftest.py.
    new_repo = basic_run["repo"]

    # The ignore file is self-contained: one "*", no negation to forget.
    assert (new_repo / WORK_DIR / ".gitignore").read_text(
        encoding="utf-8"
    ) == "*\n"

    committed = tracked(new_repo, DOCS_ROOT)
    assert [name for name in committed if name.endswith("/spec.md")]
    assert [name for name in committed if name.endswith("/plan.md")]

    assert tracked(new_repo, WORK_DIR) == []
    assert tracked(new_repo, f"{WORK_DIR}/state") == []
    assert tracked(new_repo, f"{WORK_DIR}/archive") == []

    for name in ("review.md", "verdict.json", "latest-run.txt", ".gitignore"):
        assert is_ignored(new_repo, f"{WORK_DIR}/{name}")
    spec = next((new_repo / DOCS_ROOT).glob("*/spec.md"))
    assert not is_ignored(new_repo, str(spec.relative_to(new_repo).as_posix()))

    # A clean tree proves nothing under the work dir is merely untracked:
    # git add -A would otherwise sweep it into the next commit.
    assert git(new_repo, "status", "--porcelain").stdout == ""


def test_one_top_level_entry_is_added_to_the_repository(basic_run):
    new_repo = basic_run["repo"]
    before = set(basic_run["before"])
    added = {path.name for path in new_repo.iterdir()} - before
    # The run also creates AGENTS.md, CLAUDE.md, and whatever the fake
    # agents write as product code; ARTIFACT_ROOT must be the only
    # directory the artifact layout itself contributes.
    assert ARTIFACT_ROOT in added
    assert "specs" not in added
    assert ".workflow" not in added
