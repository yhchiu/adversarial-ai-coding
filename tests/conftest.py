"""Shared fixtures. new_repo ports the bash suite's temp-repo helper."""

import shutil
import subprocess

import pytest


@pytest.fixture(scope="session")
def _repo_template(tmp_path_factory):
    """Build the throwaway repo once per worker; new_repo copies it.

    The five git calls below cost about 0.9s per test on Windows, and 166
    tests ask for a repo, so this fixture was a fifth of the whole suite.
    Copying the finished repo instead costs about 0.09s. The copy is a
    real repository: git init writes no absolute paths into .git/config,
    so the result works from any directory.
    """
    template = tmp_path_factory.mktemp("repo-template")

    def _git(*args):
        subprocess.run(
            ["git", "-C", str(template), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(template)],
        check=True,
        capture_output=True,
    )
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "test")
    (template / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    return template


@pytest.fixture
def new_repo(tmp_path, _repo_template):
    """A throwaway git repo with one commit, like helpers.test.sh new_repo."""
    shutil.copytree(_repo_template, tmp_path, dirs_exist_ok=True)
    return tmp_path


@pytest.fixture(scope="session")
def _basic_run_template(tmp_path_factory, _repo_template):
    """One finished plain workflow run, built once and handed out as copies.

    Several tests start by driving the same completed run and only then
    do something interesting to it: damage the ledger, drop the protected
    controls, inspect what git tracks. That opening run costs about 13
    seconds and roughly 100 child processes, and it is identical every
    time, so it is worth paying for once.

    The fake-agent wrappers deliberately stay at this shared path rather
    than being recreated next to each copy. A resume checks the current
    AGENT_A and AGENT_B against the run's settings snapshot and refuses a
    mismatch, so the paths recorded in the template's snapshot have to
    stay valid for every copy made from it.
    """
    from workflow_harness import run_cli, wf_env

    base = tmp_path_factory.mktemp("basic-run")
    repo = base / "repo"
    shutil.copytree(_repo_template, repo)
    work = base / "driver"
    work.mkdir()

    env = wf_env(work)
    before = sorted(path.name for path in repo.iterdir())
    with pytest.MonkeyPatch.context() as monkeypatch:
        rc = run_cli(repo, env, monkeypatch=monkeypatch)
    assert rc == 0, "the shared basic run must complete"
    return {"repo": repo, "work": work, "env": env, "before": before}


@pytest.fixture
def basic_run(tmp_path, _basic_run_template):
    """A private copy of the completed plain run.

    Returns the workspace, the driver directory holding calls.log, the env
    that produced it, and the top-level entries the repo had before it ran.
    Mutate any of it freely: the template is never touched.
    """
    from workflow_harness import driver_workdir

    repo = tmp_path
    shutil.copytree(_basic_run_template["repo"], repo, dirs_exist_ok=True)
    work = driver_workdir(tmp_path)
    shutil.copytree(_basic_run_template["work"], work)
    # Only the mutable paths move; AGENT_A and AGENT_B keep pointing at the
    # template's wrappers so the run's settings snapshot still matches.
    env = dict(
        _basic_run_template["env"],
        FAKE_CALLS_LOG=str(work / "calls.log"),
        FAKE_ABORT_ON=str(work / "abort-on"),
    )
    return {
        "repo": repo,
        "work": work,
        "env": env,
        "before": _basic_run_template["before"],
    }


@pytest.fixture
def make_ctx(new_repo):
    """WorkflowContext over a throwaway repo with silenced console sinks."""
    from adversarial_ai_coding.agents import AgentSession
    from adversarial_ai_coding.archive import establish_run_archive
    from adversarial_ai_coding.config import Settings
    from adversarial_ai_coding.prompts import default_prompts_dir
    from adversarial_ai_coding.workflow import WorkflowContext

    def _make(env=None):
        settings = Settings.from_env(env or {"RETRY_ON_LIMIT": "0"}, run_id="test")
        wf = new_repo / "aac/.run"
        wf.mkdir(parents=True, exist_ok=True)
        archive = establish_run_archive(wf / "archive", "test", settings)
        return WorkflowContext(
            settings=settings,
            archive=archive,
            state=None,
            session=AgentSession(),
            workspace=new_repo,
            wf=wf,
            prompts_dir=default_prompts_dir({}),
            spec_dir=new_repo / "aac" / "docs",
            cur_stage="stage",
            echo=lambda _line: None,
            echo_err=lambda _line: None,
        )

    return _make
