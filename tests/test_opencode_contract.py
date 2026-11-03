"""OpenCode 1 and OpenCode 2, observed at startup and at the calls."""

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from adversarial_ai_coding import agents
from adversarial_ai_coding.agents import AgentIO, AgentRef, AgentSession, validate_agents
from adversarial_ai_coding.config import Settings, SettingsError
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

    settings = make(
        {
            "AGENT_A": "claude",
            "AGENT_B": "codex",
            "IMPL_AGENT": "impl-wrapper",
        }
    )
    validate_agents(settings, which=which)

    assert "opencode" not in seen
    assert settings.opencode_major is None


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
    assert "opencode_major" not in values
    assert "1.8.24-distinct" not in values.values()
    assert replace(first, tools="git").opencode_major == 1

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
    # Startup copies settings after validation. The copy is what later calls hold.
    settings = replace(settings, tools="git")
    assert settings.opencode_major == 1
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


def test_major_version_two_is_accepted_at_startup(tmp_path, monkeypatch):
    log, which = install_fake_opencode(tmp_path, monkeypatch, "opencode 2.2.5")
    settings = make(
        {"AGENT_A": "claude", "AGENT_B": "codex", "IMPL_AGENT": "opencode"}
    )

    validate_agents(settings, which=which)

    assert invocations(log) == [{"argv": ["--version"], "stdin": ""}]
    assert replace(settings, tools="git").opencode_major == 2


def _which_unused(name):
    return f"C:/unused/{name}"


@pytest.mark.parametrize("version", ["1.2.24", "2.2.5"])
def test_two_variant_values_fail_startup_on_either_product_line(version):
    settings = make(
        {
            "AGENT_A": "opencode",
            "AGENT_B": "codex",
            "AGENT_A_ARGS": "--variant low --variant=high",
        }
    )

    with pytest.raises(SettingsError, match="AGENT_A_ARGS") as exc_info:
        validate_agents(
            settings,
            which=_which_unused,
            opencode_version=lambda _path: version,
        )

    message = str(exc_info.value)
    assert "low" in message
    assert "high" in message


@pytest.mark.parametrize(
    "args",
    ["--variant", "--variant=", "--variant --thinking"],
)
@pytest.mark.parametrize("version", ["1.2.24", "2.0.0"])
def test_a_variant_flag_with_no_value_fails_startup(args, version):
    settings = make({"AGENT_B": "opencode", "AGENT_B_ARGS": args})

    with pytest.raises(SettingsError, match="AGENT_B_ARGS") as exc_info:
        validate_agents(
            settings,
            which=_which_unused,
            opencode_version=lambda _path: version,
        )

    assert "--variant" in str(exc_info.value)


@pytest.mark.parametrize("args", ["--variant low", "--variant=low"])
def test_one_variant_spelling_is_a_single_value(args):
    validate_agents(
        make({"AGENT_A": "opencode", "AGENT_A_ARGS": args}),
        which=_which_unused,
        opencode_version=lambda _path: "1.2.24",
    )


def test_opencode_1_forwards_one_variant_without_a_model(tmp_path, monkeypatch):
    log, which = install_fake_opencode(tmp_path, monkeypatch, "1.2.24")
    settings = make({"AGENT_A": "opencode", "AGENT_A_ARGS": "--variant low"})
    validate_agents(settings, which=which)
    io, _ = make_io(tmp_path)

    agents.run_worker(
        AgentRef("A", "opencode"), "prompt text", settings, AgentSession(), io
    )

    run = invocations(log)[-1]["argv"]
    assert "--variant" in run
    assert "low" in run
    assert "-m" not in run
    assert run[-1] == "prompt text"


def test_impl_variant_conflict_waits_until_every_owner_would_reject_it():
    both = make(
        {
            "AGENT_A": "opencode",
            "AGENT_B": "opencode",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--variant low --variant high",
        }
    )
    with pytest.raises(SettingsError, match="IMPL_ARGS") as exc_info:
        validate_agents(
            both,
            which=_which_unused,
            opencode_version=lambda _path: "2.2.5",
        )
    assert "low" in str(exc_info.value) and "high" in str(exc_info.value)

    mixed = make(
        {
            "AGENT_A": "opencode",
            "AGENT_B": "claude",
            "DUAL_SPEC": "1",
            "IMPL_ARGS": "--variant low --variant high",
        }
    )
    validate_agents(
        mixed,
        which=_which_unused,
        opencode_version=lambda _path: "1.2.24",
    )

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        agents.impl_ref(AgentRef("A", "opencode"), mixed)
    agents.impl_ref(AgentRef("B", "claude"), mixed)
