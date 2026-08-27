"""E2E driver, port of tests/e2e/run.sh.

The fixture baseline is offline, but it shells out to go build, vet and test,
so it carries the slow marker and stays out of the fast inner loop. The full
real-agent workflow is marker-gated separately because it consumes quota.
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
    "AGENT_A_ARGS": "--effort=low",
    "AGENT_B": "codex",
    "MODEL_B": "gpt-5.5",
    "AGENT_B_ARGS": "-c model_reasoning_effort=low",
}

needs_go = pytest.mark.skipif(
    shutil.which("go") is None, reason="fixture is a Go project"
)


def e2e_base(prefix: str) -> Path:
    """Live-workspace root. Never under the user's AppData Temp on Windows.

    Codex's Windows elevated sandbox stamps unreadable ACLs on files it
    writes when the workspace lives under %TEMP% (every such live run died
    with PermissionError on review.md or verdict.json; the same runs pass
    from C:\\tmp). E2E_DIR still overrides the location explicitly.
    """

    if os.environ.get("E2E_DIR"):
        # A fresh prefix-named child per test: both live tests can share
        # one E2E_DIR override without colliding on <E2E_DIR>/repo.
        root = Path(os.environ["E2E_DIR"])
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    if os.name == "nt":
        root = Path("C:/tmp")
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=root))
    return Path(tempfile.mkdtemp(prefix=prefix))


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


@pytest.mark.slow
@needs_go
def test_fixture_baseline(tmp_path):
    repo = make_fixture_repo(tmp_path)
    verify_gates(repo)


@pytest.mark.e2e
@needs_go
def test_full_workflow_e2e():
    base = e2e_base("wf-e2e-")
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
    assert branch.startswith("aac/")
    assert (repo / "AGENTS.md").is_file() and (repo / "CLAUDE.md").is_file()

    spec_dirs = sorted((repo / "aac" / "docs").glob("*/"))
    assert spec_dirs, "aac/docs/<run>/ missing"
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

    protected = repo / "aac/.run" / "protected-tests.txt"
    base_sha = repo / "aac/.run" / "protected-base.sha"
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
        (repo / "aac/.run" / "latest-run.txt").read_text(encoding="utf-8").strip()
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


@pytest.mark.e2e
@needs_go
def test_full_workflow_phased_e2e():
    base = e2e_base("wf-e2e-ph-")
    print(f"== Phased E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    env = {key: os.environ.get(key, value) for key, value in E2E_DEFAULTS.items()}
    env["PHASES"] = "1"
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
    assert proc.returncode == 0, f"workflow rc={proc.returncode}; see {base}/run.log"
    log = (base / "run.log").read_text(encoding="utf-8")

    assert "All stages complete" in log
    assert "[phase-01-write-tests]" in log
    assert "[phase-01-implement]" in log
    assert "Phase red check passed" in log

    state_dirs = list((repo / "aac/.run" / "state").iterdir())
    assert len(state_dirs) == 1
    ledger = json.loads(
        (state_dirs[0] / "ledger.json").read_text(encoding="utf-8")
    )
    stages = ledger["stages"]
    assert "phase-01-write-tests" in stages and "phase-01-implement" in stages
    assert "write-acceptance-tests" not in stages

    protected = repo / "aac/.run" / "protected-tests.txt"
    assert protected.is_file() and protected.stat().st_size > 0
    plan = next((repo / "aac" / "docs").glob("*/plan.md")).read_text(encoding="utf-8")
    assert "## Phase 1:" in plan
    assert "- [ ] " not in plan and "- [x]" in plan
    verify_gates(repo)
    print(f"Phased E2E passed; workspace kept at {base} (delete after inspection)")


IMPORT_SPEC_TEXT = """# Spec: IsPalindrome for strutil

Add IsPalindrome(s string) bool to the strutil package. It reports
whether the string is a palindrome.

## Acceptance criteria

- Compare rune-by-rune, verbatim: no case folding, no Unicode
  normalization; every character (including spaces) is significant.
- The empty string returns true.
- Examples: "" true; "a" true; "abc" false; "Abba" false (case matters);
  "a b a" true; the rune sequence U+4E0A U+6D77 U+6D77 U+4E0A true
  (write that test string with Go Unicode escape sequences, not literal
  CJK characters).
- Unit tests cover the example set.

## Out of scope

- Do not modify the existing Reverse function. No new APIs, no CLI.

## Assumptions and Open Questions

- Assumes the strutil package layout stays unchanged; no open questions.
"""

IMPORT_PLAN_TEXT = """# Plan

- [ ] Add IsPalindrome to strutil with unit tests for the ASCII examples
- [ ] Add the CJK palindrome test using Go Unicode escape sequences
"""


@pytest.mark.e2e
@needs_go
def test_full_workflow_import_e2e():
    base = e2e_base("wf-e2e-imp-")
    print(f"== Import E2E workspace:{base}")
    repo = make_fixture_repo(base)
    verify_gates(repo)

    external = base / "external"
    external.mkdir()
    spec = external / "spec.md"
    spec.write_text(IMPORT_SPEC_TEXT, encoding="utf-8")
    plan = external / "plan.md"
    plan.write_text(IMPORT_PLAN_TEXT, encoding="utf-8")

    env = {key: os.environ.get(key, value) for key, value in E2E_DEFAULTS.items()}
    env["IMPORT_SPEC"] = str(spec)
    env["IMPORT_PLAN"] = str(plan)
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
    assert proc.returncode == 0, f"workflow rc={proc.returncode}; see {base}/run.log"
    log = (base / "run.log").read_text(encoding="utf-8")

    assert "All stages complete" in log
    assert "Imported spec from" in log
    assert "Imported plan from" in log
    assert spec.read_text(encoding="utf-8") == IMPORT_SPEC_TEXT

    spec_copy = next((repo / "aac" / "docs").glob("*/spec.md")).read_text(
        encoding="utf-8"
    )
    assert "assumptions and open questions" in spec_copy.lower()
    plan_text = next((repo / "aac" / "docs").glob("*/plan.md")).read_text(
        encoding="utf-8"
    )
    assert "- [x]" in plan_text and "- [ ] " not in plan_text

    strutil = "".join(
        path.read_text(encoding="utf-8")
        for path in (repo / "strutil").glob("*.go")
    )
    assert "func IsPalindrome" in strutil

    run_dir = Path(
        (repo / "aac/.run" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-spec.md"))
    assert list(run_dir.glob("*imported-plan.md"))
    verify_gates(repo)
    print(f"Import E2E passed; workspace kept at {base} (delete after inspection)")
