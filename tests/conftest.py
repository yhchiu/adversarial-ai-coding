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
