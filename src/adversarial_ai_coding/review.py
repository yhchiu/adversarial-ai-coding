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
from .phased_suggestion import DEFAULT_SUGGESTION, reset_suggestion, suggestion_path
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, agent_call
from .workflow import WorkflowContext, _retry_events, work

FAILED_VERDICT = (
    '{"approved": false, "blockers": ["reviewer did not write a '
    'verdict"], "suggestions": []}\n'
)
REVIEW_UNREADABLE_STUB = (
    "The reviewer's output file was unreadable and was discarded (a "
    "sandboxed reviewer can write it with a broken ACL). The next review "
    "round will regenerate it; there is nothing to fix in this file.\n"
)


def _read_probe(path: Path) -> None:
    path.read_bytes()


def _recover_unreadable_output(
    ctx: WorkflowContext, path: Path, fallback: str
) -> bool:
    """Replace a reviewer output the parent process cannot read back.

    A sandboxed reviewer (observed with Codex's Windows elevated sandbox) can
    rewrite verdict.json or review.md with an ACL that denies the workflow
    every access. Deleting still works through the parent directory's rights,
    so fail closed: discard the poisoned file, restore a safe fallback, and
    report the loss so the round is treated as failed.
    """

    try:
        _read_probe(path)
        return False
    except FileNotFoundError:
        return False
    except OSError:
        pass
    ctx.echo_err(
        f"(warning: reviewer output {path.name} is unreadable; discarding it "
        "and writing a safe fallback. A sandboxed reviewer may have written "
        "it with a broken ACL.)"
    )
    try:
        path.unlink(missing_ok=True)
        path.write_text(fallback, encoding="utf-8")
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Reviewer output {path} is unreadable and could not be "
            f"replaced ({exc}).\n   Remove the file manually, then resume "
            "the run."
        ) from exc
    return True


def _reset_review_file(ctx: WorkflowContext) -> None:
    """Give a new review loop a clean review.md under the parent identity."""

    _recover_unreadable_output(ctx, ctx.review_path, REVIEW_UNREADABLE_STUB)
    ctx.review_path.write_text("", encoding="utf-8")


def _disable_phased_suggestion(ctx: WorkflowContext, exc: OSError | WorkflowAbort) -> None:
    """Discard an optional suggestion failure without affecting review flow."""

    ctx.phased_suggestion_valid = False
    ctx.echo_err(f"(warning: ignoring phased suggestion: {exc})")


def _reset_phased_suggestion(ctx: WorkflowContext) -> None:
    """Prepare the optional side file, never letting its failure block review."""

    ctx.phased_suggestion_valid = False
    try:
        reset_suggestion(ctx.wf)
    except OSError as exc:
        _disable_phased_suggestion(ctx, exc)
    else:
        ctx.phased_suggestion_valid = True


def verdict_approved(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    # AGENTS.md: approved may be true only with zero blockers; a verdict
    # that contradicts itself fails closed.
    return payload.get("approved") is True and not payload.get("blockers")


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
    # Ensure reviewer outputs exist under the parent workflow identity (on
    # Windows a sandboxed reviewer can otherwise create review.md with an
    # ACL the workflow cannot read back), but keep readable content: round
    # N must see the worker replies written after round N-1.
    _recover_unreadable_output(ctx, ctx.review_path, REVIEW_UNREADABLE_STUB)
    if not ctx.review_path.is_file():
        ctx.review_path.write_text("", encoding="utf-8")
    # A reviewer that omits structured output must fail closed.
    ctx.verdict_path.write_text(FAILED_VERDICT, encoding="utf-8")
    if ctx.phased_suggestion_active:
        _reset_phased_suggestion(ctx)
    io = ctx.agent_io()
    result = agent_call(
        lambda: run_reviewer(
            agent,
            prompt_file_instruction(str(prompt_artifact)),
            ctx.settings,
            ctx.session,
            io,
        ),
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
    _recover_unreadable_output(ctx, ctx.verdict_path, FAILED_VERDICT)
    review_unreadable = _recover_unreadable_output(
        ctx, ctx.review_path, REVIEW_UNREADABLE_STUB
    )
    stage_slug = safe_slug(ctx.cur_stage)
    if ctx.phased_suggestion_active and ctx.phased_suggestion_valid:
        try:
            _recover_unreadable_output(
                ctx, suggestion_path(ctx.wf), DEFAULT_SUGGESTION
            )
            if suggestion_path(ctx.wf).is_file():
                ctx.archive.archive_snapshot(
                    suggestion_path(ctx.wf),
                    f"phased-suggestion-{stage_slug}-r{ctx.cur_round}.json",
                    "reviewer",
                    agent,
                    ctx.cur_stage,
                    ctx.cur_round,
                )
        except (OSError, WorkflowAbort) as exc:
            _disable_phased_suggestion(ctx, exc)
    if not ctx.verdict_path.is_file():
        ctx.echo_err("(reviewer did not write verdict.json; treating as failed)")
        return False
    review_missing = not ctx.review_path.is_file()
    if ctx.collect_review_suggestions and not (review_unreadable or review_missing):
        collect_suggestions(ctx)
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
    if review_unreadable or review_missing:
        ctx.echo_err(
            "(reviewer review.md was unreadable or missing; treating the "
            "round as failed)"
        )
        return False
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
    _reset_review_file(ctx)
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
