import json
import os
import sys
from pathlib import Path

import pytest

from workflow_harness import FAST_STARTUP

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

    rc, rendered, thread_id, quota_text = agents._run_codex_json(
        [sys.executable, str(emitter), str(FIXTURE)],
        io,
        agents.AgentRef("A", "codex"),
    )

    assert rc == 0
    assert thread_id == "11111111-1111-4111-8111-111111111111"
    assert io.raw_out.read_text(encoding="utf-8") == FIXTURE.read_text(encoding="utf-8")
    assert "Remember token BLUEBIRD." in rendered
    assert "You've hit your usage limit.\nPlease try again in 20s." in rendered
    assert "turn failed detail" in rendered
    assert "future.event" in rendered
    assert "stderr line that is not JSON" in rendered
    # Quota detection sees only the channels codex speaks through itself.
    assert is_rate_limited(quota_text)
    assert parse_reset_wait(quota_text, now=0) == 50
    assert "Remember token BLUEBIRD." not in quota_text
    assert "future.event" not in quota_text
    assert any("Remember token BLUEBIRD." in line for line in echoed)
    assert not any("future.event" in line for line in echoed)
    # The prefix marks the terminal line only; the rendered file stays clean.
    assert all(line.startswith("[A codex] ") for line in echoed)
    assert "[A codex]" not in io.agent_out.read_text(encoding="utf-8")


def test_codex_worker_uses_exact_thread_id_and_slot_model(monkeypatch, tmp_path):
    calls = []
    results = iter(
        [
            (0, "fresh", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", ""),
            (0, "resumed", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", ""),
        ]
    )

    def fake_run(argv, io, ref):
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


def test_worker_session_is_discarded_when_agent_ref_changes(monkeypatch, tmp_path):
    calls = []
    thread_ids = iter(
        [
            "11111111-1111-4111-8111-111111111111",
            "22222222-2222-4222-8222-222222222222",
            "33333333-3333-4333-8333-333333333333",
        ]
    )

    def fake_run(argv, io, ref):
        calls.append(argv)
        return 0, "ok", next(thread_ids), ""

    monkeypatch.setattr(agents, "_run_codex_json", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings(
        {
            "AGENT_A": "codex",
            "AGENT_B": "claude",
            "MODEL_A": "owner-model",
            "IMPL_MODEL": "implementation-model",
        }
    )
    owner = agents.AgentRef("A", "codex")
    implementation = agents.impl_ref(owner, s)
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()

    agents.run_worker(owner, "owner first", s, session, io)
    agents.run_worker(implementation, "implementation", s, session, io)
    agents.run_worker(owner, "owner again", s, session, io)

    old_ids = {
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    }
    assert all(call[:3] == ["codex", "exec", "--json"] for call in calls)
    assert all("resume" not in call for call in calls)
    assert all(not old_ids.intersection(call) for call in calls[1:])
    assert session.worker_session == "33333333-3333-4333-8333-333333333333"
    assert session.owner == owner


def test_fake_codex_impl_model_args_and_handoffs_use_real_argv(
    monkeypatch, tmp_path
):
    calls_path = tmp_path / "codex-calls.jsonl"
    emitter = tmp_path / "fake_codex.py"
    emitter.write_text(
        "import json, os, pathlib, sys\n"
        "calls_path = pathlib.Path(os.environ['FAKE_CODEX_CALLS'])\n"
        "previous = (calls_path.read_text(encoding='utf-8').splitlines() "
        "if calls_path.is_file() else [])\n"
        "args = sys.argv[1:]\n"
        "with calls_path.open('a', encoding='utf-8') as calls:\n"
        "    calls.write(json.dumps(args) + '\\n')\n"
        "thread_id = [\n"
        "    '11111111-1111-4111-8111-111111111111',\n"
        "    '22222222-2222-4222-8222-222222222222',\n"
        "    '33333333-3333-4333-8333-333333333333',\n"
        "    '44444444-4444-4444-8444-444444444444',\n"
        "][len(previous)]\n"
        "print(json.dumps({'type': 'thread.started', 'thread_id': thread_id}))\n"
        "print(json.dumps({\n"
        "    'type': 'item.completed',\n"
        "    'item': {'type': 'agent_message', 'text': 'fake codex completed'},\n"
        "}))\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "codex-bin"
    bin_dir.mkdir()
    if os.name == "nt":
        shim = bin_dir / "codex.cmd"
        shim.write_text(
            f'@"{sys.executable}" {FAST_STARTUP} "{emitter}" %*\r\n',
            encoding="utf-8",
        )
    else:
        shim = bin_dir / "codex"
        shim.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" {FAST_STARTUP} "{emitter}" "$@"\n',
            encoding="utf-8",
        )
        shim.chmod(0o755)
    monkeypatch.setenv(
        "PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    )
    monkeypatch.setenv("FAKE_CODEX_CALLS", str(calls_path))

    base_env = {
        "AGENT_A": "codex",
        "AGENT_B": "claude",
        "MODEL_A": "owner-model",
        "IMPL_MODEL": "implementation-model-v1",
        "IMPL_ARGS": '-c model_reasoning_effort="high" --skip-git-repo-check',
    }
    first_settings = settings(base_env)
    owner = agents.AgentRef("A", "codex")
    implementation = agents.impl_ref(owner, first_settings)
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()
    emitted_batches = []

    agents.run_worker(owner, "owner first", first_settings, session, io)
    emitted_batches.append(io.raw_out.read_text(encoding="utf-8").splitlines())
    agents.run_worker(
        implementation, "implementation first", first_settings, session, io
    )
    emitted_batches.append(io.raw_out.read_text(encoding="utf-8").splitlines())

    resumed_settings = settings(
        {**base_env, "IMPL_MODEL": "implementation-model-v2"}
    )
    resumed_implementation = agents.impl_ref(owner, resumed_settings)
    agents.run_worker(
        resumed_implementation,
        "implementation resumed",
        resumed_settings,
        session,
        io,
    )
    emitted_batches.append(io.raw_out.read_text(encoding="utf-8").splitlines())
    agents.run_worker(owner, "owner again", resumed_settings, session, io)
    emitted_batches.append(io.raw_out.read_text(encoding="utf-8").splitlines())

    recorded = [
        json.loads(line)
        for line in calls_path.read_text(encoding="utf-8").splitlines()
    ]
    assert Path(agents._resolve_argv0("codex")).parent == bin_dir
    assert all(
        [json.loads(line)["type"] for line in batch]
        == ["thread.started", "item.completed"]
        for batch in emitted_batches
    )

    owner_first, impl_first, impl_resumed, owner_again = recorded
    assert owner_first[:4] == ["exec", "--json", "--sandbox", "workspace-write"]
    assert impl_first[:4] == ["exec", "--json", "--sandbox", "workspace-write"]
    assert 'model="implementation-model-v1"' in impl_first
    assert "model_reasoning_effort=high" in impl_first
    assert "--skip-git-repo-check" in impl_first
    assert "resume" not in impl_first
    assert "11111111-1111-4111-8111-111111111111" not in impl_first

    assert impl_resumed[:3] == ["exec", "resume", "--json"]
    assert 'model="implementation-model-v2"' in impl_resumed
    assert 'model="implementation-model-v1"' not in impl_resumed
    assert "model_reasoning_effort=high" in impl_resumed
    assert "--skip-git-repo-check" in impl_resumed
    assert impl_resumed[-2:] == [
        "22222222-2222-4222-8222-222222222222",
        "implementation resumed",
    ]

    assert 'model="owner-model"' in owner_first
    assert owner_again[:4] == ["exec", "--json", "--sandbox", "workspace-write"]
    assert 'model="owner-model"' in owner_again
    assert "resume" not in owner_again
    assert "33333333-3333-4333-8333-333333333333" not in owner_again
    assert session.worker_session == "44444444-4444-4444-8444-444444444444"
    assert session.owner == owner


def test_codex_worker_keeps_known_id_when_response_has_no_id(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agents, "_run_codex_json", lambda argv, io, ref: (0, "ok", "", "")
    )
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings({"AGENT_A": "codex", "AGENT_B": "claude"})
    io, _ = make_io(tmp_path)
    ref = agents.AgentRef("A", "codex")
    session = agents.AgentSession(worker_session="keep-this-id", owner=ref)

    agents.run_worker(ref, "prompt", s, session, io)

    assert session.worker_session == "keep-this-id"


def test_codex_worker_without_id_stays_fresh_and_warns(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agents, "_run_codex_json", lambda argv, io, ref: (0, "ok", "", "")
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

    quota = "rate limit; try again in 0s"

    def fake_run(argv, io, ref):
        calls.append(argv)
        if len(calls) == 1:
            return 1, quota, "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", quota
        return 0, "ok", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", ""

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
    ref = agents.AgentRef("A", "codex")
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: None,
        log_retry=lambda message: None,
        notify=lambda message: None,
        sleep=lambda seconds: None,
    )

    result = agent_call(
        lambda: agents.run_worker(
            ref, "prompt", s, session, io
        ),

        settings=s,
        events=events,
        now=lambda: 0,
    )

    assert result.rc == 0
    assert calls[1][-2:] == ["bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "prompt"]
    assert session.owner == ref


def test_codex_same_agent_validation_and_reserved_args():
    same = settings({"AGENT_A": "codex", "AGENT_B": "codex"})
    agents.validate_agents(same, which=lambda name: "C:/fake/" + name)

    for value in (
        "--json",
        "resume",
        "--sandbox workspace-write",
        "--sandbox=workspace-write",
        "-s workspace-write",
        "-s=workspace-write",
        '-c sandbox_mode="read-only"',
        '--config sandbox_mode="read-only"',
        '--config=sandbox_mode="read-only"',
    ):
        invalid = settings({"CODEX_ARGS": value})
        with pytest.raises(SettingsError, match="CODEX_ARGS"):
            agents.validate_agents(invalid, which=lambda name: "C:/fake/" + name)

    for value in (
        "-c model_reasoning_effort=high",
        "--config model_reasoning_effort=high",
        "--config=model_reasoning_effort=high",
    ):
        allowed = settings({"CODEX_ARGS": value})
        agents.validate_agents(allowed, which=lambda name: "C:/fake/" + name)

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
            f'@echo off\r\n"{sys.executable}" {FAST_STARTUP} "{fake}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "codex"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" {FAST_STARTUP} "{fake}" "$@"\n',
            encoding="utf-8",
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


def test_parse_agy_conversation_id_is_strict_and_unambiguous(tmp_path):
    log = tmp_path / "agy.log"
    log.write_text(
        "Created conversation ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF\n"
        "Print mode: conversation=abcdefab-cdef-4abc-8def-abcdefabcdef\n",
        encoding="utf-8",
    )
    assert agents._parse_agy_conversation_id(log) == (
        "abcdefab-cdef-4abc-8def-abcdefabcdef"
    )

    log.write_text(
        "Created conversation 11111111-1111-4111-8111-111111111111\n"
        "Print mode: conversation=22222222-2222-4222-8222-222222222222\n",
        encoding="utf-8",
    )
    assert agents._parse_agy_conversation_id(log) == ""

    log.write_text(
        "Created conversation 11111111-1111-4111-8111-111111111111-extra\n"
        "Created conversation 11111111-1111-4111-8111-11111111111\n",
        encoding="utf-8",
    )
    assert agents._parse_agy_conversation_id(log) == ""
    assert agents._parse_agy_conversation_id(tmp_path / "missing.log") == ""


def test_parse_agy_conversation_id_requires_marker_at_line_start(tmp_path):
    log = tmp_path / "agy.log"
    log.write_text(
        "prompt echoed Created conversation "
        "11111111-1111-4111-8111-111111111111\n"
        "Created conversation 22222222-2222-4222-8222-222222222222\n",
        encoding="utf-8",
    )

    assert agents._parse_agy_conversation_id(log) == (
        "22222222-2222-4222-8222-222222222222"
    )


def test_agy_worker_uses_unique_logs_and_exact_conversation(monkeypatch, tmp_path):
    calls = []

    def fake_stream(argv, io, ref):
        calls.append(argv)
        log_path = Path(argv[argv.index("--log-file") + 1])
        if len(calls) == 1:
            log_path.write_text(
                "Created conversation 33333333-3333-4333-8333-333333333333\n",
                encoding="utf-8",
            )
        else:
            log_path.write_text("resume produced no id\n", encoding="utf-8")
        io.agent_out.write_text("ok\n", encoding="utf-8")
        return 0, "ok"

    monkeypatch.setattr(agents, "_run_streaming", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings({"AGENT_A": "agy", "AGENT_B": "claude", "MODEL_A": "agy-a"})
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()

    agents.run_worker(agents.AgentRef("A", "agy"), "first", s, session, io)
    first_log = Path(calls[0][calls[0].index("--log-file") + 1])
    agents.run_worker(agents.AgentRef("A", "agy"), "second", s, session, io)
    second_log = Path(calls[1][calls[1].index("--log-file") + 1])

    assert first_log != second_log
    assert "--continue" not in calls[0] and "--continue" not in calls[1]
    assert "--conversation" not in calls[0]
    conversation = calls[1].index("--conversation")
    assert calls[1][conversation + 1] == "33333333-3333-4333-8333-333333333333"
    assert session.worker_session == "33333333-3333-4333-8333-333333333333"
    assert io.raw_out == second_log


def test_agy_retry_resumes_id_captured_by_failed_attempt(monkeypatch, tmp_path):
    calls = []

    def fake_stream(argv, io, ref):
        calls.append(argv)
        log_path = Path(argv[argv.index("--log-file") + 1])
        if len(calls) == 1:
            log_path.write_text(
                "Created conversation 44444444-4444-4444-8444-444444444444\n",
                encoding="utf-8",
            )
            # agy has no event boundaries, so its whole output is the quota
            # channel: the adapter passes this text through as quota_text.
            return 1, "rate limit; try again in 0s"
        log_path.write_text("resumed\n", encoding="utf-8")
        return 0, "ok"

    monkeypatch.setattr(agents, "_run_streaming", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = settings(
        {
            "AGENT_A": "agy",
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
            agents.AgentRef("A", "agy"), "prompt", s, session, io
        ),

        settings=s,
        events=events,
        now=lambda: 0,
    )

    assert result.rc == 0
    index = calls[1].index("--conversation")
    assert calls[1][index + 1] == "44444444-4444-4444-8444-444444444444"


def test_agy_same_agent_validation_and_reserved_args():
    same = settings({"AGENT_A": "agy", "AGENT_B": "agy"})
    agents.validate_agents(same, which=lambda name: "C:/fake/" + name)

    for value in (
        "--log-file output.log",
        "--log-file=output.log",
        "--continue",
        "--continue=value",
        "--conversation value",
        "--conversation=value",
    ):
        invalid = settings({"AGY_ARGS": value})
        with pytest.raises(SettingsError, match="AGY_ARGS"):
            agents.validate_agents(invalid, which=lambda name: "C:/fake/" + name)


def test_fake_agy_keeps_worker_conversation_separate_from_reviewer(
    monkeypatch, tmp_path
):
    fake = tmp_path / "fake_agy.py"
    calls = tmp_path / "agy-calls.jsonl"
    fake.write_text(
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "prompt = args[args.index('--print') + 1]\n"
        "with open(os.environ['FAKE_AGY_CALLS'], 'a', encoding='utf-8') as out:\n"
        "    out.write(json.dumps(args) + '\\n')\n"
        "if '--log-file' in args:\n"
        "    log = pathlib.Path(args[args.index('--log-file') + 1])\n"
        "    if '--conversation' in args:\n"
        "        conversation = args[args.index('--conversation') + 1]\n"
        "    else:\n"
        "        conversation = '55555555-5555-4555-8555-555555555555'\n"
        "    log.write_text('Print mode: conversation=' + conversation + '\\n', "
        "encoding='utf-8')\n"
        "print('agy completed ' + prompt)\n",
        encoding="utf-8",
    )
    bin_dir = tmp_path / "agy-bin"
    bin_dir.mkdir()
    if os.name == "nt":
        wrapper = bin_dir / "agy.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{sys.executable}" {FAST_STARTUP} "{fake}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "agy"
        wrapper.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" {FAST_STARTUP} "{fake}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("FAKE_AGY_CALLS", str(calls))
    s = settings(
        {
            "AGENT_A": "agy",
            "AGENT_B": "agy",
            "MODEL_A": "agy-a",
            "MODEL_B": "agy-b",
        }
    )
    io, _ = make_io(tmp_path)
    session = agents.AgentSession()

    agents.run_worker(agents.AgentRef("A", "agy"), "worker one", s, session, io)
    agents.run_reviewer(agents.AgentRef("B", "agy"), "reviewer call", s, session, io)
    agents.run_worker(agents.AgentRef("A", "agy"), "worker two", s, session, io)

    recorded = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert recorded[0][recorded[0].index("--model") + 1] == "agy-a"
    assert recorded[1][recorded[1].index("--model") + 1] == "agy-b"
    assert "--conversation" not in recorded[1]
    index = recorded[2].index("--conversation")
    assert recorded[2][index + 1] == "55555555-5555-4555-8555-555555555555"
