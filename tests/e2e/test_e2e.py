"""E2E driver, port of tests/e2e/run.sh.

The fixture baseline is offline. The full real-agent workflow is marker-gated
and excluded from default pytest runs because it consumes quota.
"""

import csv
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
FIXTURE = HERE / "fixture"

E2E_DEFAULTS = {
    "HUMAN_GATE": "0",
    "AGENT_A": "claude",
    "MODEL_A": "sonnet",
    "CLAUDE_ARGS": "--effort=low",
    "AGENT_B": "codex",
    "MODEL_B": "gpt-5.5",
    "CODEX_ARGS": "-c model_reasoning_effort=low",
}

needs_go = pytest.mark.skipif(
    shutil.which("go") is None, reason="fixture is a Go project"
)


def run(cmd, cwd, env=None, check=True):
    merged = {**os.environ, **(env or {})}
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"{cmd} failed rc={proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def make_fixture_repo(base: Path) -> Path:
    repo = base / "repo"
    shutil.copytree(FIXTURE, repo)
    run(["git", "init", "-q", "-b", "main"], repo)
    run(["git", "config", "user.email", "e2e@local"], repo)
    run(["git", "config", "user.name", "e2e"], repo)
    run(["git", "add", "-A"], repo)
    run(
        [
            "git",
            "commit",
            "-qm",
            "chore: baseline fixture for adversarial-ai-coding E2E",
        ],
        repo,
    )
    return repo


def verify_gates(repo: Path):
    run(["go", "build", "./..."], repo)
    run(["go", "vet", "./..."], repo)
    run(["go", "test", "./..."], repo)


@needs_go
def test_fixture_baseline(tmp_path):
    repo = make_fixture_repo(tmp_path)
    verify_gates(repo)


@pytest.mark.e2e
@needs_go
def test_full_workflow_e2e():
    base = Path(os.environ.get("E2E_DIR") or tempfile.mkdtemp(prefix="wf-e2e-"))
    base.mkdir(parents=True, exist_ok=True)
    print(f"== E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    env = {key: os.environ.get(key, value) for key, value in E2E_DEFAULTS.items()}
    tool = shutil.which("adversarial-ai-coding")
    assert tool, "console script not installed; run `uv sync` first"
    proc = subprocess.run(
        [tool, "task.md"],
        cwd=repo,
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    (base / "run.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
    assert proc.returncode == 0, (
        f"workflow rc={proc.returncode}; see {base}/run.log"
    )
    log = (base / "run.log").read_text(encoding="utf-8")

    assert "All stages complete" in log
    assert "Quality gate passed" in log
    branch = run(["git", "branch", "--show-current"], repo).stdout.strip()
    assert branch.startswith("auto/")
    assert (repo / "AGENTS.md").is_file() and (repo / "CLAUDE.md").is_file()

    spec_dirs = sorted((repo / "specs").glob("*/"))
    assert spec_dirs, "specs/<run>/ missing"
    spec = spec_dirs[0] / "spec.md"
    plan = spec_dirs[0] / "plan.md"
    assert "assumptions and open questions" in spec.read_text(
        encoding="utf-8"
    ).lower()
    plan_text = plan.read_text(encoding="utf-8")
    assert "- [ ] " not in plan_text and "- [x]" in plan_text

    strutil = "".join(
        path.read_text(encoding="utf-8") for path in (repo / "strutil").glob("*.go")
    )
    assert "func IsPalindrome" in strutil

    protected = repo / ".workflow" / "protected-tests.txt"
    base_sha = repo / ".workflow" / "protected-base.sha"
    assert protected.is_file() and protected.stat().st_size > 0
    assert base_sha.is_file()
    from adversarial_ai_coding.gitops import protected_violations

    paths = frozenset(
        line
        for line in protected.read_text(encoding="utf-8").splitlines()
        if line
    )
    assert (
        protected_violations(
            paths, base_sha.read_text(encoding="utf-8").strip(), repo
        )
        == []
    )

    run_dir = Path(
        (repo / ".workflow" / "latest-run.txt").read_text(encoding="utf-8").strip()
    )
    assert run_dir.is_dir()
    for pattern in (
        "*-task-source.md",
        "*-task.txt",
        "*-prompt.md",
        "*-output.txt",
        "*-attempt-*-rc*.raw",
        "*-git-status.txt",
        "*-git-diff.patch",
        "*.meta.json",
    ):
        assert list(run_dir.glob(pattern)), f"missing artifact {pattern}"
    log_meta = json.loads(
        (run_dir / "logs" / "001-run.log.meta.json").read_text(encoding="utf-8")
    )
    assert log_meta["run_id"] and log_meta["generator_role"] == "workflow"

    commits = int(
        run(["git", "rev-list", "--count", "main..HEAD"], repo).stdout.strip()
    )
    assert commits >= 5, f"small-batch commits: main..HEAD = {commits}"

    verify_gates(repo)

    metrics = run_dir / "metrics.csv"
    assert metrics.is_file()
    rows = list(csv.reader(metrics.open(newline="", encoding="utf-8")))
    assert rows[0] == [
        "run_id",
        "stage",
        "role",
        "agent",
        "round",
        "duration_s",
        "cost_usd",
        "model",
        "model_args",
        "generated_at",
        "agent_slot",
    ]
    assert len(rows) > 1 and all(len(row) == 11 for row in rows)
    print(f"Acceptance passed; workspace kept at {base} (delete after inspection)")
