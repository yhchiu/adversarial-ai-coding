"""Reviewer machinery: verdicts, prompts, the review call, the review loop.

Port of adversarial-ai-coding.sh:698-700, 1226-1241, 1295-1352, 1420-1439.
Blockers must be fixed; suggestions do not block and are evaluated at the end.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .agents import AgentRef, run_reviewer
from .archive import safe_slug
from .config import WorkflowAbort
from .gates import gate_loop
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, agent_call
from .workflow import WorkflowContext, _retry_events, work

FAILED_VERDICT = (
    '{"approved": false, "blockers": ["reviewer did not write a '
    'verdict"], "suggestions": []}\n'
)


def verdict_approved(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return payload.get("approved") is True


def compose_review_prompt(
    agent: AgentRef, scope: str, prompts_dir: Path, wf: Path
) -> str:
    prompt = render_prompt(prompts_dir, "review", {"SCOPE": scope, "WF": str(wf)})
    if agent.name == "claude":
        return prompt
    instruction = render_prompt(
        prompts_dir, "verdict-file-instruction", {"WF": str(wf)}
    )
    return prompt + instruction


def collect_suggestions(ctx: WorkflowContext) -> None:
    if not ctx.verdict_path.is_file():
        return
    try:
        payload = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    suggestions = [suggestion for suggestion in payload.get("suggestions") or [] if suggestion]
    if not suggestions:
        return
    block = (
        f"## {ctx.cur_stage}(round {ctx.cur_round})\n"
        + "".join(f"- {suggestion}\n" for suggestion in suggestions)
        + "\n"
    )
    with ctx.suggestions_path.open("a", encoding="utf-8") as suggestions_file:
        suggestions_file.write(block)


def show_blockers(ctx: WorkflowContext) -> None:
    if not ctx.verdict_path.is_file():
        return
    ctx.log("Review did not pass; blockers:")
    try:
        payload = json.loads(ctx.verdict_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return
    for blocker in payload.get("blockers") or []:
        ctx.log(f"  - {blocker}")


def run_review(ctx: WorkflowContext, agent: AgentRef, scope: str) -> bool:
    started = time.monotonic()
    ctx.session.last_cost = ""
    ctx.archive.log_section(
        "review",
        "reviewer",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.echo(f">>> Reviewer({agent.name}) is reviewing...")
    slug = f"reviewer-{safe_slug(ctx.cur_stage or 'startup')}-r{ctx.cur_round}"
    prompt = compose_review_prompt(agent, scope, ctx.prompts_dir, ctx.wf)
    prompt_artifact = ctx.archive.archive_text(
        f"{slug}-prompt.md",
        prompt,
        "reviewer",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    # A reviewer that omits structured output must fail closed.
    ctx.verdict_path.write_text(FAILED_VERDICT, encoding="utf-8")
    io = ctx.agent_io()
    result = agent_call(
        lambda: run_reviewer(
            agent,
            prompt_file_instruction(str(prompt_artifact)),
            ctx.settings,
            ctx.session,
            io,
        ),
        agent_out=ctx.agent_out,
        settings=ctx.settings,
        events=_retry_events(ctx, "reviewer", agent, slug, io),
    )
    output_artifact = ctx.archive.art_path(f"{slug}-output.txt")
    output_artifact.write_text(result.text.rstrip("\n") + "\n", encoding="utf-8")
    ctx.archive.write_meta(
        output_artifact, "reviewer", agent, ctx.cur_stage, ctx.cur_round
    )
    ctx.log_file(result.text)
    if result.rc == QUOTA_ABORT_RC:
        raise WorkflowAbort(
            "!! Reviewer gave up on a quota/rate limit; aborting the run as resumable.",
            rc=QUOTA_ABORT_RC,
        )
    if result.rc != 0:
        ctx.echo_err("(warning: reviewer execution failed)")
    ctx.archive.archive_snapshot(
        ctx.agent_out,
        f"{slug}-final.raw",
        "reviewer",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.metric(
        "reviewer",
        agent,
        ctx.cur_round,
        int(time.monotonic() - started),
        ctx.session.last_cost,
        stage=ctx.cur_stage,
    )
    if not ctx.verdict_path.is_file():
        ctx.echo_err("(reviewer did not write verdict.json; treating as failed)")
        return False
    if ctx.collect_review_suggestions:
        collect_suggestions(ctx)
    stage_slug = safe_slug(ctx.cur_stage)
    ctx.archive.archive_snapshot(
        ctx.review_path,
        f"review-{stage_slug}-r{ctx.cur_round}.md",
        "reviewer",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_snapshot(
        ctx.verdict_path,
        f"verdict-{stage_slug}-r{ctx.cur_round}.json",
        "reviewer",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    if not verdict_approved(ctx.verdict_path):
        show_blockers(ctx)
        return False
    return True


def review_loop(
    ctx: WorkflowContext,
    reviewer: AgentRef,
    worker: AgentRef,
    scope: str,
    gate_cmd: str = "",
) -> None:
    ctx.cur_round = 1
    while not run_review(ctx, reviewer, scope):
        if ctx.cur_round >= ctx.settings.max_rounds:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] review failed after "
                f"{ctx.settings.max_rounds} rounds; human intervention required"
            )
            raise WorkflowAbort(
                f"!! [{ctx.cur_stage}] Review still failed after "
                f"{ctx.settings.max_rounds} rounds; stopping. Read "
                f"{ctx.review_path} and handle it manually."
            )
        ctx.cur_round += 1
        ctx.log(
            f"--- [{ctx.cur_stage}] round {ctx.cur_round}: worker updates "
            "from review findings ---"
        )
        prompt = render_prompt(
            ctx.prompts_dir,
            "review-findings-repair",
            {"REVIEW_FILE": str(ctx.review_path), "STAGE": ctx.cur_stage},
        )
        work(ctx, worker, prompt)
        ctx.archive.archive_snapshot(
            ctx.review_path,
            f"review-{safe_slug(ctx.cur_stage)}-r{ctx.cur_round}-worker.md",
            "worker",
            worker,
            ctx.cur_stage,
            ctx.cur_round,
        )
        gate_loop(
            gate_cmd,
            cwd=ctx.workspace,
            prompts_dir=ctx.prompts_dir,
            max_rounds=ctx.settings.max_rounds,
            do_work=lambda repair_prompt: work(ctx, worker, repair_prompt),
            log=ctx.log,
            notify=ctx.notify,
            stage=ctx.cur_stage,
        )
    ctx.log(f"[{ctx.cur_stage}] Review approved")
