"""Ports tests/helpers.test.sh:63-123 (engine helpers and adapters)."""

import json
import sys
from pathlib import Path

import pytest

from adversarial_ai_coding import agents
from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.agents import (
    VERDICT_SCHEMA,
    AgentIO,
    AgentRef,
    AgentResult,
    AgentSession,
    agent_model as _agent_model,
    generic_agent_args as _generic_agent_args,
    is_builtin_agent,
    notify,
    resolve_model_args as _resolve_model_args,
    run_reviewer as _run_reviewer,
    run_worker as _run_worker,
    validate_agents,
)


def make(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def ref_for_name(name, settings):
    slot = "A" if name == settings.agent_a else "B"
    return AgentRef(slot, name)


def agent_model(name, settings):
    return _agent_model(ref_for_name(name, settings), settings)


def resolve_model_args(name, settings):
    return _resolve_model_args(ref_for_name(name, settings), settings)


def generic_agent_args(name, settings):
    return _generic_agent_args(ref_for_name(name, settings), settings)


def run_worker(name, prompt, settings, session, io):
    return _run_worker(ref_for_name(name, settings), prompt, settings, session, io)


def run_reviewer(name, prompt, settings, session, io):
    return _run_reviewer(ref_for_name(name, settings), prompt, settings, session, io)


def test_is_builtin_agent():
    assert is_builtin_agent("claude")
    assert is_builtin_agent("codex")
    assert is_builtin_agent("agy")
    assert not is_builtin_agent("custom-agent")


def test_agent_model_slot_a_uses_model_a():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex", "MODEL_A": "haiku", "MODEL_B": "mini"})
    assert agent_model("claude", s) == "haiku"
    assert agent_model("codex", s) == "mini"


def test_agent_model_uses_slot_when_both_slots_share_agent_name():
    s = make(
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "MODEL_A": "gpt-a",
            "MODEL_B": "gpt-b",
        }
    )

    assert agents.agent_model(agents.AgentRef("A", "codex"), s) == "gpt-a"
    assert agents.agent_model(agents.AgentRef("B", "codex"), s) == "gpt-b"


def test_custom_agent_args_use_slot_when_names_match():
    s = make(
        {
            "AGENT_A": "wrapper",
            "AGENT_B": "wrapper",
            "AGENT_A_ARGS": "--profile a",
            "AGENT_B_ARGS": "--profile b",
        }
    )

    assert agents.resolve_model_args(agents.AgentRef("A", "wrapper"), s) == "--profile a"
    assert agents.resolve_model_args(agents.AgentRef("B", "wrapper"), s) == "--profile b"


def test_agent_model_unset_is_empty_for_cli_default():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    assert agent_model("claude", s) == ""


def test_agent_model_custom_agent_ignores_model_a():
    s = make({"AGENT_A": "custom-agent", "AGENT_B": "codex", "MODEL_A": "ignored",
              "AGENT_A_ARGS": "--model custom"})
    assert agent_model("custom-agent", s) == ""


def test_resolve_model_args_builtin_uses_cli_args():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex",
              "CLAUDE_ARGS": "--fast", "CODEX_ARGS": "-c model_reasoning_effort=low"})
    assert resolve_model_args("claude", s) == "--fast"
    assert resolve_model_args("codex", s) == "-c model_reasoning_effort=low"


def test_resolve_model_args_custom_agent_uses_slot_args():
    s = make({"AGENT_A": "custom-agent", "AGENT_B": "codex",
              "AGENT_A_ARGS": "--model custom --flag"})
    assert resolve_model_args("custom-agent", s) == "--model custom --flag"
    assert generic_agent_args("custom-agent", s) == "--model custom --flag"


def test_resolve_model_args_unknown_agent_is_empty():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    assert resolve_model_args("stranger", s) == ""
    assert generic_agent_args("stranger", s) == ""


def test_validate_agents_missing_command():
    s = make({"AGENT_A": "claude", "AGENT_B": "codex"})
    with pytest.raises(SettingsError, match="Missing required command:claude"):
        validate_agents(s, which=lambda name: None)


def test_validate_agents_same_codex_agent_allowed():
    s = make({"AGENT_A": "codex", "AGENT_B": "codex"})
    validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_same_custom_agent_rejected():
    s = make({"AGENT_A": "wrapper", "AGENT_B": "wrapper"})
    with pytest.raises(SettingsError, match="custom agent command wrapper"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


def test_validate_agents_both_claude_is_allowed():
    s = make({"AGENT_A": "claude", "AGENT_B": "claude"})
    validate_agents(s, which=lambda name: "C:/fake/" + name)  # must not raise


@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("CLAUDE_ARGS", {}),
        ("CODEX_ARGS", {}),
        ("AGY_ARGS", {"AGENT_A": "agy"}),
        ("AGENT_A_ARGS", {"AGENT_A": "custom-a"}),
        ("AGENT_B_ARGS", {"AGENT_B": "custom-b"}),
    ],
)
def test_validate_agents_rejects_unclosed_quotes(key, agent_env):
    s = make({**agent_env, key: '--flag "unterminated'})

    with pytest.raises(SettingsError, match=rf"{key}.*quoting"):
        validate_agents(s, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        (
            "CODEX_ARGS",
            '--config developer_instructions="mention --sandbox safely"',
        ),
        ("AGY_ARGS", '--append-system-prompt "mention --continue safely"'),
    ],
)
def test_validate_agents_ignores_reserved_words_inside_quoted_values(key, value):
    s = make({key: value})

    validate_agents(s, which=lambda name: "C:/fake/" + name)


def make_io(tmp_path, lines=None):
    sink = [] if lines is None else lines
    return AgentIO(
        agent_out=tmp_path / "agent-out.txt",
        raw_out=tmp_path / "agent-raw.txt",
        verdict_path=tmp_path / "verdict.json",
        echo=sink.append,
    ), sink


def test_verdict_schema_matches_bash():
    schema = json.loads(VERDICT_SCHEMA)
    assert schema["required"] == ["approved", "blockers", "suggestions"]
    assert schema["properties"]["approved"]["type"] == "boolean"


def test_generic_worker_passes_args_and_prompt_as_final_arg(tmp_path):
    # helpers.test.sh: "generic:w_generic passes args and prompt as final arg"
    capture = tmp_path / "generic-capture.txt"
    fake = tmp_path / "fake_agent.py"
    fake.write_text(
        "import sys, pathlib\n"
        f"cap = pathlib.Path(r'{capture}')\n"
        "lines = [f'argc={len(sys.argv) - 1}']\n"
        "lines += [f'arg{i}={a}' for i, a in enumerate(sys.argv[1:], 1)]\n"
        "cap.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')\n"
        "print('custom agent ran')\n",
        encoding="utf-8",
    )
    s = Settings.from_env(
        {
            "AGENT_A": sys.executable,
            "AGENT_A_ARGS": f"'{fake}' --flag \"two words\"",
            "AGENT_B": "codex",
        },
        run_id="r",
    )
    io, sink = make_io(tmp_path)
    session = AgentSession()
    result = run_worker(sys.executable, "hello prompt", s, session, io)
    assert result.rc == 0
    captured = capture.read_text(encoding="utf-8")
    # The interpreter consumes fake.py as sys.argv[0]. The custom agent sees
    # the POSIX-quoted slot args followed by the prompt as the final arg.
    assert "argc=3" in captured
    assert "arg1=--flag" in captured
    assert "arg2=two words" in captured
    assert "arg3=hello prompt" in captured
    assert io.agent_out.read_text(encoding="utf-8").strip() == "custom agent ran"
    assert sink and sink[-1].strip() == "custom agent ran"


def test_generic_worker_resolves_argv0_with_shutil_which(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(agents.shutil, "which", lambda name: "C:/resolved/fake.cmd")
    monkeypatch.setattr(
        agents,
        "_run_streaming",
        lambda argv, io: (calls.append(argv), (0, "ok"))[1],
    )
    settings = Settings.from_env(
        {"AGENT_A": "fake", "AGENT_B": "codex"}, run_id="r"
    )
    io, _ = make_io(tmp_path)
    run_worker("fake", "prompt", settings, AgentSession(), io)
    assert calls[0][0] == "C:/resolved/fake.cmd"


def test_non_codex_worker_removes_stale_cli_raw(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_streaming", lambda argv, io: (0, "ok"))
    s = Settings.from_env(
        {"AGENT_A": "custom-agent", "AGENT_B": "codex"}, run_id="r"
    )
    io, _ = make_io(tmp_path)
    io.raw_out.write_text("stale codex jsonl\n", encoding="utf-8")

    run_worker("custom-agent", "prompt", s, AgentSession(), io)

    assert not io.raw_out.exists()


def test_claude_worker_parses_json_and_tracks_session(monkeypatch, tmp_path):
    payload = json.dumps(
        {"session_id": "sess-1", "total_cost_usd": 0.42, "result": "did the work"}
    )
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({"TOOLS": "Bash(git *)"}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession()
    result = run_worker("claude", "prompt text", s, session, io)
    assert result.rc == 0
    assert result.text == "did the work"
    assert session.worker_session == "sess-1"
    assert session.last_cost == "0.42"
    assert json.loads(io.agent_out.read_text(encoding="utf-8")) == json.loads(payload)


def test_claude_worker_resumes_session_and_builds_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv):
        seen["argv"] = argv
        return (0, json.dumps({"session_id": "s2", "result": "ok"}))

    monkeypatch.setattr(agents, "_run_captured", fake_run)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_A": "haiku",
            "CLAUDE_ARGS": '--append-system-prompt "two words"',
            "TOOLS": "Bash(git *)",
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="prev-session")
    run_worker("claude", "the prompt", s, session, io)
    argv = seen["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert argv[2] == "the prompt"
    assert "--output-format" in argv and "json" in argv
    assert "--allowedTools" in argv
    assert argv[argv.index("--allowedTools") + 1] == "Bash(git *)"
    assert argv[argv.index("--model") + 1] == "haiku"
    prompt_index = argv.index("--append-system-prompt")
    assert argv[prompt_index + 1] == "two words"
    assert argv[argv.index("--resume") + 1] == "prev-session"


def test_claude_worker_failure_writes_agent_out_and_keeps_rc(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (2, "quota text"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_worker("claude", "p", s, AgentSession(), io)
    assert result.rc == 2
    assert io.agent_out.read_text(encoding="utf-8").strip() == "quota text"
    err = capsys.readouterr().err
    assert "quota text" in err
    assert "claude exited with code 2" in err


def test_claude_worker_invalid_json_success_keeps_session(monkeypatch, tmp_path):
    # Deliberate lenient divergence: bash's jq failures inside agent_call's
    # condition context degraded to empty values rather than aborting.
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, "not json at all"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="keep-me")
    result = run_worker("claude", "p", s, session, io)
    assert result.rc == 0
    assert result.text == "not json at all"
    assert session.worker_session == "keep-me"


def test_claude_worker_top_level_null_matches_bash(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, "null"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="old-session", last_cost="old-cost")

    result = run_worker("claude", "p", s, session, io)

    assert result == AgentResult(rc=0, text="")
    assert session == AgentSession(worker_session="null", last_cost="")
    assert io.agent_out.read_text(encoding="utf-8") == "null\n"


@pytest.mark.parametrize("payload", [[], "text", 0, True])
def test_claude_worker_non_object_json_matches_bash_jq_failure(
    monkeypatch, tmp_path, payload
):
    raw = json.dumps(payload)
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, raw))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="old-session", last_cost="old-cost")

    result = run_worker("claude", "p", s, session, io)

    assert result == AgentResult(rc=5, text="")
    assert session == AgentSession(worker_session="", last_cost="")
    assert io.agent_out.read_text(encoding="utf-8") == raw + "\n"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, "null"),
        ({"session_id": None}, "null"),
        ({"session_id": False}, "false"),
        ({"session_id": 0}, "0"),
        ({"session_id": True}, "true"),
        ({"session_id": "session"}, "session"),
        ({"session_id": {"part": 1}}, '{\n  "part": 1\n}'),
        ({"session_id": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_worker_session_id_uses_jq_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_captured", lambda argv: (0, json.dumps(payload))
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(worker_session="old-session")

    run_worker("claude", "p", s, session, io)

    assert session.worker_session == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"result": None}, ""),
        ({"result": False}, ""),
        ({"result": 0}, "0"),
        ({"result": True}, "true"),
        ({"result": "work"}, "work"),
        (
            {"result": {"done": [1, 2], "label": "完成"}},
            '{\n  "done": [\n    1,\n    2\n  ],\n  "label": "完成"\n}',
        ),
        ({"result": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_worker_result_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_captured", lambda argv: (0, json.dumps(payload))
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)

    result = run_worker("claude", "p", s, AgentSession(), io)

    assert result == AgentResult(rc=0, text=expected)


def test_codex_worker_fresh_then_resume_argv(monkeypatch, tmp_path):
    calls = []
    ids = iter(["thread-1", "thread-1"])

    def fake_stream(argv, io):
        calls.append(argv)
        return (0, "codex output", next(ids))

    monkeypatch.setattr(agents, "_run_codex_json", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "MODEL_B": "gpt-5.5",
            "AGENT_B": "codex",
            "CODEX_ARGS": (
                "-c model_reasoning_effort=low "
                "--config 'developer_instructions=\"two words\"'"
            ),
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession()
    run_worker("codex", "p1", s, session, io)
    assert session.worker_session == "thread-1"
    run_worker("codex", "p2", s, session, io)
    fresh, resumed = calls
    assert fresh[:5] == ["codex", "exec", "--json", "--sandbox", "workspace-write"]
    assert '-c' in fresh and 'model="gpt-5.5"' in fresh
    assert "model_reasoning_effort=low" in fresh
    assert 'developer_instructions="two words"' in fresh
    assert fresh[-1] == "p1"
    assert resumed[:4] == ["codex", "exec", "resume", "--json"]
    assert "--last" not in resumed
    assert 'sandbox_mode="workspace-write"' in resumed
    assert resumed[-2:] == ["thread-1", "p2"]


def test_agy_worker_conversation_flag(monkeypatch, tmp_path):
    calls = []

    def fake_stream(argv, io):
        calls.append(argv)
        log_path = Path(argv[argv.index("--log-file") + 1])
        log_path.write_text(
            "Created conversation 66666666-6666-4666-8666-666666666666\n",
            encoding="utf-8",
        )
        return 0, "x"

    monkeypatch.setattr(agents, "_run_streaming", fake_stream)
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env(
        {
            "AGENT_A": "agy",
            "AGENT_B": "codex",
            "AGY_ARGS": '--append-system-prompt "two words"',
        },
        run_id="r",
    )
    io, _ = make_io(tmp_path)
    session = AgentSession()
    run_worker("agy", "p1", s, session, io)
    assert session.worker_session == "66666666-6666-4666-8666-666666666666"
    run_worker("agy", "p2", s, session, io)
    assert "--continue" not in calls[0] and "--continue" not in calls[1]
    assert "--conversation" not in calls[0]
    index = calls[1].index("--conversation")
    assert calls[1][index + 1] == "66666666-6666-4666-8666-666666666666"
    assert calls[0][:3] == ["agy", "--print", "p1"]
    assert "--dangerously-skip-permissions" in calls[0]
    prompt_index = calls[0].index("--append-system-prompt")
    assert calls[0][prompt_index + 1] == "two words"


def test_claude_reviewer_writes_verdict_from_structured_output(monkeypatch, tmp_path):
    payload = json.dumps({
        "structured_output": {"approved": True, "blockers": [], "suggestions": ["s1"]},
        "total_cost_usd": 0.1,
        "result": "review text",
    })
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result.text == "review text"
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is True and verdict["suggestions"] == ["s1"]


def test_claude_reviewer_invalid_json_matches_bash_jq_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, "not json at all"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")
    result = run_reviewer("claude", "p", s, session, io)
    assert result == AgentResult(rc=5, text="")
    assert session.last_cost == ""
    assert io.agent_out.read_text(encoding="utf-8") == "not json at all\n"
    assert io.verdict_path.exists()
    assert io.verdict_path.stat().st_size == 0


def test_claude_reviewer_top_level_null_matches_bash(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, "null"))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    result = run_reviewer("claude", "p", s, session, io)

    assert result == AgentResult(rc=0, text="")
    assert session.last_cost == ""
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == {
        "approved": False,
        "blockers": ["reviewer did not produce a structured verdict"],
        "suggestions": [],
    }
    assert io.agent_out.read_text(encoding="utf-8") == "null\n"


@pytest.mark.parametrize("payload", [[], "text", 0, True])
def test_claude_reviewer_non_object_json_matches_bash_jq_failure(
    monkeypatch, tmp_path, payload
):
    raw = json.dumps(payload)
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, raw))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    result = run_reviewer("claude", "p", s, session, io)

    assert result == AgentResult(rc=5, text="")
    assert session.last_cost == ""
    assert io.verdict_path.exists()
    assert io.verdict_path.stat().st_size == 0
    assert io.agent_out.read_text(encoding="utf-8") == raw + "\n"


@pytest.mark.parametrize("structured_output", [{}, [], "", 0])
def test_claude_reviewer_preserves_jq_coalesce_non_null_non_false_values(
    monkeypatch, tmp_path, structured_output
):
    payload = json.dumps({"structured_output": structured_output, "result": "review"})
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result == AgentResult(rc=0, text="review")
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == structured_output


def test_claude_reviewer_missing_structured_output_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(agents, "_run_captured",
                        lambda argv: (0, json.dumps({"result": "no verdict"})))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, AgentSession(), io)
    verdict = json.loads(io.verdict_path.read_text(encoding="utf-8"))
    assert verdict["approved"] is False
    assert verdict["blockers"] == ["reviewer did not produce a structured verdict"]


@pytest.mark.parametrize("structured_output", [None, False])
def test_claude_reviewer_null_or_false_structured_output_uses_fallback(
    monkeypatch, tmp_path, structured_output
):
    payload = json.dumps({"structured_output": structured_output, "result": "review"})
    monkeypatch.setattr(agents, "_run_captured", lambda argv: (0, payload))
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    result = run_reviewer("claude", "p", s, AgentSession(), io)
    assert result == AgentResult(rc=0, text="review")
    assert json.loads(io.verdict_path.read_text(encoding="utf-8")) == {
        "approved": False,
        "blockers": ["reviewer did not produce a structured verdict"],
        "suggestions": [],
    }


@pytest.mark.parametrize("role", ["worker", "reviewer"])
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"total_cost_usd": None}, ""),
        ({"total_cost_usd": False}, ""),
        ({"total_cost_usd": 0}, "0"),
        ({"total_cost_usd": True}, "true"),
        ({"total_cost_usd": "0.50"}, "0.50"),
        (
            {"total_cost_usd": {"a": 1, "b": [2]}},
            '{\n  "a": 1,\n  "b": [\n    2\n  ]\n}',
        ),
        ({"total_cost_usd": [1, 2]}, "[\n  1,\n  2\n]"),
    ],
)
def test_claude_cost_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, role, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_captured", lambda argv: (0, json.dumps(payload))
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    session = AgentSession(last_cost="old-cost")

    if role == "worker":
        run_worker("claude", "p", s, session, io)
    else:
        run_reviewer("claude", "p", s, session, io)

    assert session.last_cost == expected


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({}, ""),
        ({"result": None}, ""),
        ({"result": False}, ""),
        ({"result": 0}, "0"),
        ({"result": True}, "true"),
        ({"result": "review"}, "review"),
        ({"result": {"approved": True}}, '{\n  "approved": true\n}'),
        ({"result": ["review"]}, '[\n  "review"\n]'),
    ],
)
def test_claude_reviewer_result_uses_jq_coalesce_and_raw_coercion(
    monkeypatch, tmp_path, payload, expected
):
    monkeypatch.setattr(
        agents, "_run_captured", lambda argv: (0, json.dumps(payload))
    )
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)

    result = run_reviewer("claude", "p", s, AgentSession(), io)

    assert result == AgentResult(rc=0, text=expected)


def test_claude_reviewer_argv_has_schema(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agents, "_run_captured",
                        lambda argv: (seen.update(argv=argv), (0, "{}"))[1])
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env({}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("claude", "p", s, AgentSession(), io)
    argv = seen["argv"]
    assert argv[argv.index("--json-schema") + 1] == VERDICT_SCHEMA


def test_agy_reviewer_uses_30m_timeout_and_no_continue(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(agents, "_run_streaming",
                        lambda argv, io: (seen.update(argv=argv), (0, "x"))[1])
    monkeypatch.setattr(agents.shutil, "which", lambda name: name)
    s = Settings.from_env({"AGENT_A": "agy", "AGENT_B": "codex"}, run_id="r")
    io, _ = make_io(tmp_path)
    run_reviewer("agy", "p", s, AgentSession(worker_session="continue"), io)
    argv = seen["argv"]
    assert "--print-timeout" in argv and "30m" in argv
    assert "--continue" not in argv  # reviewers always start fresh


def test_notify_noop_when_unset_and_warns_on_failure(tmp_path, capsys):
    s = Settings.from_env({}, run_id="r")
    notify(s, "hello")  # no NOTIFY_CMD: silent no-op
    s2 = Settings.from_env({"NOTIFY_CMD": "definitely-not-a-command-xyz"}, run_id="r")
    notify(s2, "hello")
    assert "notification command failed" in capsys.readouterr().err
