"""DUAL_SPEC mode: independent candidates, cross review, human selection.

Port of adversarial-ai-coding.sh:851-1003 and 1485-1697. All eight
sub-stages go through begin_stage/end_stage so resume skips paid work; the
human decision is restored from spec-decision.md on resume (C5).
"""

from __future__ import annotations

from pathlib import Path

from .agents import AgentRef
from .config import WorkflowAbort
from .prompts import render_prompt
from .review import review_loop, run_review
from .workflow import (
    WorkflowContext,
    begin_stage,
    end_stage,
    human_gate_spec,
    set_spec_roles_from_slot,
    work,
)

_DECISIONS = {"a": "adopt-a", "b": "adopt-b", "ma": "merge-a", "mb": "merge-b"}
_MERGE_TEMPLATE_PREFIX = (
    "Replace this paragraph with the concrete requirements, acceptance criteria,"
)


def normalize_dual_spec_decision(raw: str) -> str | None:
    return _DECISIONS.get((raw or "").lower())


def dual_spec_owner_slot(decision: str) -> str | None:
    return {
        "adopt-a": "A",
        "merge-a": "A",
        "adopt-b": "B",
        "merge-b": "B",
    }.get(decision)


def agent_for_slot(ctx: WorkflowContext, slot: str) -> AgentRef:
    return ctx.ref(slot)


def reviewer_slot_for_owner_slot(slot: str) -> str:
    return "B" if slot == "A" else "A"


def candidate_spec_for_slot(ctx: WorkflowContext, slot: str) -> Path:
    return ctx.spec_dir / f"spec-{slot.lower()}.md"


def dual_spec_preflight(settings, stdin_isatty: bool) -> None:
    if not settings.dual_spec:
        return
    if not settings.human_gate:
        raise WorkflowAbort(
            "DUAL_SPEC=1 requires HUMAN_GATE=1 because a human must choose the "
            "final spec owner."
        )
    if not stdin_isatty:
        raise WorkflowAbort(
            "DUAL_SPEC=1 requires an interactive terminal for spec selection. "
            "Run interactively or set DUAL_SPEC=0."
        )


def dual_spec_final_review_scope(ctx: WorkflowContext, decision: str) -> str:
    merge_instruction = ""
    if decision.startswith("merge-"):
        merge_instruction = (
            f" Also compare {ctx.spec_dir / 'spec.md'} with "
            f"{ctx.wf / 'spec-merge-request.md'} and block approval if any "
            "requested adoption item is missing, distorted, or contradicted."
        )
    return render_prompt(
        ctx.prompts_dir,
        "review-scope-dual-final",
        {
            "SPEC_FILE": str(ctx.spec_dir / "spec.md"),
            "MERGE_INSTRUCTION": merge_instruction,
        },
    )


def write_spec_merge_request_template(
    ctx: WorkflowContext, base_slot: str, other_slot: str
) -> None:
    base_file = candidate_spec_for_slot(ctx, base_slot)
    other_file = candidate_spec_for_slot(ctx, other_slot)
    ctx.wf.mkdir(parents=True, exist_ok=True)
    (ctx.wf / "spec-merge-request.md").write_text(
        f"# Dual Spec Merge Request\n\n"
        f"- base owner: {base_slot}\n"
        f"- base spec: {base_file}\n"
        f"- adopt from owner: {other_slot}\n"
        f"- adopt from spec: {other_file}\n\n"
        f"## Items to adopt from {other_slot}\n\n"
        "Replace this paragraph with the concrete requirements, acceptance criteria,\n"
        "edge cases, non-goals, assumptions, or wording that the final spec owner must\n"
        f"adopt from {other_file}.\n",
        encoding="utf-8",
    )


def merge_request_has_content(ctx: WorkflowContext) -> bool:
    path = ctx.wf / "spec-merge-request.md"
    if not path.is_file():
        return False
    items: list[str] = []
    in_items = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Items to adopt "):
            in_items = True
            continue
        if in_items:
            items.append(line)
    while items and not items[0].strip():
        items.pop(0)
    while items and not items[-1].strip():
        items.pop()
    body = "\n".join(items)
    if not body:
        return False
    return not body.startswith(_MERGE_TEMPLATE_PREFIX)


def run_candidate_spec_review(
    ctx: WorkflowContext,
    reviewer: AgentRef,
    scope: str,
    review_out: Path,
    verdict_out: Path,
) -> None:
    ctx.review_path.unlink(missing_ok=True)
    ctx.verdict_path.unlink(missing_ok=True)
    old_collect = ctx.collect_review_suggestions
    ctx.collect_review_suggestions = False
    try:
        if not run_review(ctx, reviewer, scope):
            ctx.log(
                "(candidate spec review recorded a non-approved verdict; "
                "continuing to comparison)"
            )
    finally:
        ctx.collect_review_suggestions = old_collect
    if not ctx.review_path.is_file():
        ctx.review_path.write_text(
            "(reviewer did not write review.md)\n", encoding="utf-8"
        )
    if not ctx.verdict_path.is_file():
        ctx.verdict_path.write_text(
            '{"approved": false, "blockers": ["reviewer did not write a '
            'verdict"], "suggestions": []}\n',
            encoding="utf-8",
        )
    review_out.write_text(
        ctx.review_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    verdict_out.write_text(
        ctx.verdict_path.read_text(encoding="utf-8"), encoding="utf-8"
    )
    ctx.archive.archive_snapshot(
        review_out,
        review_out.name,
        "reviewer",
        reviewer,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_snapshot(
        verdict_out,
        verdict_out.name,
        "reviewer",
        reviewer,
        ctx.cur_stage,
        ctx.cur_round,
    )


def write_spec_comparison_index(ctx: WorkflowContext) -> None:
    comparison = ctx.spec_dir / "spec-comparison.md"
    comparison.write_text(
        "# Dual Spec Comparison\n\n"
        "Review these files before choosing the final spec owner:\n\n"
        f"- Candidate A: {ctx.spec_dir / 'spec-a.md'}\n"
        f"- Candidate B: {ctx.spec_dir / 'spec-b.md'}\n"
        f"- A's review of B: {ctx.spec_dir / 'spec-b.review-by-a.md'}\n"
        f"- B's review of A: {ctx.spec_dir / 'spec-a.review-by-b.md'}\n"
        f"- A's comparison table: {ctx.spec_dir / 'spec-comparison-a.md'}\n"
        f"- B's comparison table: {ctx.spec_dir / 'spec-comparison-b.md'}\n\n"
        "Decision commands:\n\n"
        "- a: adopt Candidate A as the base final spec\n"
        "- b: adopt Candidate B as the base final spec\n"
        "- ma: use Candidate A as base and explicitly adopt selected items from Candidate B\n"
        "- mb: use Candidate B as base and explicitly adopt selected items from Candidate A\n",
        encoding="utf-8",
    )
    ctx.archive.archive_snapshot(
        comparison,
        "spec-comparison.md",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )


def write_dual_spec_decision_file(ctx: WorkflowContext, decision: str) -> None:
    owner_slot = dual_spec_owner_slot(decision)
    reviewer_slot = reviewer_slot_for_owner_slot(owner_slot)
    decision_file = ctx.spec_dir / "spec-decision.md"
    decision_file.write_text(
        "# Dual Spec Decision\n\n"
        f"- decision: {decision}\n"
        f"- selected owner slot: {owner_slot}\n"
        f"- selected owner agent: {agent_for_slot(ctx, owner_slot).name}\n"
        f"- reviewer slot: {reviewer_slot}\n"
        f"- reviewer agent: {agent_for_slot(ctx, reviewer_slot).name}\n"
        f"- candidate A: {ctx.spec_dir / 'spec-a.md'}\n"
        f"- candidate B: {ctx.spec_dir / 'spec-b.md'}\n\n"
        f"The selected owner produces or owns the final {ctx.spec_dir / 'spec.md'}.\n"
        "The reviewer must approve the final spec before implementation planning starts.\n",
        encoding="utf-8",
    )
    ctx.archive.archive_snapshot(
        decision_file,
        "spec-decision.md",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )


def human_gate_dual_spec_decision(ctx: WorkflowContext) -> None:
    ctx.archive.log_section(
        "dual spec human selection",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.notify(
        "adversarial-ai-coding: dual spec comparison awaits human selection "
        f"({ctx.spec_dir / 'spec-comparison.md'})"
    )
    ctx.echo("\n### Human checkpoint: compare dual spec candidates.")
    for name in (
        "spec-a.md",
        "spec-b.md",
        "spec-comparison-a.md",
        "spec-comparison-b.md",
        "spec-comparison.md",
    ):
        ctx.echo(f"### - {ctx.spec_dir / name}")
    ctx.echo(
        "### Choose: a, b, ma, or mb. Final spec review and human approval "
        "run after this selection."
    )
    while True:
        decision = normalize_dual_spec_decision(
            ctx.ask("Dual spec decision [a/b/ma/mb]:")
        )
        if decision:
            break
        ctx.echo("Invalid decision. Enter a, b, ma, or mb.")
    owner_slot = dual_spec_owner_slot(decision)
    other_slot = reviewer_slot_for_owner_slot(owner_slot)
    if decision.startswith("merge-"):
        write_spec_merge_request_template(ctx, owner_slot, other_slot)
        ctx.echo(f"\n### Edit {ctx.wf / 'spec-merge-request.md'} now.")
        ctx.echo(
            "### List the exact items the selected owner must adopt from "
            f"{candidate_spec_for_slot(ctx, other_slot)}."
        )
        answer = ctx.ask(
            "Enter y after editing the merge request; anything else aborts:"
        )
        if answer not in ("y", "Y"):
            raise WorkflowAbort("Aborted: merge request was not approved.")
        if not merge_request_has_content(ctx):
            raise WorkflowAbort(
                f"Aborted: {ctx.wf / 'spec-merge-request.md'} does not contain "
                "explicit adoption instructions."
            )
        ctx.archive.archive_snapshot(
            ctx.wf / "spec-merge-request.md",
            "spec-merge-request.md",
            "workflow",
            None,
            ctx.cur_stage,
            ctx.cur_round,
        )
    write_dual_spec_decision_file(ctx, decision)
    ctx.dual_spec_decision = decision


def restore_dual_spec_decision(ctx: WorkflowContext) -> None:
    if not ctx.settings.dual_spec or ctx.dual_spec_decision:
        return
    path = ctx.spec_dir / "spec-decision.md"
    if not path.is_file():
        raise WorkflowAbort(
            f"!! DUAL_SPEC run has no decision yet and no {path} to restore it from."
        )
    decision = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("- decision: "):
            decision = line[len("- decision: ") :]
            break
    slot = dual_spec_owner_slot(decision)
    if slot is None:
        raise WorkflowAbort(
            f"!! Invalid decision [{decision}] in {path}; cannot restore the "
            "dual-spec selection."
        )
    if decision.startswith("merge-") and not (
        ctx.wf / "spec-merge-request.md"
    ).is_file():
        raise WorkflowAbort(
            f"!! Decision {decision} needs {ctx.wf / 'spec-merge-request.md'}, "
            "which is missing.\n   Restore it from the run archive under "
            f"{ctx.archive.run_dir.parent}, then resume again."
        )
    ctx.dual_spec_decision = decision
    set_spec_roles_from_slot(ctx, slot)
    ctx.echo_err(
        f"(restored dual-spec decision: {decision}; owner "
        f"{ctx.spec_roles.owner_slot}={ctx.spec_roles.owner_agent.name})"
    )


def apply_dual_spec_decision(
    ctx: WorkflowContext, decision: str, task: str
) -> None:
    owner_slot = dual_spec_owner_slot(decision)
    if owner_slot is None:
        raise WorkflowAbort(f"Unsupported dual spec decision:{decision}")
    set_spec_roles_from_slot(ctx, owner_slot)
    other_slot = ctx.spec_roles.reviewer_slot
    base_file = candidate_spec_for_slot(ctx, owner_slot)
    other_file = candidate_spec_for_slot(ctx, other_slot)
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = ctx.spec_dir / "spec.md"
    spec_file.write_text(base_file.read_text(encoding="utf-8"), encoding="utf-8")
    if decision.startswith("merge-"):
        prompt = render_prompt(
            ctx.prompts_dir,
            "dual-spec-merge-final",
            {
                "BASE_FILE": str(base_file),
                "OTHER_FILE": str(other_file),
                "MERGE_REQUEST_FILE": str(ctx.wf / "spec-merge-request.md"),
                "SPEC_FILE": str(spec_file),
                "TASK": task,
            },
        )
        work(ctx, ctx.spec_roles.owner_agent, prompt)
    review_loop(
        ctx,
        ctx.spec_roles.reviewer_agent,
        ctx.spec_roles.owner_agent,
        dual_spec_final_review_scope(ctx, decision),
    )
    human_gate_spec(ctx)


def run_dual_spec_spec_stage(ctx: WorkflowContext, task: str) -> None:
    ctx.spec_dir.mkdir(parents=True, exist_ok=True)
    spec_a = ctx.spec_dir / "spec-a.md"
    spec_b = ctx.spec_dir / "spec-b.md"

    if begin_stage(ctx, "write-spec-a", spec_a):
        work(
            ctx,
            ctx.ref("A"),
            render_prompt(
                ctx.prompts_dir,
                "dual-spec-write-candidate",
                {"SPEC_FILE": str(spec_a), "OTHER_SPEC_FILE": str(spec_b), "TASK": task},
            ),
        )
        end_stage(ctx)

    if begin_stage(ctx, "write-spec-b", spec_b):
        work(
            ctx,
            ctx.ref("B"),
            render_prompt(
                ctx.prompts_dir,
                "dual-spec-write-candidate",
                {"SPEC_FILE": str(spec_b), "OTHER_SPEC_FILE": str(spec_a), "TASK": task},
            ),
        )
        end_stage(ctx)

    review_a = ctx.spec_dir / "spec-a.review-by-b.md"
    verdict_a = ctx.spec_dir / "spec-a.verdict-by-b.json"
    if begin_stage(ctx, "review-spec-a", review_a, verdict_a):
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-candidate-spec",
            {"SPEC_FILE": str(spec_a), "CANDIDATE": "A", "OTHER_CANDIDATE": "B"},
        )
        run_candidate_spec_review(ctx, ctx.ref("B"), scope, review_a, verdict_a)
        end_stage(ctx)

    review_b = ctx.spec_dir / "spec-b.review-by-a.md"
    verdict_b = ctx.spec_dir / "spec-b.verdict-by-a.json"
    if begin_stage(ctx, "review-spec-b", review_b, verdict_b):
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-candidate-spec",
            {"SPEC_FILE": str(spec_b), "CANDIDATE": "B", "OTHER_CANDIDATE": "A"},
        )
        run_candidate_spec_review(ctx, ctx.ref("A"), scope, review_b, verdict_b)
        end_stage(ctx)

    for slot, agent in (("a", ctx.ref("A")), ("b", ctx.ref("B"))):
        comparison = ctx.spec_dir / f"spec-comparison-{slot}.md"
        if begin_stage(ctx, f"compare-specs-{slot}", comparison):
            work(
                ctx,
                agent,
                render_prompt(
                    ctx.prompts_dir,
                    "dual-spec-compare",
                    {
                        "OUTPUT_FILE": str(comparison),
                        "SPEC_A_FILE": str(spec_a),
                        "SPEC_B_FILE": str(spec_b),
                        "SPEC_A_REVIEW_FILE": str(review_a),
                        "SPEC_B_REVIEW_FILE": str(review_b),
                    },
                ),
            )
            end_stage(ctx)

    write_spec_comparison_index(ctx)

    if begin_stage(ctx, "select-spec", ctx.spec_dir / "spec-decision.md"):
        human_gate_dual_spec_decision(ctx)
        end_stage(ctx)
    restore_dual_spec_decision(ctx)

    if begin_stage(ctx, "finalize-spec", ctx.spec_dir / "spec.md"):
        apply_dual_spec_decision(ctx, ctx.dual_spec_decision, task)
        end_stage(ctx)
