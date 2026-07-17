"""Phased plan parsing: headings, markers, and structure problems."""

import pytest

from adversarial_ai_coding.phases import Phase, PhasePlanError, parse_phases

VALID = """# Plan

Intro prose is allowed outside phases.

## Phase 1: feature works
Acceptance: the CLI prints the result.
- [ ] add the flag
- [ ] emit output

## Phase 2: old behavior unchanged (regression-guard)
Acceptance: output without the flag is unchanged.
- [ ] add regression fixture
"""


def _write(tmp_path, text):
    plan = tmp_path / "plan.md"
    plan.write_text(text, encoding="utf-8")
    return plan


def test_parses_phases_titles_guard_and_tasks(tmp_path):
    phases = parse_phases(_write(tmp_path, VALID))
    assert phases == (
        Phase(
            number=1,
            title="feature works",
            regression_guard=False,
            tasks=("add the flag", "emit output"),
        ),
        Phase(
            number=2,
            title="old behavior unchanged",
            regression_guard=True,
            tasks=("add regression fixture",),
        ),
    )


def test_missing_acceptance_line_is_a_problem(tmp_path):
    text = "## Phase 1: x\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="no 'Acceptance:' line"):
        parse_phases(_write(tmp_path, text))


def test_phase_without_tasks_is_a_problem(tmp_path):
    text = "## Phase 1: x\nAcceptance: y.\n"
    with pytest.raises(PhasePlanError, match="no '- \\[ \\] ' task"):
        parse_phases(_write(tmp_path, text))


def test_task_outside_any_phase_is_a_problem(tmp_path):
    text = "- [ ] stray\n## Phase 1: x\nAcceptance: y.\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="task outside any phase: stray"):
        parse_phases(_write(tmp_path, text))


def test_non_sequential_numbering_is_a_problem(tmp_path):
    text = (
        "## Phase 1: x\nAcceptance: y.\n- [ ] t\n"
        "## Phase 3: z\nAcceptance: y.\n- [ ] t\n"
    )
    with pytest.raises(PhasePlanError, match="found Phase 3, expected Phase 2"):
        parse_phases(_write(tmp_path, text))


def test_empty_title_is_a_problem(tmp_path):
    text = "## Phase 1: \nAcceptance: y.\n- [ ] t\n"
    with pytest.raises(PhasePlanError, match="empty title"):
        parse_phases(_write(tmp_path, text))


def test_no_headings_is_a_problem(tmp_path):
    with pytest.raises(PhasePlanError, match="no '## Phase N: <title>' headings"):
        parse_phases(_write(tmp_path, "# Plan\n\nprose only\n"))


def test_missing_file_is_a_problem(tmp_path):
    with pytest.raises(PhasePlanError, match="plan file not found"):
        parse_phases(tmp_path / "absent.md")


def test_all_problems_are_reported_together(tmp_path):
    text = "- [ ] stray\n## Phase 2: x\n"
    with pytest.raises(PhasePlanError) as excinfo:
        parse_phases(_write(tmp_path, text))
    message = str(excinfo.value)
    assert "task outside any phase" in message
    assert "expected Phase 1" in message
    assert "no 'Acceptance:' line" in message
