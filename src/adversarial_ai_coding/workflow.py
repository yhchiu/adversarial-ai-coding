"""Workflow context and the worker-call wrapper.

Port of adversarial-ai-coding.sh:1171-1220 (work, check_protected) plus the
shared mutable context replacing bash's globals. Plan 5 adds stage flow here.
"""

from __future__ import annotations

import sys
import time
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .agents import (
    AgentIO,
    AgentRef,
    AgentSession,
    agent_ref,
    notify as agents_notify,
    run_worker,
)
from .archive import RunArchive, safe_slug
from .config import Settings, WorkflowAbort
from .gitops import protected_violations
from .prompts import prompt_file_instruction, render_prompt
from .ratelimit import QUOTA_ABORT_RC, RetryEvents, agent_call
from .runstate import RunState


# review/gates are imported lazily to avoid the review->workflow cycle;
# these module-level names give the pipeline and tests a shared patch seam.
def review_loop_ref(ctx, reviewer, worker, scope, gate_cmd=""):
    from .review import review_loop

    review_loop(ctx, reviewer, worker, scope, gate_cmd)


def gate_loop_ref(cmd, **kwargs):
    from .gates import gate_loop

    gate_loop(cmd, **kwargs)


def _print_err(text: str) -> None:
    print(text, file=sys.stderr)


def _default_ask(prompt: str) -> str:
    # Divergence: bash read /dev/tty; the port supports interactive stdin only.
    if not sys.stdin.isatty():
        raise WorkflowAbort(
            "!! No interactive terminal is available for approval. Run from an "
            "interactive terminal, or set HUMAN_GATE=0 to skip this gate (not "
            "recommended)."
        )
    return input(prompt)


@dataclass
class SpecRoles:
    owner_slot: str = "A"
    reviewer_slot: str = "B"
    owner_agent: AgentRef | None = None
    reviewer_agent: AgentRef | None = None


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
    spec_roles: SpecRoles = field(default_factory=SpecRoles)
    dual_spec_decision: str = ""
    ask: Callable[[str], str] = _default_ask
    run_id: str = ""

    def __post_init__(self) -> None:
        if not self.spec_roles.owner_agent:
            self.spec_roles.owner_agent = self.ref("A")
            self.spec_roles.reviewer_agent = self.ref("B")

    def ref(self, slot: str) -> AgentRef:
        return agent_ref(slot, self.settings)

    @property
    def agent_out(self) -> Path:
        return self.wf / "last-agent-output.txt"

    @property
    def raw_out(self) -> Path:
        return self.wf / "last-agent-cli.raw"

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
            raw_out=self.raw_out,
            verdict_path=self.verdict_path,
            echo=self.echo,
        )

    def notify(self, message: str) -> None:
        agents_notify(self.settings, message)


def _retry_events(
    ctx: WorkflowContext,
    role: str,
    agent: AgentRef,
    slug: str,
    io: AgentIO,
) -> RetryEvents:
    return RetryEvents(
        archive_attempt=lambda attempt, rc: ctx.archive.archive_agent_attempt(
            role,
            agent,
            slug,
            attempt,
            rc,
            ctx.agent_out,
            io.raw_out,
            stage=ctx.cur_stage,
            round=ctx.cur_round,
        ),
        log_retry=ctx.log,
        notify=ctx.notify,
        sleep=time.sleep,
    )


def work(ctx: WorkflowContext, agent: AgentRef, instruction: str) -> None:
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
    ctx.echo(f">>> Worker({agent.name}) is running...")
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
        events=_retry_events(ctx, "worker", agent, slug, io),
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


def check_protected(ctx: WorkflowContext, agent: AgentRef) -> None:
    protected_file = ctx.wf / "protected-tests.txt"
    base_file = ctx.wf / "protected-base.sha"
    if not (protected_file.is_file() and base_file.is_file()):
        return
    ctx.archive.log_section(
        "protected check",
        "workflow",
        None,
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


def set_spec_roles_from_slot(ctx: WorkflowContext, slot: str) -> None:
    reviewer = "B" if slot == "A" else "A"
    ctx.spec_roles = SpecRoles(
        owner_slot=slot,
        reviewer_slot=reviewer,
        owner_agent=ctx.ref(slot),
        reviewer_agent=ctx.ref(reviewer),
    )


def begin_stage(ctx: WorkflowContext, name: str, *artifacts: Path) -> bool:
    if ctx.state is not None and ctx.state.stage_done(name):
        for artifact in artifacts:
            if not artifact.exists():
                raise WorkflowAbort(
                    f"!! Stage {name} is recorded complete but its artifact "
                    f"{artifact} is missing.\n   Restore it from the run archive "
                    f"under {ctx.archive.run_dir.parent}, or delete "
                    f"{ctx.state.state_dir} to start over."
                )
        ctx.log(f"== skip [{name}] (already completed in run {ctx.run_id})")
        return False
    ctx.cur_stage = name
    ctx.session.worker_session = ""
    ctx.session.owner = None
    ctx.cur_round = 1
    ctx.archive.log_section(
        "stage begin",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.log(f"\n================ [{name}] ================")
    return True


def end_stage(ctx: WorkflowContext) -> None:
    if ctx.state is None:
        return
    from .gitops import head_sha

    ctx.state.record_stage(ctx.cur_stage, head_sha(ctx.workspace))


def commit_work(ctx: WorkflowContext, agent: AgentRef, description: str) -> None:
    from .gitops import ensure_committed

    ctx.archive.log_section(
        "commit", "worker", agent, ctx.cur_stage, ctx.cur_round, echo=ctx.echo
    )
    prompt = render_prompt(
        ctx.prompts_dir, "commit-approved-work", {"DESCRIPTION": description}
    )
    work(ctx, agent, prompt)
    ensure_committed(ctx.workspace, ctx.cur_stage, ctx.echo_err)


def commit_if_dirty(ctx: WorkflowContext, agent: AgentRef, description: str) -> None:
    from .gitops import status_porcelain

    if not status_porcelain(ctx.workspace):
        return
    commit_work(ctx, agent, description)


def _human_approval(
    ctx: WorkflowContext, *, subject: str, path: Path, focus: str
) -> None:
    ctx.archive.log_section(
        "human gate",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.notify(
        f"adversarial-ai-coding: {subject} awaits human approval ({path})"
    )
    ctx.echo("")
    ctx.echo(f"### Human checkpoint: review {path}, especially {focus}")
    ctx.echo(
        "### You may edit the file before continuing; your edits will be "
        f"committed with the {subject}."
    )
    answer = ctx.ask("Enter y to approve and continue; anything else aborts:")
    if answer not in ("y", "Y"):
        raise WorkflowAbort(f"Aborted: {subject} was not approved.")
    ctx.log(f"{subject.capitalize()} approved by human")


def human_gate_spec(ctx: WorkflowContext) -> None:
    if not ctx.settings.human_gate:
        return
    _human_approval(
        ctx,
        subject="spec",
        path=ctx.spec_dir / "spec.md",
        focus="the Assumptions and Open Questions section.",
    )


def human_gate_plan(ctx: WorkflowContext) -> None:
    """Optional (HUMAN_GATE_PLAN=1) checkpoint before the plan is committed.

    plan.md is the task queue for the implementation stage: one `- [ ]` item
    becomes one commit, so this is the last cheap place to intervene.
    """

    if not ctx.settings.human_gate_plan:
        return
    _human_approval(
        ctx,
        subject="plan",
        path=ctx.spec_dir / "plan.md",
        focus="the task breakdown: each `- [ ]` item becomes one commit.",
    )


def plan_gate_preflight(settings: Settings, stdin_isatty: bool) -> None:
    """Fail before any paid AI call when the plan gate cannot ask a human."""

    if not settings.human_gate_plan:
        return
    if not stdin_isatty:
        raise WorkflowAbort(
            "HUMAN_GATE_PLAN=1 requires an interactive terminal for plan "
            "approval. Run interactively or set HUMAN_GATE_PLAN=0."
        )


def _run_git_default(args, cwd):
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.strip()


def _run_gh_default(args, cwd):
    proc = subprocess.run(
        ["gh", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout.strip()


def finish(
    ctx: WorkflowContext,
    task: str,
    *,
    which=shutil.which,
    run_git=_run_git_default,
    run_gh=_run_gh_default,
) -> None:
    from .archive import metrics_summary
    from .gitops import current_branch

    ctx.archive.log_section(
        "finish",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    branch = current_branch(ctx.workspace)
    title = task.split("\n", 1)[0][:72]
    roles = ctx.spec_roles
    (ctx.wf / "pr-body.md").write_text(
        f"## Task\n\n{task}\n\n## Artifacts\n\n"
        f"- Spec with assumptions and open questions:`{ctx.spec_dir}/spec.md`\n"
        f"- Implementation plan:`{ctx.spec_dir}/plan.md`\n\n"
        "Generated by adversarial-ai-coding, with original slots "
        f"A={ctx.settings.agent_a} and B={ctx.settings.agent_b}.\n"
        f"Final spec owner/worker: {roles.owner_slot}={roles.owner_agent.name}. "
        f"Reviewer: {roles.reviewer_slot}={roles.reviewer_agent.name}.\n"
        "Each stage passed deterministic quality gates and cross-review. "
        "Acceptance tests were written by the reviewer and protected against "
        "worker edits.\n",
        encoding="utf-8",
    )
    ctx.echo(
        f"\nAll stages complete. Spec and plan are in {ctx.spec_dir}/, "
        f"and the run log is at {ctx.archive.log_path}"
    )
    if ctx.archive.metrics_path.is_file():
        ctx.echo("")
        ctx.echo(
            f"Run metrics (details:{ctx.archive.metrics_path}; review rounds "
            "are a prompt-quality signal):"
        )
        ctx.echo(metrics_summary(ctx.archive.metrics_path))
    ctx.archive.archive_snapshot(
        ctx.wf / "pr-body.md",
        "pr-body.md",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_snapshot(
        ctx.suggestions_path,
        "suggestions.md",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
    )
    has_origin = (
        run_git is not None
        and run_git(["remote", "get-url", "origin"], ctx.workspace)[0] == 0
    )
    if ctx.settings.open_pr and which("gh") and has_origin:
        run_git(["push", "-u", "origin", branch], ctx.workspace)
        rc, url = run_gh(
            ["pr", "view", "--json", "url", "--jq", ".url"], ctx.workspace
        )
        if rc == 0 and url:
            ctx.echo(f"PR already exists: {url} (skipping gh pr create)")
        else:
            run_gh(
                [
                    "pr",
                    "create",
                    "--title",
                    title,
                    "--body-file",
                    str(ctx.wf / "pr-body.md"),
                ],
                ctx.workspace,
            )
    else:
        ctx.echo("")
        ctx.echo("Next steps, run manually:")
        ctx.echo(f"  git push -u origin {branch}")
        ctx.echo(
            f'  gh pr create --title "{title}" --body-file {ctx.wf / "pr-body.md"}'
        )
        if ctx.settings.open_pr:
            ctx.echo_err(
                "(OPEN_PR=1 but gh or origin remote is missing; printed "
                "commands instead)"
            )
    ctx.notify(f"adversarial-ai-coding: all stages complete ({branch})")


def run_workflow(ctx: WorkflowContext, task: str) -> None:
    from .gitops import git_out, head_sha
    from .runstate import (
        ensure_task_queue,
        mark_plan_task_done,
        pop_task_queue,
        remaining_tasks,
        restore_or_record_acceptance_base,
    )

    spec_file = ctx.spec_dir / "spec.md"
    plan_file = ctx.spec_dir / "plan.md"

    if ctx.settings.dual_spec:
        from .dual_spec import run_dual_spec_spec_stage

        run_dual_spec_spec_stage(ctx, task)
    else:
        set_spec_roles_from_slot(ctx, "A")
        if begin_stage(ctx, "write-spec", spec_file):
            work(
                ctx,
                ctx.spec_roles.owner_agent,
                render_prompt(
                    ctx.prompts_dir,
                    "write-spec",
                    {"SPEC_FILE": str(spec_file), "TASK": task},
                ),
            )
            scope = render_prompt(
                ctx.prompts_dir,
                "review-scope-spec",
                {"SPEC_FILE": str(spec_file)},
            )
            review_loop_ref(
                ctx,
                ctx.spec_roles.reviewer_agent,
                ctx.spec_roles.owner_agent,
                scope,
            )
            human_gate_spec(ctx)
            end_stage(ctx)

    from .dual_spec import restore_dual_spec_decision

    restore_dual_spec_decision(ctx)

    if begin_stage(ctx, "commit-spec"):
        commit_work(
            ctx,
            ctx.spec_roles.owner_agent,
            "Spec, approved by review and human gate",
        )
        end_stage(ctx)

    if begin_stage(ctx, "write-implementation-plan", plan_file):
        work(
            ctx,
            ctx.spec_roles.owner_agent,
            render_prompt(
                ctx.prompts_dir,
                "write-implementation-plan",
                {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)},
            ),
        )
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-plan",
            {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)},
        )
        review_loop_ref(
            ctx,
            ctx.spec_roles.reviewer_agent,
            ctx.spec_roles.owner_agent,
            scope,
        )
        human_gate_plan(ctx)
        commit_work(ctx, ctx.spec_roles.owner_agent, "Implementation plan")
        end_stage(ctx)

    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    if begin_stage(ctx, "write-acceptance-tests", protected_list, protected_base):
        test_base = restore_or_record_acceptance_base(
            ctx.state, lambda: head_sha(ctx.workspace)
        )
        work(
            ctx,
            ctx.spec_roles.reviewer_agent,
            render_prompt(
                ctx.prompts_dir,
                "write-acceptance-tests",
                {"SPEC_FILE": str(spec_file), "SPEC_DIR": str(ctx.spec_dir)},
            ),
        )
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-acceptance-tests",
            {"TEST_BASE": test_base, "SPEC_FILE": str(spec_file)},
        )
        review_loop_ref(
            ctx,
            ctx.spec_roles.owner_agent,
            ctx.spec_roles.reviewer_agent,
            scope,
        )
        commit_work(ctx, ctx.spec_roles.reviewer_agent, "Acceptance tests")
        changed = git_out(
            ["diff", "--name-only", test_base, "HEAD"], ctx.workspace
        )
        root = Path(git_out(["rev-parse", "--show-toplevel"], ctx.workspace))
        try:
            spec_prefix = ctx.spec_dir.relative_to(root).as_posix().rstrip("/") + "/"
        except ValueError:
            spec_prefix = ""
        names = [
            name
            for name in changed.splitlines()
            if name and (not spec_prefix or not name.startswith(spec_prefix))
        ]
        protected_list.write_text(
            "".join(name + "\n" for name in names), encoding="utf-8"
        )
        protected_base.write_text(head_sha(ctx.workspace) + "\n", encoding="utf-8")
        ctx.archive.archive_snapshot(
            protected_list,
            "protected-tests.txt",
            "workflow",
            None,
            ctx.cur_stage,
            ctx.cur_round,
        )
        ctx.archive.archive_snapshot(
            protected_base,
            "protected-base.sha",
            "workflow",
            None,
            ctx.cur_stage,
            ctx.cur_round,
        )
        if names:
            ctx.log(
                "Protected acceptance test files:\n"
                + "\n".join(f"  - {name}" for name in names)
            )
        else:
            ctx.echo_err(
                "(warning: acceptance-test stage produced no files; test "
                "protection is disabled)"
            )
        end_stage(ctx)

    if begin_stage(ctx, "write-code"):
        if ctx.state is not None:
            ensure_task_queue(ctx.state, plan_file)
            total = len(remaining_tasks(ctx.state))
            index = 1
            while remaining_tasks(ctx.state):
                task_line = remaining_tasks(ctx.state)[0]
                ctx.log(f"--- Task {index}/{total}:{task_line} ---")
                work(
                    ctx,
                    ctx.spec_roles.owner_agent,
                    render_prompt(
                        ctx.prompts_dir,
                        "implement-plan-task",
                        {
                            "PLAN_FILE": str(plan_file),
                            "TASK": task_line,
                            "PROTECTED_TESTS_FILE": str(protected_list),
                        },
                    ),
                )
                gate_loop_ref(
                    ctx.build_gate_cmd,
                    cwd=ctx.workspace,
                    prompts_dir=ctx.prompts_dir,
                    max_rounds=ctx.settings.max_rounds,
                    do_work=lambda prompt: work(
                        ctx, ctx.spec_roles.owner_agent, prompt
                    ),
                    log=ctx.log,
                    notify=ctx.notify,
                    stage=ctx.cur_stage,
                )
                commit_work(ctx, ctx.spec_roles.owner_agent, f'Task "{task_line}"')
                pop_task_queue(ctx.state)
                mark_plan_task_done(plan_file, task_line)
                index += 1
        ctx.log(
            "--- All tasks complete; running full quality gate. Acceptance "
            "tests must pass. ---"
        )
        gate_loop_ref(
            ctx.gate_cmd,
            cwd=ctx.workspace,
            prompts_dir=ctx.prompts_dir,
            max_rounds=ctx.settings.max_rounds,
            do_work=lambda prompt: work(ctx, ctx.spec_roles.owner_agent, prompt),
            log=ctx.log,
            notify=ctx.notify,
            stage=ctx.cur_stage,
        )
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-branch",
            {
                "SPEC_FILE": str(spec_file),
                "PROTECTED_TESTS_FILE": str(protected_list),
            },
        )
        review_loop_ref(
            ctx,
            ctx.spec_roles.reviewer_agent,
            ctx.spec_roles.owner_agent,
            scope,
            gate_cmd=ctx.gate_cmd,
        )
        commit_if_dirty(ctx, ctx.spec_roles.owner_agent, "Review fixes")
        end_stage(ctx)

    if begin_stage(ctx, "final-review-and-fixes"):
        work(
            ctx,
            ctx.spec_roles.owner_agent,
            render_prompt(
                ctx.prompts_dir,
                "final-self-review",
                {"SUGGESTIONS_FILE": str(ctx.suggestions_path)},
            ),
        )
        gate_loop_ref(
            ctx.gate_cmd,
            cwd=ctx.workspace,
            prompts_dir=ctx.prompts_dir,
            max_rounds=ctx.settings.max_rounds,
            do_work=lambda prompt: work(ctx, ctx.spec_roles.owner_agent, prompt),
            log=ctx.log,
            notify=ctx.notify,
            stage=ctx.cur_stage,
        )
        scope = render_prompt(
            ctx.prompts_dir,
            "review-scope-final-acceptance",
            {"SPEC_FILE": str(spec_file)},
        )
        review_loop_ref(
            ctx,
            ctx.spec_roles.reviewer_agent,
            ctx.spec_roles.owner_agent,
            scope,
            gate_cmd=ctx.gate_cmd,
        )
        commit_if_dirty(ctx, ctx.spec_roles.owner_agent, "Final fixes")
        end_stage(ctx)

    finish(ctx, task)
    if ctx.state is not None:
        ctx.state.mark_completed()
