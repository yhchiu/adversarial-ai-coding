"""Cross-platform fake agent for the resume integration suite."""

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path


def classify(prompt: str) -> str:
    if prompt.startswith("You are a strict code reviewer.") and (
        "after dual spec selection" in prompt
    ):
        return "review-dual-final"
    if prompt.startswith("You are a strict code reviewer."):
        return "review"
    if prompt.startswith("Write a spec for the following request"):
        return "write-spec"
    if prompt.startswith("Write an independent candidate spec"):
        return "write-candidate"
    if prompt.startswith("Write an implementation plan"):
        return "write-plan"
    if prompt.startswith("Write acceptance tests for exactly one phase"):
        return "write-phase-tests"
    if prompt.startswith("Write acceptance tests"):
        return "write-acceptance"
    if prompt.startswith("Implement this task from"):
        return "implement"
    if prompt.startswith("Compare the dual spec candidates"):
        return "compare"
    if prompt.startswith("Do a complete self-review"):
        return "final-review"
    if "is complete and approved. Commit all current changes" in prompt:
        return "commit"
    return "other"


def main() -> int:
    args = sys.argv[1:]
    name = "fake-agent"
    if "--role" in args:
        index = args.index("--role")
        name = args[index + 1]
        del args[index : index + 2]
    last = args[-1] if args else ""
    prompt = last
    match = re.search(r"follow it exactly: (.+)$", last.strip())
    if match and Path(match.group(1)).is_file():
        prompt = Path(match.group(1)).read_text(encoding="utf-8")

    kind = classify(prompt)
    log = os.environ.get("FAKE_CALLS_LOG", "calls.log")
    with open(log, "a", encoding="utf-8") as calls_log:
        calls_log.write(f"{name} {kind}\n")

    implementation_tasks_log = os.environ.get("FAKE_IMPLEMENTATION_TASKS_LOG", "")
    if kind == "implement" and implementation_tasks_log:
        first_line = prompt.splitlines()[0]
        task_match = re.match(
            r"^Implement this task from .*[\\/]plan\.md:(.+)$", first_line
        )
        if task_match:
            with open(
                implementation_tasks_log, "a", encoding="utf-8"
            ) as tasks_log:
                tasks_log.write(f"{name} {task_match.group(1)}\n")

    abort_on = os.environ.get("FAKE_ABORT_ON", "")
    abort_on_nth = os.environ.get("FAKE_ABORT_ON_NTH", "")
    matching_calls = sum(
        1
        for line in Path(log).read_text(encoding="utf-8").splitlines()
        if line == f"{name} {kind}"
    )
    if (
        abort_on
        and Path(abort_on).is_file()
        and Path(abort_on).read_text(encoding="utf-8").strip() == kind
        and (not abort_on_nth or matching_calls == int(abort_on_nth))
    ):
        if os.environ.get("FAKE_ABORT_MODE", "quota") == "plain":
            print("fake agent plain failure")
            return 1
        reset_at = (datetime.now() + timedelta(days=2)).strftime(
            "%b %d, %Y %I:%M %p"
        )
        print(f"You've hit your usage limit. Please try again at {reset_at}.")
        return 1

    def grep_target(pattern: str) -> str:
        # Workflow prompts contain absolute native paths. Accept both slash
        # styles and keep the regex anchored at the specs path component.
        target = re.search(r"(?:[A-Za-z]:)?[^ \r\n]*" + pattern, prompt)
        return target.group(0) if target else ""

    if kind in ("review", "review-dual-final"):
        Path(".workflow").mkdir(exist_ok=True)
        Path(".workflow/review.md").write_text(
            f"approved by {name}\n", encoding="utf-8"
        )
        Path(".workflow/verdict.json").write_text(
            '{"approved":true,"blockers":[],"suggestions":[]}\n',
            encoding="utf-8",
        )
    elif kind == "write-spec":
        target = grep_target(r"specs[\\/][^ \r\n]+[\\/]spec\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(
            "# Spec\n\nDemo feature.\n\n## Assumptions and Open Questions\n\n- none\n",
            encoding="utf-8",
        )
    elif kind == "write-candidate":
        target = grep_target(r"specs[\\/][^ \r\n]+[\\/]spec-[ab]\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(f"# Candidate spec {target}\n", encoding="utf-8")
    elif kind == "write-plan":
        target = grep_target(r"specs[\\/][^ \r\n]+[\\/]plan\.md")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        if '"## Phase N: <title>"' in prompt:
            Path(target).write_text(
                "# Plan\n\n"
                "## Phase 1: feature works\n"
                "Acceptance: src.txt records the implementation.\n"
                "- [ ] add feature one\n"
                "- [ ] add feature two\n\n"
                "## Phase 2: old behavior unchanged (regression-guard)\n"
                "Acceptance: base.txt still says base.\n"
                "- [ ] add regression fixture\n",
                encoding="utf-8",
            )
        else:
            Path(target).write_text(
                "# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n",
                encoding="utf-8",
            )
    elif kind == "write-acceptance":
        Path("acc").mkdir(exist_ok=True)
        Path("acc/acceptance.txt").write_text(
            "ACCEPTANCE CHECK\n", encoding="utf-8"
        )
    elif kind == "write-phase-tests":
        Path("acc").mkdir(exist_ok=True)
        title_match = re.search(r'one phase of the plan: "(.+?)"', prompt)
        title = title_match.group(1) if title_match else "phase"
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        Path(f"acc/{slug}.txt").write_text("PHASE CHECK\n", encoding="utf-8")
    elif kind == "implement":
        with open("src.txt", "a", encoding="utf-8") as source:
            source.write("implemented\n")
    elif kind == "compare":
        target = grep_target(
            r"specs[\\/][^ \r\n]+[\\/]spec-comparison-[ab]\.md"
        )
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text("comparison table\n", encoding="utf-8")

    print(f"{name} did {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
