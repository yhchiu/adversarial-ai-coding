"""Phased plan parsing (Phased ATDD, PHASES=1).

plan.md is split into "## Phase N: <title>" sections. Each phase needs an
observable "Acceptance:" line and at least one "- [ ] " task. A trailing
"(regression-guard)" on the title flips the red-check expectation: those
tests must pass immediately instead of starting red.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

TASK_PREFIX = "- [ ] "
_PHASE_HEADING = re.compile(
    r"^## Phase (?P<number>\d+):(?P<title>.*?)(?P<guard>\(regression-guard\))?\s*$"
)


class PhasePlanError(Exception):
    """plan.md does not have a usable phased structure."""


@dataclass(frozen=True)
class Phase:
    number: int
    title: str
    regression_guard: bool
    tasks: tuple[str, ...]


def parse_phases(plan_path: Path) -> tuple[Phase, ...]:
    if not plan_path.is_file():
        raise PhasePlanError(f"plan file not found: {plan_path}")
    problems: list[str] = []
    phases: list[Phase] = []
    current: dict | None = None

    def close(section: dict | None) -> None:
        if section is None:
            return
        if not section["title"]:
            problems.append(f"Phase {section['number']} has an empty title")
        if not section["acceptance"]:
            problems.append(
                f"Phase {section['number']} has no 'Acceptance:' line"
            )
        if not section["tasks"]:
            problems.append(f"Phase {section['number']} has no '- [ ] ' task")
        phases.append(
            Phase(
                number=section["number"],
                title=section["title"],
                regression_guard=section["guard"],
                tasks=tuple(section["tasks"]),
            )
        )

    for line in plan_path.read_text(encoding="utf-8").splitlines():
        heading = _PHASE_HEADING.match(line)
        if heading:
            close(current)
            number = int(heading.group("number"))
            expected = len(phases) + 1
            if number != expected:
                problems.append(
                    "Phase numbering must be sequential: "
                    f"found Phase {number}, expected Phase {expected}"
                )
            current = {
                "number": number,
                "title": heading.group("title").strip(),
                "guard": heading.group("guard") is not None,
                "acceptance": False,
                "tasks": [],
            }
            continue
        if line.startswith(TASK_PREFIX):
            text = line[len(TASK_PREFIX) :]
            if current is None:
                problems.append(f"task outside any phase: {text}")
            elif not text.strip():
                problems.append(
                    f"Phase {current['number']} has an empty '- [ ] ' task"
                )
            else:
                current["tasks"].append(text)
            continue
        if line.startswith("Acceptance:") and current is not None:
            if line[len("Acceptance:") :].strip():
                current["acceptance"] = True
            else:
                problems.append(
                    f"Phase {current['number']} has an empty 'Acceptance:' line"
                )
    close(current)
    if not phases and not problems:
        problems.append("no '## Phase N: <title>' headings found")
    if problems:
        raise PhasePlanError(
            "plan.md is not a valid phased plan:\n"
            + "".join(f"- {problem}\n" for problem in problems)
        )
    return tuple(phases)
