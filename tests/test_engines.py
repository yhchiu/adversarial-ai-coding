"""Ports tests/helpers.test.sh:63-95 (engine_model, aliases, builtin checks)."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.engines import (
    engine_model,
    generic_engine_args,
    is_builtin_engine,
    resolve_model_args,
    validate_engines,
)


def make(env=None):
    return Settings.from_env(env or {}, run_id="20260711-000000")


def test_is_builtin_engine():
    assert is_builtin_engine("claude")
    assert is_builtin_engine("codex")
    assert is_builtin_engine("agy")
    assert not is_builtin_engine("custom-agent")


def test_engine_model_slot_a_uses_model_a():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex", "MODEL_A": "haiku", "MODEL_B": "mini"})
    assert engine_model("claude", s) == "haiku"
    assert engine_model("codex", s) == "mini"


def test_engine_model_unset_is_empty_for_cli_default():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    assert engine_model("claude", s) == ""


def test_engine_model_custom_engine_ignores_model_a():
    s = make({"ENGINE_A": "custom-agent", "ENGINE_B": "codex", "MODEL_A": "ignored",
              "ENGINE_A_ARGS": "--model custom"})
    assert engine_model("custom-agent", s) == ""


def test_resolve_model_args_builtin_uses_cli_args():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex",
              "CLAUDE_ARGS": "--fast", "CODEX_ARGS": "-c model_reasoning_effort=low"})
    assert resolve_model_args("claude", s) == "--fast"
    assert resolve_model_args("codex", s) == "-c model_reasoning_effort=low"


def test_resolve_model_args_custom_engine_uses_slot_args():
    s = make({"ENGINE_A": "custom-agent", "ENGINE_B": "codex",
              "ENGINE_A_ARGS": "--model custom --flag"})
    assert resolve_model_args("custom-agent", s) == "--model custom --flag"
    assert generic_engine_args("custom-agent", s) == "--model custom --flag"


def test_resolve_model_args_unknown_engine_is_empty():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    assert resolve_model_args("stranger", s) == ""
    assert generic_engine_args("stranger", s) == ""


def test_validate_engines_missing_command():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "codex"})
    with pytest.raises(SettingsError, match="Missing required command:claude"):
        validate_engines(s, which=lambda name: None)


def test_validate_engines_same_builtin_engine_rejected():
    s = make({"ENGINE_A": "codex", "ENGINE_B": "codex"})
    with pytest.raises(SettingsError, match="cannot both use codex"):
        validate_engines(s, which=lambda name: "C:/fake/" + name)


def test_validate_engines_same_custom_engine_rejected():
    s = make({"ENGINE_A": "wrapper", "ENGINE_B": "wrapper"})
    with pytest.raises(SettingsError, match="custom engine command wrapper"):
        validate_engines(s, which=lambda name: "C:/fake/" + name)


def test_validate_engines_both_claude_is_allowed():
    s = make({"ENGINE_A": "claude", "ENGINE_B": "claude"})
    validate_engines(s, which=lambda name: "C:/fake/" + name)  # must not raise
