"""Arming and side-file logic for the spec-gate Phased ATDD suggestion."""

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.phased_suggestion import (
    DEFAULT_SUGGESTION,
    read_suggestion,
    reset_suggestion,
    suggestion_armed,
    suggestion_path,
)


def settings_for(env):
    return Settings.from_env({"RETRY_ON_LIMIT": "0", **env}, run_id="t")


def test_suggestion_armed_matrix():
    assert suggestion_armed(settings_for({}))
    # Phased already on: nothing to suggest.
    assert not suggestion_armed(settings_for({"PHASES": "1"}))
    # Explicit opt-out: respect the user's decision.
    assert not suggestion_armed(settings_for({"PHASES": "0"}))
    # Imported plan cannot retroactively become a phased plan.
    assert not suggestion_armed(
        settings_for({"IMPORT_SPEC": "s.md", "IMPORT_PLAN": "p.md"})
    )
    # Imported spec alone leaves the plan AI-written: still armed.
    assert suggestion_armed(settings_for({"IMPORT_SPEC": "s.md"}))
    # HUMAN_GATE=0 stays armed: the reviewer judges, the gate only logs.
    assert suggestion_armed(settings_for({"HUMAN_GATE": "0"}))


def test_reset_writes_the_default_no_suggestion(tmp_path):
    reset_suggestion(tmp_path)
    assert suggestion_path(tmp_path).read_text(encoding="utf-8") == DEFAULT_SUGGESTION
    assert read_suggestion(tmp_path) == (False, "")


def test_read_suggestion_fails_open(tmp_path):
    assert read_suggestion(tmp_path) == (False, "")  # missing file
    cases = [
        "not json at all",
        "[true]",
        '{"phased": "yes"}',
        '{"reason": "no phased key"}',
        '{"phased": false, "reason": "explicitly not a fit"}',
        '{"phased": true, "reason": 5}',
    ]
    for text in cases:
        suggestion_path(tmp_path).write_text(text, encoding="utf-8")
        assert read_suggestion(tmp_path) == (False, ""), text


def test_read_suggestion_accepts_a_recommendation(tmp_path):
    suggestion_path(tmp_path).write_text(
        '{"phased": true, "reason": "two independent features"}',
        encoding="utf-8",
    )
    assert read_suggestion(tmp_path) == (True, "two independent features")
