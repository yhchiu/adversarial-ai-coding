"""Ports helpers.test.sh:510-532 (AGENTS.md bootstrap)."""

import pytest

from adversarial_ai_coding.prompts import (
    AGENTS_END_MARKER,
    AGENTS_MARKER,
    PromptTemplateError,
    REPO_ROOT,
    bootstrap_agents_md,
    default_agents_template,
    managed_agents_section,
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


def make_template(tmp_path, body):
    path = tmp_path / "AGENTS.template.md"
    path.write_text(
        f"{AGENTS_MARKER}\n{body}\n{AGENTS_END_MARKER}\n", encoding="utf-8"
    )
    return path


def test_managed_section_needs_both_markers():
    text = write_agents_section(default_agents_template({}))
    section = managed_agents_section(text)
    assert section is not None
    assert section.startswith(AGENTS_MARKER)
    assert section.endswith(AGENTS_END_MARKER)
    assert managed_agents_section("no markers here") is None
    assert managed_agents_section(f"{AGENTS_MARKER}\nunterminated\n") is None
    # Line endings must not read as drift on Windows checkouts.
    assert managed_agents_section(text.replace("\n", "\r\n")) == section


def test_bootstrap_reports_outdated_rules(tmp_path):
    """An AGENTS.md written by an older version must not go unnoticed."""

    template = make_template(tmp_path, "rule one\nrule two")
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        f"# My project\n\n{AGENTS_MARKER}\nrule one\n{AGENTS_END_MARKER}\n",
        encoding="utf-8",
    )
    before = agents.read_text(encoding="utf-8")
    out, err = sinks()

    bootstrap_agents_md(tmp_path, template, out.append, err.append)

    assert agents.read_text(encoding="utf-8") == before
    assert any("out of date" in line for line in err)


def test_bootstrap_is_quiet_when_rules_are_current(tmp_path):
    template = make_template(tmp_path, "rule one\nrule two")
    section = managed_agents_section(template.read_text(encoding="utf-8"))
    # The user's own prose around the managed block is not drift.
    (tmp_path / "AGENTS.md").write_text(
        f"# My project\n\n{section}\n\n## My own rules\n", encoding="utf-8"
    )
    out, err = sinks()

    bootstrap_agents_md(tmp_path, template, out.append, err.append)

    assert err == []


def test_bootstrap_cannot_compare_against_an_undelimited_template(tmp_path):
    """A template without an end marker gives nothing to compare; stay quiet."""

    template = tmp_path / "AGENTS.template.md"
    template.write_text(f"{AGENTS_MARKER}\nrule one\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(
        f"{AGENTS_MARKER}\nanything\n{AGENTS_END_MARKER}\n", encoding="utf-8"
    )
    out, err = sinks()

    bootstrap_agents_md(tmp_path, template, out.append, err.append)

    assert err == []


def test_bootstrap_reports_a_missing_template_over_existing_agents(tmp_path):
    (tmp_path / "AGENTS.md").write_text(
        f"{AGENTS_MARKER}\nrule one\n{AGENTS_END_MARKER}\n", encoding="utf-8"
    )
    out, err = sinks()

    bootstrap_agents_md(
        tmp_path, tmp_path / "nonexistent-template", out.append, err.append
    )

    assert any("AGENTS.md template not found" in line for line in err)
    assert not (tmp_path / "CLAUDE.md").exists()


def test_bootstrap_missing_template_leaves_no_empty_files(tmp_path):
    out, err = sinks()
    bootstrap_agents_md(
        tmp_path, tmp_path / "nonexistent-template", out.append, err.append
    )
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "CLAUDE.md").exists()
