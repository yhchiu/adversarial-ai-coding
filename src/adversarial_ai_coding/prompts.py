"""Workflow prompt templates.

Port of adversarial-ai-coding.sh:507-535 and the resource-path defaults at
1008-1012. Templates stay in resources/prompts as plain markdown; this
module only locates, reads, and renders them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

PACKAGE_DIR = Path(__file__).resolve().parent
# src/adversarial_ai_coding -> src -> repo root. The tool runs from a repo
# checkout (never released as a wheel), so this is the SCRIPT_DIR equivalent.
REPO_ROOT = PACKAGE_DIR.parents[1]


class PromptTemplateError(Exception):
    """A workflow prompt template is missing or unreadable."""


def default_prompts_dir(env: Mapping[str, str]) -> Path:
    resources = Path(env.get("RESOURCES_DIR") or REPO_ROOT / "resources")
    return Path(env.get("PROMPTS_DIR") or resources / "prompts")


def prompt_template_path(prompts_dir: Path, name: str) -> Path:
    return prompts_dir / f"{name}.md"


def read_prompt_template(prompts_dir: Path, name: str) -> str:
    path = prompt_template_path(prompts_dir, name)
    if not path.is_file():
        raise PromptTemplateError(
            f"(workflow prompt template not found:{path}; "
            "keep resources/prompts with the script or set PROMPTS_DIR)"
        )
    return path.read_text(encoding="utf-8")


def render_prompt(
    prompts_dir: Path, name: str, replacements: Mapping[str, str]
) -> str:
    # bash: command substitution strips the template's trailing newlines BEFORE
    # substitution; printf '%s\n' appends exactly one afterwards. Values keep
    # their own trailing newlines.
    text = read_prompt_template(prompts_dir, name).rstrip("\n")
    for key, value in replacements.items():
        text = text.replace("{{" + key + "}}", value)
    return text + "\n"


def prompt_file_instruction(artifact_path: str) -> str:
    return (
        "Read the full workflow prompt from this repository file "
        f"and follow it exactly: {artifact_path}\n"
    )


AGENTS_MARKER = "<!-- adversarial-ai-coding:begin -->"


def default_agents_template(env: Mapping[str, str]) -> Path:
    if env.get("AGENTS_TEMPLATE"):
        return Path(env["AGENTS_TEMPLATE"])
    resources = Path(env.get("RESOURCES_DIR") or REPO_ROOT / "resources")
    return resources / "AGENTS.template.md"


def write_agents_section(template: Path) -> str:
    if not template.is_file():
        raise PromptTemplateError(
            f"(AGENTS.md template not found:{template}; keep "
            "resources/AGENTS.template.md with the script or set AGENTS_TEMPLATE)"
        )
    return template.read_text(encoding="utf-8")


def bootstrap_agents_md(cwd: Path, template: Path, echo, echo_err) -> None:
    agents = cwd / "AGENTS.md"
    if agents.is_file():
        if AGENTS_MARKER not in agents.read_text(encoding="utf-8"):
            echo_err(
                "(note: AGENTS.md exists but does not include "
                "adversarial-ai-coding rules; run \"print-agents\" and "
                "merge them manually)"
            )
    else:
        try:
            agents.write_text(write_agents_section(template), encoding="utf-8")
        except PromptTemplateError as exc:
            echo_err(str(exc))
            return
        echo("Created AGENTS.md with adversarial-ai-coding cross-review rules.")
    claude = cwd / "CLAUDE.md"
    if not claude.is_file():
        claude.write_text(
            "Follow the adversarial-ai-coding cross-review rules in AGENTS.md.\n",
            encoding="utf-8",
        )
