"""Ports tests/helpers.test.sh:125-171 (prompt handoff and templates)."""

import pytest

from adversarial_ai_coding.prompts import (
    REPO_ROOT,
    PromptTemplateError,
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
