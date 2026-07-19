"""Ports tests/helpers.test.sh:125-171 (prompt handoff and templates)."""

import pytest

from adversarial_ai_coding.prompts import (
    REPO_ROOT,
    PromptTemplateError,
    default_agents_template,
    default_prompts_dir,
    prompt_file_instruction,
    render_prompt,
)


def test_default_prompts_dir_lives_under_resources():
    # helpers.test.sh: "prompts:default directory lives under resources"
    assert default_prompts_dir({}) == REPO_ROOT / "resources" / "prompts"
    assert default_prompts_dir({}).is_dir()


def test_default_prompts_dir_env_overrides(tmp_path):
    assert default_prompts_dir({"PROMPTS_DIR": str(tmp_path)}) == tmp_path
    assert default_prompts_dir({"RESOURCES_DIR": str(tmp_path)}) == tmp_path / "prompts"


def test_render_prompt_replaces_placeholders(tmp_path):
    # helpers.test.sh: "prompts:render_prompt replaces placeholders"
    (tmp_path / "sample.md").write_text(
        "Hello {{NAME}}.\nPath: {{PATH}}\nMessage:\n{{MESSAGE}}\n", encoding="utf-8"
    )
    out = render_prompt(
        tmp_path,
        "sample",
        {"NAME": "worker", "PATH": "specs/run/spec.md", "MESSAGE": "line one\nline two"},
    )
    assert out == "Hello worker.\nPath: specs/run/spec.md\nMessage:\nline one\nline two\n"


def test_missing_template_fails_and_names_the_file(tmp_path):
    # helpers.test.sh: "prompts:missing template fails" + "names the file"
    with pytest.raises(PromptTemplateError, match=r"prompt template not found:.*missing\.md"):
        render_prompt(tmp_path, "missing", {})


def test_prompt_file_instruction_points_at_the_file():
    # helpers.test.sh: "prompt_file_instruction:points engine at prompt file"
    out = prompt_file_instruction(".workflow/runs/test/001-worker-prompt.md")
    assert "Read the full workflow prompt" in out
    assert ".workflow/runs/test/001-worker-prompt.md" in out


def test_real_repo_templates_render():
    # Every template shipped in resources/prompts must load through this module.
    prompts_dir = default_prompts_dir({})
    names = sorted(p.stem for p in prompts_dir.glob("*.md"))
    assert "review" in names
    for name in names:
        assert render_prompt(prompts_dir, name, {})


def test_value_trailing_newlines_survive_at_template_end(tmp_path):
    # bash strips the template before substitution, so a value's own trailing
    # newlines are preserved: printf adds exactly one more after them.
    (tmp_path / "t.md").write_text("Message:\n{{MESSAGE}}\n", encoding="utf-8")
    out = render_prompt(tmp_path, "t", {"MESSAGE": "x\n\n"})
    assert out == "Message:\nx\n\n\n"


PHASED_TEMPLATES = {
    "write-implementation-plan-phased": {
        "SPEC_FILE": "specfile.md",
        "PLAN_FILE": "planfile.md",
    },
    "review-scope-plan-phased": {
        "PLAN_FILE": "planfile.md",
        "SPEC_FILE": "specfile.md",
    },
    "phased-plan-invalid": {
        "PLAN_FILE": "planfile.md",
        "PROBLEMS": "- Phase 1 has no 'Acceptance:' line",
    },
    "write-phase-tests": {
        "SPEC_FILE": "specfile.md",
        "PLAN_FILE": "planfile.md",
        "SPEC_DIR": "specdir",
        "PHASE_TITLE": "phase-title",
        "PHASES_DONE": "phases-done",
        "PROTECTED_TESTS_FILE": "protectedfile.txt",
    },
    "phase-red-check-failed": {
        "COMMAND": "gate-command",
        "EXPECTED": "expected-text",
        "PHASE_TITLE": "phase-title",
        "OUTPUT": "tail-output",
    },
    "review-scope-phase": {
        "PHASE_TITLE": "phase-title",
        "PHASE_BASE": "base-sha",
        "PLAN_FILE": "planfile.md",
    },
}


@pytest.mark.parametrize("name", sorted(PHASED_TEMPLATES))
def test_phased_templates_render_every_placeholder(name):
    rendered = render_prompt(default_prompts_dir({}), name, PHASED_TEMPLATES[name])
    assert "{{" not in rendered
    for value in PHASED_TEMPLATES[name].values():
        assert value in rendered


def test_review_overwrite_rule_keeps_unreplied_findings():
    # AGENTS.template.md carries the full overwrite rule; the review prompt
    # must not drift into a plain "overwrite old content" that lets a
    # reviewer discard findings the worker has not answered yet.
    phrase = "keep items the worker has not replied to yet"
    prompt = render_prompt(
        default_prompts_dir({}), "review", {"SCOPE": "scope", "WF": ".workflow"}
    )
    agents = default_agents_template({}).read_text(encoding="utf-8")
    assert phrase in " ".join(prompt.split())
    assert phrase in " ".join(agents.split())


def test_branch_review_scopes_name_the_diff_base():
    # Without an explicit base the reviewer must guess which commit the
    # branch diff starts from; both whole-branch scopes must name it.
    prompts_dir = default_prompts_dir({})
    branch = render_prompt(
        prompts_dir,
        "review-scope-branch",
        {
            "BASE": "base-sha",
            "SPEC_FILE": "specfile.md",
            "PROTECTED_TESTS_FILE": "protected.txt",
        },
    )
    final = render_prompt(
        prompts_dir,
        "review-scope-final-acceptance",
        {"BASE": "base-sha", "SPEC_FILE": "specfile.md"},
    )
    assert "base-sha" in branch and "{{" not in branch
    assert "base-sha" in final and "{{" not in final


def test_verdict_rules_require_zero_blockers_for_approval():
    # The zero-blockers rule lives in AGENTS.md; both verdict-producing
    # prompts must repeat it so a reviewer cannot approve with blockers.
    prompts_dir = default_prompts_dir({})
    review = render_prompt(prompts_dir, "review", {"SCOPE": "s", "WF": "wf"})
    instruction = render_prompt(prompts_dir, "verdict-file-instruction", {"WF": "wf"})
    phrase = "only when there are zero blockers"
    assert phrase in " ".join(review.split())
    assert phrase in " ".join(instruction.split())


def test_implement_plan_task_marks_done_in_the_plan_file():
    # The prompt must reference the parameterized plan path, not assume
    # the file is literally named plan.md.
    out = render_prompt(
        default_prompts_dir({}),
        "implement-plan-task",
        {
            "PLAN_FILE": "PLANPATH.md",
            "TASK": "- [ ] do it",
            "PROTECTED_TESTS_FILE": "protected.txt",
        },
    )
    assert 'change this task in PLANPATH.md from "- [ ]" to "- [x]"' in out
    assert "plan.md" not in out


def test_quality_gate_repair_must_not_weaken_tests():
    out = render_prompt(
        default_prompts_dir({}),
        "quality-gate-failed",
        {"COMMAND": "gate-cmd", "OUTPUT": "boom"},
    )
    assert (
        "Do not make the gate pass by deleting, skipping, or weakening tests"
        in out
    )


def test_final_self_review_names_where_rejections_go():
    out = render_prompt(
        default_prompts_dir({}),
        "final-self-review",
        {"SUGGESTIONS_FILE": "SUGG.md"},
    )
    assert "writing a reason in SUGG.md under each suggestion you reject" in out


def test_write_phase_tests_first_line_is_stable():
    rendered = render_prompt(
        default_prompts_dir({}), "write-phase-tests", PHASED_TEMPLATES["write-phase-tests"]
    )
    assert rendered.startswith(
        'Write acceptance tests for exactly one phase of the plan: "phase-title"'
    )
