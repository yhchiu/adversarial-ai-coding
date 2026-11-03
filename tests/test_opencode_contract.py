"""OpenCode 1 and OpenCode 2, observed at startup and at the calls."""

import json
import os
import sys
from pathlib import Path

from adversarial_ai_coding import agents
from adversarial_ai_coding.agents import AgentIO, AgentRef, AgentSession, validate_agents
from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runstate import snapshot_values


def make(env=None):
    return Settings.from_env(env or {}, run_id="20261103-000000")


def make_io(tmp_path):
    echoed = []
    return (
        AgentIO(
            agent_out=tmp_path / "agent-out.txt",
            raw_out=tmp_path / "agent-raw.txt",
            verdict_path=tmp_path / "verdict.json",
            echo=echoed.append,
        ),
        echoed,
    )


def install_fake_opencode(tmp_path, monkeypatch, version):
    """A fake opencode whose --version text is `version`.

    Startup receives the wrapper path from `which`. Calls resolve the same
    path through the shutil lookup the adapter already uses.
    """
    script = Path(__file__).with_name("fake_opencode.py")
    log = tmp_path / "invocations.jsonl"
    monkeypatch.setenv("OPENCODE_FAKE_LOG", str(log))
    monkeypatch.setenv("OPENCODE_FAKE_VERSION", version)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    if os.name == "nt":
        wrapper = bindir / "opencode.cmd"
        wrapper.write_text(
            f'@"{sys.executable}" -X utf8 -S -E "{script}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bindir / "opencode"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" -X utf8 -S -E "{script}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    resolved = str(wrapper)

    def which(name):
        if name == "opencode":
            return resolved
        return f"C:/unused/{name}"

    monkeypatch.setattr(
        agents.shutil, "which", lambda name: resolved if name == "opencode" else name
    )
    return log, which


def invocations(log: Path):
    if not log.is_file():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line
    ]


def test_startup_skips_the_version_read_when_nothing_invokes_opencode():
    seen = []

    def which(name):
        seen.append(name)
        return f"C:/unused/{name}"

    validate_agents(
        make(
            {
                "AGENT_A": "claude",
                "AGENT_B": "codex",
                "IMPL_AGENT": "impl-wrapper",
            }
        ),
        which=which,
    )

    assert "opencode" not in seen


def test_startup_reads_one_major_when_a_slot_or_impl_invokes_opencode(
    tmp_path, monkeypatch
):
    cases = [
        {"AGENT_A": "opencode", "AGENT_B": "codex"},
        {"AGENT_A": "claude", "AGENT_B": "opencode"},
        {"AGENT_A": "claude", "AGENT_B": "codex", "IMPL_AGENT": "opencode"},
        {"AGENT_A": "opencode", "AGENT_B": "opencode", "IMPL_AGENT": "opencode"},
    ]
    for env in cases:
        log, which = install_fake_opencode(tmp_path, monkeypatch, "1.2.24")
        validate_agents(make(env), which=which)
        assert [item["argv"] for item in invocations(log)] == [["--version"]]
        log.unlink()


def test_the_next_startup_reads_the_version_again_and_the_snapshot_does_not(
    tmp_path, monkeypatch
):
    log, which = install_fake_opencode(tmp_path, monkeypatch, "1.8.24-distinct")
    env = {"AGENT_A": "opencode", "AGENT_B": "codex"}
    first = make(env)
    validate_agents(first, which=which)
    values = snapshot_values(
        first,
        branch="",
        gate_cmd="",
        build_gate_cmd="",
        phase_gate_cmd="",
        task_arg="",
        task_source_kind="literal",
        task_source_path="",
    )

    assert not any("version" in key.lower() for key in values)
    assert "1.8.24-distinct" not in values.values()

    validate_agents(make(env), which=which)

    assert [item["argv"] for item in invocations(log)] == [
        ["--version"],
        ["--version"],
    ]


def test_a_version_with_no_major_number_fails_before_any_call(tmp_path, monkeypatch):
    from adversarial_ai_coding.config import SettingsError

    log, which = install_fake_opencode(tmp_path, monkeypatch, "local")

    try:
        validate_agents(make({"AGENT_A": "opencode", "AGENT_B": "codex"}), which=which)
    except SettingsError as exc:
        message = str(exc)
    else:
        raise AssertionError("startup accepted a version with no major number")

    assert "could not be read" in message
    assert "local" in message
    assert invocations(log) == [{"argv": ["--version"], "stdin": ""}]


def test_opencode_1_keeps_the_positional_prompt_closed_stdin_and_unguarded_resume(
    tmp_path, monkeypatch
):
    log, which = install_fake_opencode(tmp_path, monkeypatch, "1 then 9")
    settings = make({"AGENT_A": "opencode", "AGENT_B": "opencode"})
    validate_agents(settings, which=which)
    io, _ = make_io(tmp_path)
    session = AgentSession()

    agents.run_worker(
        AgentRef("A", "opencode"), "keep this prompt", settings, session, io
    )
    agents.run_worker(
        AgentRef("A", "opencode"), "resume this prompt", settings, session, io
    )
    agents.run_reviewer(
        AgentRef("B", "opencode"), "review this prompt", settings, session, io
    )

    version, fresh, resume, review = invocations(log)
    assert version == {"argv": ["--version"], "stdin": ""}
    assert fresh["argv"][0] == "run"
    assert fresh["argv"][-1] == "keep this prompt"
    assert "--session" not in fresh["argv"]
    assert fresh["stdin"] == ""
    assert resume["argv"][-3:] == ["--session", "ses_captured", "resume this prompt"]
    assert resume["stdin"] == ""
    assert review["argv"][-1] == "review this prompt"
    assert "--session" not in review["argv"]
    assert review["stdin"] == ""
    assert session.worker_session == "ses_captured"


def test_an_unsupported_major_is_not_treated_as_either_contract(tmp_path, monkeypatch):
    from adversarial_ai_coding.config import SettingsError

    log, which = install_fake_opencode(tmp_path, monkeypatch, "9 then 1")

    try:
        validate_agents(make({"AGENT_B": "opencode"}), which=which)
    except SettingsError as exc:
        message = str(exc)
    else:
        raise AssertionError("startup guessed a contract for major 9")

    assert "9" in message
    assert [item["argv"] for item in invocations(log)] == [["--version"]]


def test_major_version_two_is_accepted_at_startup(tmp_path, monkeypatch):
    log, which = install_fake_opencode(tmp_path, monkeypatch, "opencode 2.2.5")

    validate_agents(
        make({"AGENT_A": "claude", "AGENT_B": "codex", "IMPL_AGENT": "opencode"}),
        which=which,
    )

    assert invocations(log) == [{"argv": ["--version"], "stdin": ""}]
