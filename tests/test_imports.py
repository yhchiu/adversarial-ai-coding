"""Unit tests for import validation and preflight (IMPORT_SPEC/IMPORT_PLAN)."""

import pytest

from adversarial_ai_coding.config import Settings, SettingsError
from adversarial_ai_coding.imports import (
    import_preflight,
    validate_import_plan,
    validate_import_spec,
)

GOOD_SPEC = "# Spec\n\nBody.\n\n## Assumptions and Open Questions\n\n- none\n"
GOOD_PLAN = "# Plan\n\n- [ ] one task\n"
PHASED_PLAN = (
    "# Plan\n\n## Phase 1: works\nAcceptance: observable.\n- [ ] do it\n"
)


def _write(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_spec_validation(tmp_path):
    validate_import_spec(_write(tmp_path / "s.md", GOOD_SPEC))
    with pytest.raises(SettingsError, match="file not found"):
        validate_import_spec(tmp_path / "missing.md")
    with pytest.raises(SettingsError, match="empty"):
        validate_import_spec(_write(tmp_path / "e.md", "  \n"))
    with pytest.raises(SettingsError, match="Assumptions"):
        validate_import_spec(
            _write(tmp_path / "n.md", "# Spec\n\nno required section\n")
        )


def test_spec_heading_is_case_insensitive_and_order_free(tmp_path):
    validate_import_spec(
        _write(
            tmp_path / "s.md",
            "# Spec\n\n### OPEN QUESTIONS and assumptions\n\n- none\n",
        )
    )


def test_plan_validation_basic(tmp_path):
    validate_import_plan(_write(tmp_path / "p.md", GOOD_PLAN), phases=False)
    with pytest.raises(SettingsError, match="task line"):
        validate_import_plan(
            _write(tmp_path / "done.md", "# Plan\n\n- [x] already done\n"),
            phases=False,
        )


def test_plan_validation_phased(tmp_path):
    validate_import_plan(_write(tmp_path / "p.md", PHASED_PLAN), phases=True)
    with pytest.raises(SettingsError, match="phased plan"):
        validate_import_plan(_write(tmp_path / "b.md", GOOD_PLAN), phases=True)


def test_preflight_combination_rules(tmp_path):
    spec = _write(tmp_path / "s.md", GOOD_SPEC)

    env = {"IMPORT_PLAN": str(tmp_path / "p.md")}
    with pytest.raises(SettingsError, match="IMPORT_PLAN requires IMPORT_SPEC"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)

    env = {"IMPORT_REVIEW": "0"}
    with pytest.raises(SettingsError, match="IMPORT_REVIEW"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)

    env = {"IMPORT_SPEC": str(spec), "DUAL_SPEC": "1"}
    with pytest.raises(SettingsError, match="DUAL_SPEC"):
        import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)


def test_preflight_validates_files_only_on_fresh_runs(tmp_path):
    env = {"IMPORT_SPEC": str(tmp_path / "gone.md")}
    settings = Settings.from_env(env, run_id="r")
    with pytest.raises(SettingsError, match="file not found"):
        import_preflight(settings, env, fresh_run=True)
    import_preflight(settings, env, fresh_run=False)


def test_preflight_accepts_good_import(tmp_path):
    spec = _write(tmp_path / "s.md", GOOD_SPEC)
    plan = _write(tmp_path / "p.md", GOOD_PLAN)
    env = {"IMPORT_SPEC": str(spec), "IMPORT_PLAN": str(plan)}
    import_preflight(Settings.from_env(env, run_id="r"), env, fresh_run=True)
