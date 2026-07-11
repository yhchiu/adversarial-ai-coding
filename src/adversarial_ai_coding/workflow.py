"""Workflow context and the worker-call wrapper.

Port of adversarial-ai-coding.sh:1171-1220 (work, check_protected) plus the
shared mutable context replacing bash's globals. Plan 5 adds stage flow here.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .agents import AgentIO, AgentSession, notify as agents_notify, run_worker
from .archive import RunArchive, safe_slug
from .config import Settings, WorkflowAbort
from .gitops import protected_violations
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, RetryEvents, agent_call
from .runstate import RunState


def _print_err(text: str) -> None:
    print(text, file=sys.stderr)


@dataclass
class WorkflowContext:
    settings: Settings
    archive: RunArchive
    state: RunState | None
    session: AgentSession
    workspace: Path
    wf: Path
    prompts_dir: Path
    spec_dir: Path
    cur_stage: str = "startup"
    cur_round: int = 1
    collect_review_suggestions: bool = True
    checking_protected: bool = False
    gate_cmd: str = ""
    build_gate_cmd: str = ""
    echo: Callable[[str], None] = print
    echo_err: Callable[[str], None] = _print_err

    @property
    def agent_out(self) -> Path:
        return self.wf / "last-agent-output.txt"

    @property
    def verdict_path(self) -> Path:
        return self.wf / "verdict.json"

    @property
    def review_path(self) -> Path:
        return self.wf / "review.md"

    @property
    def suggestions_path(self) -> Path:
        return self.wf / "suggestions.md"

    def log(self, text: str) -> None:
        self.log_file(text)
        self.echo(text)

    def log_file(self, text: str) -> None:
        self.archive.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.archive.log_path.open("a", encoding="utf-8") as log:
            log.write(text + "\n")

    def agent_io(self) -> AgentIO:
        return AgentIO(
            agent_out=self.agent_out,
            verdict_path=self.verdict_path,
            echo=self.echo,
        )

    def notify(self, message: str) -> None:
        agents_notify(self.settings, message)


def _retry_events(
    ctx: WorkflowContext, role: str, agent: str, slug: str
) -> RetryEvents:
    return RetryEvents(
        archive_attempt=lambda attempt, rc: ctx.archive.archive_agent_attempt(
            role,
            agent,
            slug,
            attempt,
            rc,
            ctx.agent_out,
            stage=ctx.cur_stage,
            round=ctx.cur_round,
        ),
        log_retry=ctx.log,
        notify=ctx.notify,
        sleep=time.sleep,
    )


def work(ctx: WorkflowContext, agent: str, instruction: str) -> None:
    started = time.monotonic()
    ctx.session.last_cost = ""
    ctx.archive.log_section(
        "AI call",
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.echo(f">>> Worker({agent}) is running...")
    slug = f"worker-{safe_slug(ctx.cur_stage or 'startup')}-r{ctx.cur_round}"
    prompt_artifact = ctx.archive.archive_text(
        f"{slug}-prompt.md",
        instruction,
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    short_prompt = prompt_file_instruction(str(prompt_artifact))
    io = ctx.agent_io()
    result = agent_call(
        lambda: run_worker(agent, short_prompt, ctx.settings, ctx.session, io),
        agent_out=ctx.agent_out,
        settings=ctx.settings,
        events=_retry_events(ctx, "worker", agent, slug),
    )
    output_artifact = ctx.archive.art_path(f"{slug}-output.txt")
    output_artifact.write_text(result.text.rstrip("\n") + "\n", encoding="utf-8")
    ctx.archive.write_meta(
        output_artifact, "worker", agent, ctx.cur_stage, ctx.cur_round
    )
    ctx.log_file(result.text)
    if result.rc == QUOTA_ABORT_RC:
        raise WorkflowAbort(
            "!! Worker gave up on a quota/rate limit; aborting the run as resumable.",
            rc=QUOTA_ABORT_RC,
        )
    # Bash's process-substitution pipeline masks ordinary agent failures. The
    # review and gate loops remain the correctness net, so preserve that result.
    ctx.archive.archive_snapshot(
        ctx.agent_out,
        f"{slug}-final.raw",
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_git_state(
        "worker",
        agent,
        slug,
        ctx.cur_stage,
        ctx.cur_round,
        cwd=ctx.workspace,
    )
    ctx.archive.metric(
        "worker",
        agent,
        ctx.cur_round,
        int(time.monotonic() - started),
        ctx.session.last_cost,
        stage=ctx.cur_stage,
    )
    if not ctx.checking_protected:
        check_protected(ctx, agent)


def check_protected(ctx: WorkflowContext, agent: str) -> None:
    protected_file = ctx.wf / "protected-tests.txt"
    base_file = ctx.wf / "protected-base.sha"
    if not (protected_file.is_file() and base_file.is_file()):
        return
    ctx.archive.log_section(
        "protected check",
        "workflow",
        "workflow",
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    base = base_file.read_text(encoding="utf-8").strip()
    recoveries = 0
    while True:
        violations = protected_violations(protected_file, base, ctx.workspace)
        if not violations:
            return
        listing = "\n".join(f"  - {violation}" for violation in violations)
        ctx.log(f"!! Protected acceptance test files were modified:\n{listing}")
        if recoveries >= 2:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] protected tests were "
                "modified and not restored; human intervention required"
            )
            raise WorkflowAbort(
                "!! Worker repeatedly modified protected tests and did not "
                "restore them; stopping for human intervention."
            )
        recoveries += 1
        prompt = render_prompt(
            ctx.prompts_dir,
            "protected-tests-modified",
            {
                "VIOLATIONS": "\n".join(violations),
                "BASE": base,
                "SPEC_FILE": str(ctx.spec_dir / "spec.md"),
            },
        )
        ctx.checking_protected = True
        try:
            work(ctx, agent, prompt)
        finally:
            ctx.checking_protected = False
