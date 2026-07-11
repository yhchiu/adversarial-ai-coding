"""Ports helpers.test.sh:510-532 (AGENTS.md bootstrap)."""

import pytest

from adversarial_ai_coding.prompts import (
    AGENTS_MARKER,
    PromptTemplateError,
    REPO_ROOT,
    bootstrap_agents_md,
    default_agents_template,
    write_agents_section,
)


def test_default_template_lives_under_resources():
    assert default_agents_template({}) == REPO_ROOT / "resources" / "AGENTS.template.md"
    assert default_agents_template({}).is_file()
    assert default_agents_template({"AGENTS_TEMPLATE": "X"}).name == "X"


def test_write_agents_section_has_marker():
    text = write_agents_section(default_agents_template({}))
    assert AGENTS_MARKER in text


def test_write_agents_section_missing_template_fails():
    with pytest.raises(PromptTemplateError, match="AGENTS.md template not found"):
        write_agents_section(
            default_agents_template({"AGENTS_TEMPLATE": "/nonexistent"})
        )


def sinks():
    out, err = [], []
    return out, err


def test_bootstrap_creates_agents_and_claude_md(tmp_path):
    out, err = sinks()
    bootstrap_agents_md(tmp_path, default_agents_template({}), out.append, err.append)
    assert AGENTS_MARKER in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert any("Created AGENTS.md" in line for line in out)


def test_bootstrap_does_not_overwrite_existing(tmp_path):
    (tmp_path / "AGENTS.md").write_text("my own rules\n", encoding="utf-8")
    out, err = sinks()
    bootstrap_agents_md(tmp_path, default_agents_template({}), out.append, err.append)
    assert (tmp_path / "AGENTS.md").read_text(encoding="utf-8") == "my own rules\n"
    assert any("merge them manually" in line for line in err)


def test_bootstrap_missing_template_leaves_no_empty_files(tmp_path):
    out, err = sinks()
    bootstrap_agents_md(
        tmp_path, tmp_path / "nonexistent-template", out.append, err.append
    )
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
