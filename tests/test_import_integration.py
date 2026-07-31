"""Import-mode integration: cli.main end-to-end with fake agents.

Reuses the resume-suite harness. Fake-agent call counts prove which AI
steps ran: a full basic run has exactly 4 reviewer 'review' calls (spec,
plan, branch, final acceptance). IMPORT_REVIEW=0 skips review only for
artifacts that were actually imported.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from workflow_harness import (
    calls,
    driver_workdir,
    run_cli,
    state_dir_of,
    wf_env,
)

SPEC_TEXT = (
    "# Spec: demo feature\n\nDemo feature description.\n\n"
    "## Assumptions and Open Questions\n\n- none\n"
)
PLAN_TEXT = "# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n"


def import_files(work: Path, plan: bool = False) -> dict:
    spec = work / "external-spec.md"
    spec.write_text(SPEC_TEXT, encoding="utf-8")
    overrides = {"IMPORT_SPEC": str(spec)}
    if plan:
        plan_file = work / "external-plan.md"
        plan_file.write_text(PLAN_TEXT, encoding="utf-8")
        overrides["IMPORT_PLAN"] = str(plan_file)
    return overrides


@pytest.mark.slow
def test_import_spec_skips_write_and_keeps_review(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 1
    assert calls(work, "fake-reviewer review") == 4
    spec = next((new_repo / "aac" / "docs").glob("*/spec.md"))
    assert spec.read_text(encoding="utf-8") == SPEC_TEXT
    assert (work / "external-spec.md").read_text(encoding="utf-8") == SPEC_TEXT
    run_dir = Path(
        (new_repo / "aac/.run" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-spec.md"))


@pytest.mark.slow
def test_relative_import_paths_survive_worktree_setup(
    new_repo, tmp_path, monkeypatch, capsys
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    external = new_repo / "external"
    external.mkdir()
    spec_source = external / "spec.md"
    spec_source.write_text(SPEC_TEXT, encoding="utf-8")
    plan_source = external / "plan.md"
    plan_source.write_text(PLAN_TEXT, encoding="utf-8")
    env = wf_env(
        work,
        IMPORT_SPEC=os.path.relpath(spec_source, new_repo),
        IMPORT_PLAN=os.path.relpath(plan_source, new_repo),
        USE_WORKTREE="1",
    )

    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0

    workspace = Path.cwd()
    assert workspace != new_repo
    # The announcement asks git which branch it is on instead of rebuilding
    # the name, so it cannot drift away from setup_workspace. test_gitops
    # pins what that name is; this pins only that the message reports it.
    created = subprocess.run(
        ["git", "-C", str(workspace), "branch", "--show-current"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert f"(branch {created};" in capsys.readouterr().out
    spec = next((workspace / "aac" / "docs").glob("*/spec.md"))
    assert spec.read_text(encoding="utf-8") == SPEC_TEXT
    state = state_dir_of(workspace)
    snapshot = json.loads((state / "settings.json").read_text(encoding="utf-8"))
    assert snapshot["import_spec"] == str(spec_source.resolve())
    assert snapshot["import_plan"] == str(plan_source.resolve())


@pytest.mark.slow
def test_import_spec_and_plan_review_off(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work, plan=True), IMPORT_REVIEW="0")
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 0
    assert calls(work, "fake-reviewer review") == 2
    plan = next((new_repo / "aac" / "docs").glob("*/plan.md"))
    text = plan.read_text(encoding="utf-8")
    assert "- [x]" in text and "- [ ] " not in text
    run_dir = Path(
        (new_repo / "aac/.run" / "latest-run.txt")
        .read_text(encoding="utf-8")
        .strip()
    )
    assert list(run_dir.glob("*imported-plan.md"))


@pytest.mark.slow
def test_import_spec_review_off_still_writes_and_reviews_generated_plan(
    new_repo, tmp_path, monkeypatch
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work), IMPORT_REVIEW="0")

    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 0

    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 1
    assert calls(work, "fake-reviewer review") == 3


def test_import_preflight_fails_before_any_agent_call(
    new_repo, tmp_path, monkeypatch
):
    work = driver_workdir(tmp_path)
    work.mkdir()
    bad_spec = work / "no-assumptions.md"
    bad_spec.write_text("# Spec\n\nNo required section.\n", encoding="utf-8")
    env = wf_env(work, IMPORT_SPEC=str(bad_spec))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 1
    assert not (work / "calls.log").is_file()

    env = wf_env(work, IMPORT_PLAN=str(work / "external-plan.md"))
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 1
    assert not (work / "calls.log").is_file()


@pytest.mark.slow
def test_import_run_resumes_from_snapshot(new_repo, tmp_path, monkeypatch):
    work = driver_workdir(tmp_path)
    work.mkdir()
    env = wf_env(work, **import_files(work, plan=True))
    (work / "abort-on").write_text("write-acceptance\n", encoding="utf-8")
    assert run_cli(new_repo, env, monkeypatch=monkeypatch) == 75
    state = state_dir_of(new_repo)
    (work / "abort-on").unlink()

    resume_env = {
        key: value
        for key, value in env.items()
        if not key.startswith("IMPORT_")
    }
    resume_env["RESUME_RUN"] = state.name

    conflict_env = dict(
        resume_env, IMPORT_SPEC=str(work / "somewhere-else.md")
    )
    assert run_cli(new_repo, conflict_env, args=[], monkeypatch=monkeypatch) == 1

    assert run_cli(new_repo, resume_env, args=[], monkeypatch=monkeypatch) == 0
    assert calls(work, "fake-worker write-spec") == 0
    assert calls(work, "fake-worker write-plan") == 0
