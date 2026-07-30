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
        archive = establish_run_archive(wf / "runs", "test", settings)
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
