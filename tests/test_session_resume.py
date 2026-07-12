import json
import os
import sys
from pathlib import Path

import pytest

from adversarial_ai_coding import agents
from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.ratelimit import RetryEvents, agent_call, is_rate_limited, parse_reset_wait


FIXTURE = Path(__file__).parent / "fixtures" / "codex_exec_rate_limit.jsonl"


def make_io(tmp_path):
    echoed = []
    return (
        agents.AgentIO(
            agent_out=tmp_path / "agent-out.txt",
            raw_out=tmp_path / "agent-raw.jsonl",
            verdict_path=tmp_path / "verdict.json",
            echo=echoed.append,
        ),
        echoed,
    )


def settings(env=None):
    return Settings.from_env(env or {}, run_id="r")


def test_codex_json_preserves_raw_and_renders_readable_quota_text(tmp_path):
    emitter = tmp_path / "emit_fixture.py"
    emitter.write_text(
        "import pathlib, sys\n"
        "sys.stdout.write(pathlib.Path(sys.argv[1]).read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    io, echoed = make_io(tmp_path)

    rc, rendered, thread_id = agents._run_codex_json(
        [sys.executable, str(emitter), str(FIXTURE)], io
    )

    assert rc == 0
    assert thread_id == "11111111-1111-4111-8111-111111111111"
    assert io.raw_out.read_text(encoding="utf-8") == FIXTURE.read_text(encoding="utf-8")
    assert "Remember token BLUEBIRD." in rendered
    assert "You've hit your usage limit.\nPlease try again in 20s." in rendered
    assert "turn failed detail" in rendered
    assert "future.event" in rendered
    assert "stderr line that is not JSON" in rendered
    assert is_rate_limited(io.agent_out)
    assert parse_reset_wait(io.agent_out, now=0) == 50
    assert any("Remember token BLUEBIRD." in line for line in echoed)
    assert not any("future.event" in line for line in echoed)


def test_codex_worker_uses_exact_thread_id_and_slot_model(monkeypatch, tmp_path):
    calls = []
    results = iter(
        [
            (0, "fresh", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            (0, "resumed", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        ]
    )

    def fake_run(argv, io):
        calls.append(argv)
        return next(results)

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "MODEL_A": "gpt-a",
            "MODEL_B": "gpt-b",
            "CODEX_ARGS": "-c model_reasoning_effort=high",
        }
    )
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()

    agents.run_worker(agents.AgentRef("B", "codex"), "first", s, session, io)
    agents.run_worker(agents.AgentRef("B", "codex"), "second", s, session, io)

    fresh, resumed = calls
    assert fresh[:5] == ["codex", "exec", "--json", "--sandbox", "workspace-write"]
    assert 'model="gpt-b"' in fresh and fresh[-1] == "first"
    assert resumed[:4] == ["codex", "exec", "resume", "--json"]
    assert "--last" not in resumed
    assert resumed[-2:] == ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "second"]
    assert session.worker_session == "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def test_codex_worker_keeps_known_id_when_response_has_no_id(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agents, "_run_codex_json", lambda argv, io: (0, "ok", "")
    )
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings({"AGENT_A": "codex", "AGENT_B": "claude"})
    io, _ = make_io(tmp_path)
    session = agents.AgentSession(worker_session="keep-this-id")

    agents.run_worker(agents.AgentRef("A", "codex"), "prompt", s, session, io)

    assert session.worker_session == "keep-this-id"


def test_codex_worker_without_id_stays_fresh_and_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agents, "_run_codex_json", lambda argv, io: (0, "ok", "")
    )
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings({"AGENT_A": "codex", "AGENT_B": "claude"})
    io, echoed = make_io(tmp_path)
    session = agents.AgentSession()

    agents.run_worker(agents.AgentRef("A", "codex"), "prompt", s, session, io)

    assert session.worker_session == ""
    assert any("next worker call will start a fresh session" in line for line in echoed)


def test_codex_retry_resumes_id_captured_by_failed_attempt(monkeypatch, tmp_path):
    calls = []

    def fake_run(argv, io):
        calls.append(argv)
        if len(calls) == 1:
            io.agent_out.write_text("rate limit; try again in 0s\n", encoding="utf-8")
            return 1, "rate limit", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        io.agent_out.write_text("ok\n", encoding="utf-8")
        return 0, "ok", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "RETRY_BASE_WAIT": "0",
            "RETRY_MAX": "1",
        }
    )
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: None,
        log_retry=lambda message: None,
        notify=lambda message: None,
        sleep=lambda seconds: None,
    )

    result = agent_call(
        lambda: agents.run_worker(
            agents.AgentRef("A", "codex"), "prompt", s, session, io
        ),
        agent_out=io.agent_out,
        settings=s,
        events=events,
        now=lambda: 0,
    )

    assert result.rc == 0
    assert calls[1][-2:] == ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "prompt"]


def test_codex_same_agent_validation_and_reserved_args():
    same = settings({"AGENT_A": "codex", "AGENT_B": "codex"})
    agents.validate_agents(same, which=lambda name: "C:/fake/" + name)

    for value in ("--json", "resume", "--sandbox workspace-write", '-c sandbox_mode="read-only"'):
        invalid = settings({"CODEX_ARGS": value})
        with pytest.raises(SettingsError, match="CODEX_ARGS"):
            agents.validate_agents(invalid, which=lambda name: "C:/fake/" + name)

    allowed = settings({"CODEX_ARGS": "-c model_reasoning_effort=high"})
    agents.validate_agents(allowed, which=lambda name: "C:/fake/" + name)

    same_agy = settings({"AGENT_A": "agy", "AGENT_B": "agy"})
    with pytest.raises(SettingsError, match="cannot both use agy"):
        agents.validate_agents(same_agy, which=lambda name: "C:/fake/" + name)


def test_fake_codex_keeps_worker_thread_separate_from_reviewer(monkeypatch, tmp_path):
    fake = tmp_path / "fake_codex.py"
    calls = tmp_path / "calls.jsonl"
    fake.write_text(
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = args[-1]\n"
        "if 'resume' in args:\n"
        "    thread_id = args[-2]\n"
        "elif 'reviewer' in prompt:\n"
        "    thread_id = '22222222-2222-4222-8222-222222222222'\n"
        "else:\n"
        "    thread_id = '11111111-1111-4111-8111-111111111111'\n"
        "with open(os.environ['FAKE_CODEX_CALLS'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}))\n"
        "print(json.dumps({'type': 'item.completed', 'item': "
        "{'type': 'agent_message', 'text': thread_id}}))\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if os.name == "nt":
        wrapper = bin_dir / "codex.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" "{fake}" %*\r\n', encoding="utf-8"
        )
    else:
        wrapper = bin_dir / "codex"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{fake}" "$@"\n', encoding="utf-8"
        )
        wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_CODEX_CALLS", str(calls))
    s = settings(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "MODEL_A": "gpt-a",
            "MODEL_B": "gpt-b",
        }
    )
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()

    first = agents.run_worker(agents.AgentRef("A", "codex"), "worker one", s, session, io)
    review = agents.run_reviewer(
        agents.AgentRef("B", "codex"), "reviewer call", s, session, io
    )
    second = agents.run_worker(agents.AgentRef("A", "codex"), "worker two", s, session, io)

    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert first.text.endswith("11111111-1111-4111-8111-111111111111")
    assert review.text.endswith("22222222-2222-4222-8222-222222222222")
    assert second.text.endswith("11111111-1111-4111-8111-111111111111")
    assert 'model="gpt-a"' in recorded[0]
    assert 'model="gpt-b"' in recorded[1]
    assert "resume" not in recorded[1]
    assert recorded[2][-2] == "11111111-1111-4111-8111-111111111111"
