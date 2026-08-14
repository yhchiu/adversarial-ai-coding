"""Workflow context and the worker-call wrapper.

Port of adversarial-ai-coding.sh:1171-1220 (work, check_protected) plus the
shared mutable context replacing bash's globals. Plan 5 adds stage flow here.
"""

from __future__ import annotations

import sys
import time
import shutil
import stat
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from .agents import (
    AgentIO,
    AgentRef,
    AgentSession,
    agent_model,
    agent_ref,
    impl_ref,
    is_builtin_agent,
    notify as agents_notify,
    resolve_model_args,
    run_worker,
)
from .archive import RunArchive, safe_slug
from .config import Settings, WorkflowAbort, render_template
from .i18n import emit
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


@dataclass(frozen=True)
class ProtectedControlsSnapshot:
    protected_bytes: bytes
    base_bytes: bytes
    paths: frozenset[str]
    base: str


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
    phased_suggestion_active: bool = False
    # A later consumer may read phased-suggestion.json only when this is true.
    # run_review clears it before each armed round and on every side-file error.
    phased_suggestion_valid: bool = False
    checking_protected: bool = False
    gate_cmd: str = ""
    build_gate_cmd: str = ""
    phase_gate_cmd: str = ""
    echo: Callable[..., None] = print
    echo_err: Callable[..., None] = _print_err
    spec_roles: SpecRoles = field(default_factory=SpecRoles)
    dual_spec_decision: str = ""
    ask: Callable[..., str] = _default_ask
    run_id: str = ""
    protected_controls: ProtectedControlsSnapshot | None = None

    def __post_init__(self) -> None:
        if not self.spec_roles.owner_agent:
            self.spec_roles.owner_agent = self.ref("A")
            self.spec_roles.reviewer_agent = self.ref("B")

    def ref(self, slot: str) -> AgentRef:
        return agent_ref(slot, self.settings)

    def update_settings(self, **changes) -> None:
        """Change settings for every holder, not just this context.

        Settings is frozen and RunArchive keeps its own reference for run
        metadata, so a plain ctx.settings assignment would leave the
        archive describing the run as it was before the change.
        """

        self.settings = replace(self.settings, **changes)
        self.archive.settings = self.settings

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

    def log(self, template: str, **fields: object) -> None:
        english = render_template(template, fields)
        self.log_file(english)
        emit(self.echo, template, **fields)

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


def _read_regular_control(path: Path) -> bytes:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise WorkflowAbort(
                f"!! protected control is not a regular file:{path}"
            )
        return path.read_bytes()
    except WorkflowAbort:
        raise
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Unable to read protected control:{path}: {exc}"
        ) from exc


def _snapshot_protected_controls(
    ctx: WorkflowContext,
) -> ProtectedControlsSnapshot:
    protected_path = ctx.wf / "protected-tests.txt"
    base_path = ctx.wf / "protected-base.sha"
    protected_bytes = _read_regular_control(protected_path)
    base_bytes = _read_regular_control(base_path)
    try:
        protected_text = protected_bytes.decode("utf-8")
        base = base_bytes.decode("utf-8").strip()
    except UnicodeError as exc:
        raise WorkflowAbort(
            f"!! protected control is not valid UTF-8: {exc}"
        ) from exc
    if not base:
        raise WorkflowAbort("!! protected control base is empty")
    return ProtectedControlsSnapshot(
        protected_bytes=protected_bytes,
        base_bytes=base_bytes,
        paths=frozenset(line for line in protected_text.splitlines() if line),
        base=base,
    )


def activate_protected_controls(ctx: WorkflowContext) -> None:
    ctx.protected_controls = _snapshot_protected_controls(ctx)


def _verify_protected_controls(ctx: WorkflowContext) -> None:
    snapshot = ctx.protected_controls
    if snapshot is None:
        return
    protected_path = ctx.wf / "protected-tests.txt"
    base_path = ctx.wf / "protected-base.sha"
    if _read_regular_control(protected_path) != snapshot.protected_bytes:
        raise WorkflowAbort(
            f"!! protected control changed during worker execution:{protected_path}"
        )
    if _read_regular_control(base_path) != snapshot.base_bytes:
        raise WorkflowAbort(
            f"!! protected control changed during worker execution:{base_path}"
        )


def _require_regular_or_missing_control(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Unable to inspect protected control:{path}: {exc}"
        ) from exc
    if not stat.S_ISREG(mode):
        raise WorkflowAbort(
            f"!! Refusing to overwrite non-regular protected control:{path}"
        )


def record_protected_tests(
    ctx: WorkflowContext, test_base: str, *, append: bool = False
) -> list[str]:
    """Detect test files changed since test_base and write both controls.

    append=True keeps already-protected paths (phased mode grows the list one
    phase at a time); append=False replaces the list (single-shot stage 4).
    """
    from .gitops import git_out, head_sha
    from .runstate import _atomic_write

    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    changed = git_out(["diff", "--name-only", test_base, "HEAD"], ctx.workspace)
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
    _require_regular_or_missing_control(protected_list)
    _require_regular_or_missing_control(protected_base)
    existing: list[str] = []
    if append and protected_list.is_file():
        existing = [
            line
            for line in protected_list.read_text(encoding="utf-8").splitlines()
            if line
        ]
    merged = existing + [name for name in names if name not in existing]
    # Base first, atomically: an interrupt then leaves {fresh base, stale
    # list}, which flags nothing. The old order left {fresh list, stale
    # base} and misread already-committed phase tests as tampering.
    _atomic_write(protected_base, head_sha(ctx.workspace) + "\n")
    _atomic_write(protected_list, "".join(name + "\n" for name in merged))
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
    if merged:
        ctx.log(
            "Protected acceptance test files:\n"
            + "\n".join(f"  - {name}" for name in merged)
        )
    else:
        ctx.echo_err(
            "(warning: no acceptance-test paths were recorded; protected "
            "control files remain active)"
        )
    return names


def work(ctx: WorkflowContext, agent: AgentRef, instruction: str) -> None:
    _verify_protected_controls(ctx)
    try:
        _work_body(ctx, agent, instruction)
    except BaseException as exc:
        try:
            _verify_protected_controls(ctx)
        except WorkflowAbort as tampering:
            raise tampering from exc
        raise
    _verify_protected_controls(ctx)


def _work_body(ctx: WorkflowContext, agent: AgentRef, instruction: str) -> None:
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
    controls = ctx.protected_controls
    if controls is None:
        return
    ctx.archive.log_section(
        "protected check",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    recoveries = 0
    while True:
        try:
            violations = protected_violations(
                controls.paths, controls.base, ctx.workspace
            )
        except subprocess.CalledProcessError as exc:
            raise WorkflowAbort(
                "!! Unable to verify protected acceptance tests; "
                "git diff failed closed."
            ) from exc
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
                "BASE": controls.base,
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
    emit(
        ctx.echo,
        "### Human checkpoint: review {path}, especially {focus}",
        path=path,
        focus=focus,
    )
    emit(
        ctx.echo,
        "### You may edit the file before continuing; your edits will be "
        "committed with the {subject}.",
        subject=subject,
    )
    answer = emit(
        ctx.ask, "Enter y to approve and continue; anything else aborts:"
    )
    if answer not in ("y", "Y"):
        raise WorkflowAbort(
            "Aborted: {subject} was not approved.", subject=subject
        )
    ctx.log("{subject} approved by human", subject=subject.capitalize())


def human_gate_spec(ctx: WorkflowContext) -> None:
    if ctx.settings.human_gate:
        _human_approval(
            ctx,
            subject="spec",
            path=ctx.spec_dir / "spec.md",
            focus="the Assumptions and Open Questions section.",
        )
    offer_phased_suggestion(ctx)


def offer_phased_suggestion(ctx: WorkflowContext) -> None:
    """Offer to enable Phased ATDD when the spec reviewer recommended it."""

    if not ctx.phased_suggestion_valid:
        return

    from .phased_suggestion import read_suggestion, suggestion_armed

    if not suggestion_armed(ctx.settings):
        return
    phased, reason = read_suggestion(ctx.wf, warn=ctx.echo_err)
    if not phased:
        return
    detail = f": {reason}" if reason else " (no reason given)"
    if not ctx.settings.human_gate:
        ctx.log(
            f"reviewer suggests Phased ATDD{detail}; HUMAN_GATE=0, not asking"
        )
        return
    ctx.echo("")
    ctx.echo(f"### Reviewer suggests Phased ATDD{detail}")
    answer = emit(ctx.ask, "Enable Phased ATDD for this run? [y/N]:")
    if answer not in ("y", "Y"):
        ctx.log("Phased ATDD suggestion declined; keeping the single-shot flow")
        return
    if ctx.state is not None:
        from .runstate import enable_snapshot_phases

        enable_snapshot_phases(ctx.state.state_dir)
    ctx.update_settings(phases=True)
    ctx.log("Phased ATDD enabled at the spec gate")
    ctx.notify("adversarial-ai-coding: Phased ATDD enabled at the spec gate")


def append_phased_suggestion_scope(ctx: WorkflowContext, scope: str) -> str:
    """Arm the spec review to also judge phased fitness, when applicable."""

    from .phased_suggestion import suggestion_armed

    if not suggestion_armed(ctx.settings):
        return scope
    ctx.phased_suggestion_active = True
    return scope + render_prompt(
        ctx.prompts_dir, "phased-suggestion-instruction", {"WF": str(ctx.wf)}
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


def _artifact_provenance(imported: bool, reviewed: bool) -> str:
    if not imported:
        return ""
    if reviewed:
        return " (imported; cross-reviewed in-run)"
    return " (imported; AI review skipped)"


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
    spec_note = _artifact_provenance(
        bool(ctx.settings.import_spec), ctx.settings.import_review
    )
    plan_note = _artifact_provenance(
        bool(ctx.settings.import_plan), ctx.settings.import_review
    )
    if ctx.settings.import_spec and not ctx.settings.import_review:
        review_note = (
            "Each stage passed deterministic quality gates; imported "
            "artifacts skipped AI review, and later stages were "
            "cross-reviewed. "
        )
    else:
        review_note = (
            "Each stage passed deterministic quality gates and cross-review. "
        )
    (ctx.wf / "pr-body.md").write_text(
        f"## Request\n\n{task}\n\n## Artifacts\n\n"
        f"- Spec with assumptions and open questions:"
        f"`{ctx.spec_dir}/spec.md`{spec_note}\n"
        f"- Implementation plan:`{ctx.spec_dir}/plan.md`{plan_note}\n\n"
        "Generated by adversarial-ai-coding, with original slots "
        f"A={ctx.settings.agent_a} and B={ctx.settings.agent_b}.\n"
        f"Final spec owner/worker: {roles.owner_slot}={roles.owner_agent.name}. "
        f"Reviewer: {roles.reviewer_slot}={roles.reviewer_agent.name}.\n"
        f"{review_note}"
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
        restore_or_record_base,
    )

    spec_file = ctx.spec_dir / "spec.md"
    plan_file = ctx.spec_dir / "plan.md"
    # The whole-branch reviews diff from the commit the run started on;
    # record it before the first stage so resumed runs reuse the same base.
    run_base = restore_or_record_base(
        ctx.state, "run-base", lambda: head_sha(ctx.workspace)
    )

    if ctx.settings.dual_spec:
        from .dual_spec import run_dual_spec_spec_stage

        run_dual_spec_spec_stage(ctx, task)
    else:
        set_spec_roles_from_slot(ctx, "A")
        if begin_stage(ctx, "write-spec", spec_file):
            if ctx.settings.import_spec:
                from .imports import stage_import

                stage_import(ctx, "spec", ctx.settings.import_spec, spec_file)
            else:
                work(
                    ctx,
                    ctx.spec_roles.owner_agent,
                    render_prompt(
                        ctx.prompts_dir,
                        "write-spec",
                        {"SPEC_FILE": str(spec_file), "TASK": task},
                    ),
                )
            if not ctx.settings.import_spec or ctx.settings.import_review:
                scope = append_phased_suggestion_scope(
                    ctx,
                    render_prompt(
                        ctx.prompts_dir,
                        "review-scope-spec",
                        {"SPEC_FILE": str(spec_file)},
                    ),
                )
                review_loop_ref(
                    ctx,
                    ctx.spec_roles.reviewer_agent,
                    ctx.spec_roles.owner_agent,
                    scope,
                )
                ctx.phased_suggestion_active = False
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
        plan_template = (
            "write-implementation-plan-phased"
            if ctx.settings.phases
            else "write-implementation-plan"
        )
        plan_scope_template = (
            "review-scope-plan-phased"
            if ctx.settings.phases
            else "review-scope-plan"
        )
        if ctx.settings.import_plan:
            from .imports import stage_import

            stage_import(ctx, "plan", ctx.settings.import_plan, plan_file)
        else:
            work(
                ctx,
                ctx.spec_roles.owner_agent,
                render_prompt(
                    ctx.prompts_dir,
                    plan_template,
                    {"SPEC_FILE": str(spec_file), "PLAN_FILE": str(plan_file)},
                ),
            )
        if not ctx.settings.import_plan or ctx.settings.import_review:
            scope = render_prompt(
                ctx.prompts_dir,
                plan_scope_template,
                {"PLAN_FILE": str(plan_file), "SPEC_FILE": str(spec_file)},
            )
            review_loop_ref(
                ctx,
                ctx.spec_roles.reviewer_agent,
                ctx.spec_roles.owner_agent,
                scope,
            )
        human_gate_plan(ctx)
        if ctx.settings.phases:
            from .phaseflow import phased_plan_structure_check

            phased_plan_structure_check(ctx, plan_file)
        commit_work(ctx, ctx.spec_roles.owner_agent, "Implementation plan")
        end_stage(ctx)

    protected_list = ctx.wf / "protected-tests.txt"
    protected_base = ctx.wf / "protected-base.sha"
    if ctx.settings.phases:
        from .phaseflow import run_phased_stages

        run_phased_stages(ctx, spec_file, plan_file)
    else:
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
            record_protected_tests(ctx, test_base)
            end_stage(ctx)

        activate_protected_controls(ctx)

    if begin_stage(ctx, "write-code"):
        if not ctx.settings.phases:
            impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)
            ctx.log(
                "Resolved implementation: "
                f"agent={impl.name} model={agent_model(impl, ctx.settings)} "
                f"args={resolve_model_args(impl, ctx.settings)}"
            )
            if ctx.settings.impl_model and not is_builtin_agent(impl.name):
                ctx.log(
                    "warning: IMPL_MODEL is ignored for custom implementation "
                    f"agent {impl.name}"
                )
            if ctx.state is not None:
                ensure_task_queue(ctx.state, plan_file)
                total = len(remaining_tasks(ctx.state))
                index = 1
                while remaining_tasks(ctx.state):
                    task_line = remaining_tasks(ctx.state)[0]
                    ctx.log(f"--- Task {index}/{total}:{task_line} ---")
                    work(
                        ctx,
                        impl,
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
                            ctx, impl, prompt
                        ),
                        log=ctx.log,
                        notify=ctx.notify,
                        stage=ctx.cur_stage,
                    )
                    commit_work(ctx, impl, f'Task "{task_line}"')
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
                "BASE": run_base,
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
            {"BASE": run_base, "SPEC_FILE": str(spec_file)},
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
