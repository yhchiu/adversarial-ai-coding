"""CLI entry point: startup checks, state claiming, abort handling.

Port of adversarial-ai-coding.sh:91-123, 332-339, 1813-1894, and
2006-2008. jq is no longer required; resume hints name the console script.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from . import __version__
from .agents import (
    AgentSession,
    agent_model,
    agent_ref,
    resolve_model_args,
    validate_agents,
)
from .archive import establish_run_archive
from .config import WORK_DIR, Settings, SettingsError, WorkflowAbort
from .dual_spec import dual_spec_preflight
from .gates import detect_build_gate, detect_gate
from .gitops import (
    current_branch,
    is_inside_work_tree,
    resume_workspace,
    setup_workspace,
)
from .i18n import Presenter, bind_ask, emit_exception, resolve_lang
from .imports import import_preflight
from .prompts import (
    PromptTemplateError,
    bootstrap_agents_md,
    default_agents_template,
    default_prompts_dir,
    write_agents_section,
)
from .runindex import write_run_manifest
from .runstate import (
    RunState,
    RunStateError,
    check_immutable,
    init_live_state,
    load_snapshot,
    snapshot_values,
    write_snapshot,
)
from .style import Styler
from .workflow import (
    WorkflowContext,
    _default_ask,
    plan_gate_preflight,
    run_workflow,
)

USAGE = """Usage:adversarial-ai-coding [options] "request description"
      adversarial-ai-coding [options] request.md      # If the argument is a file, use its contents as the request
      adversarial-ai-coding print-agents    # Print the AGENTS.md rule template and exit
      adversarial-ai-coding -h, --help      # Show this help and exit
      adversarial-ai-coding -V, -v, --version  # Print version and exit"""

_HELP_FLAGS = frozenset({"-h", "--help"})
_VERSION_FLAGS = frozenset({"-V", "-v", "--version"})


def _parse_argv(argv: list[str]) -> tuple[str, str]:
    """Classify argv into (action, payload) before any work starts.

    Actions: help, version, print-agents, run, error.
    A bare "--" ends flag parsing so a request may start with a dash.
    """
    help_requested = False
    version_requested = False
    unknown = ""
    positionals: list[str] = []
    ended = False

    for token in argv:
        if ended:
            positionals.append(token)
            continue
        if token == "--":
            ended = True
            continue
        if token in _HELP_FLAGS:
            help_requested = True
            continue
        if token in _VERSION_FLAGS:
            version_requested = True
            continue
        if len(token) > 1 and token.startswith("-"):
            if not unknown:
                unknown = token
            continue
        positionals.append(token)

    if help_requested:
        return "help", ""
    if version_requested:
        return "version", ""
    if unknown:
        return "error", unknown
    if not ended and positionals and positionals[0] == "print-agents":
        return "print-agents", ""
    task = positionals[0] if positionals else ""
    return "run", task


def _slot_summary(slot: str, settings: Settings) -> str:
    """One slot's command with whatever it actually resolved to.

    The startup line already names the commands; the model and arguments
    are what a reader cannot otherwise check without opening
    run-metadata.json after the fact. Empty parts are dropped so the
    common case stays as short as it was.
    """
    ref = agent_ref(slot, settings)
    model = agent_model(ref, settings)
    args = resolve_model_args(ref, settings)
    detail = "  ".join(
        part
        for part in (f"model={model}" if model else "", f"args={args}" if args else "")
        if part
    )
    return f"{ref.name} [{detail}]" if detail else ref.name


def _absolute_import_path(raw: str, startup_dir: Path) -> str:
    if not raw:
        return ""
    return str((startup_dir / raw).resolve())


def _configure_stdio() -> None:
    """Make console and redirected output safe for arbitrary agent Unicode."""

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def _print_resume_hint(
    run_id: str,
    use_worktree: bool,
    workspace: Path,
    printed: set,
    echo_err: Callable[..., None],
) -> None:
    if printed:
        return
    printed.add(True)
    if use_worktree:
        echo_err(
            "To resume this run:\n  cd {workspace} && "
            "RESUME_RUN={run_id} adversarial-ai-coding",
            workspace=workspace,
            run_id=run_id,
        )
    else:
        echo_err(
            "To resume this run:\n  RESUME_RUN={run_id} adversarial-ai-coding",
            run_id=run_id,
        )


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    stdin_isatty: bool | None = None,
) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ) if env is None else dict(env)
    startup_dir = Path.cwd()
    for key in ("IMPORT_SPEC", "IMPORT_PLAN"):
        if key in env:
            env[key] = _absolute_import_path(env[key], startup_dir)
    if stdin_isatty is None:
        stdin_isatty = sys.stdin.isatty()

    lang = resolve_lang(env)
    presenter = Presenter(Styler.plain(), lang)
    action, payload = _parse_argv(argv)
    if action == "help":
        presenter.out(USAGE)
        return 0
    if action == "version":
        presenter.out("adversarial-ai-coding {version}", version=__version__)
        return 0
    if action == "error":
        presenter.err("!! unrecognized option:{option}", option=payload)
        presenter.err(USAGE)
        return 1
    if action == "print-agents":
        try:
            print(write_agents_section(default_agents_template(env)), end="")
            return 0
        except PromptTemplateError as exc:
            emit_exception(presenter.err, exc)
            return 1

    task_arg = payload
    resume_run = env.get("RESUME_RUN", "")
    if not task_arg and not resume_run:
        presenter.err(USAGE)
        return 1

    state: RunState | None = None
    hint_printed: set = set()
    run_id = ""
    use_worktree = False
    workspace = Path.cwd()
    styler = presenter.styler
    try:
        styler = Styler.from_env(env)
        presenter = Presenter(styler, lang)
        task_source_kind, task_source_path = "literal", ""
        task = task_arg
        if task_arg and Path(task_arg).is_file():
            task_source_kind = "file"
            task_source_path = str(Path(task_arg).resolve())
            presenter.out("Reading request from file:{path}", path=task_arg)
            task = Path(task_arg).read_text(encoding="utf-8")

        snapshot: dict[str, str] = {}
        wf = Path(WORK_DIR)
        if resume_run:
            state = RunState.resume(wf / "state", resume_run)
            run_id = state.run_id
            snapshot = load_snapshot(state.state_dir)
            check_immutable(env, snapshot)
            task_snapshot = state.task_text()
            if task and task != task_snapshot:
                raise RunStateError(
                    "!! The request argument resolves to different text than "
                    "the resumed run's request snapshot.\n   Resume without a "
                    "request argument (the snapshot is used), or start a fresh "
                    "run."
                )
            task = task_snapshot
            task_arg = snapshot.get("TASK_ARG", "")
            task_source_kind = snapshot.get("TASK_SOURCE_KIND", "literal")
            task_source_path = snapshot.get("TASK_SOURCE_PATH", "")
            presenter.err(
                "Resuming run {run_id} (state: {state_dir})",
                run_id=run_id,
                state_dir=state.state_dir,
            )
        else:
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        settings = Settings.from_env(env, run_id, snapshot)
        settings = replace(
            settings,
            import_spec=_absolute_import_path(settings.import_spec, startup_dir),
            import_plan=_absolute_import_path(settings.import_plan, startup_dir),
        )
        use_worktree = settings.use_worktree

        # Pass the lookup explicitly so startup checks share the CLI's
        # injectable command resolver instead of a definition-time default.
        validate_agents(settings, which=shutil.which)
        if not is_inside_work_tree(Path.cwd()):
            presenter.err(
                "Run this script from the root of the target git repository."
            )
            return 1
        import_preflight(settings, env, fresh_run=not resume_run)
        dual_spec_preflight(settings, stdin_isatty)
        plan_gate_preflight(settings, stdin_isatty)

        presenter.out(
            "Workflow settings:A={agent_a}  B={agent_b}  "
            "DUAL_SPEC={dual_spec}  MAX_ROUNDS={max_rounds}  "
            "SPEC_DIR={spec_dir}  PHASES={phases}",
            agent_a=_slot_summary("A", settings),
            agent_b=_slot_summary("B", settings),
            dual_spec="1" if settings.dual_spec else "0",
            max_rounds=settings.max_rounds,
            spec_dir=settings.spec_dir,
            phases="1" if settings.phases else "0",
        )
        presenter.out("Request:{task}", task=task)
        if settings.import_spec:
            presenter.out(
                "Importing spec:{spec}{plan_part}  IMPORT_REVIEW={review}",
                spec=settings.import_spec,
                plan_part=(
                    f"  plan:{settings.import_plan}"
                    if settings.import_plan
                    else ""
                ),
                review="1" if settings.import_review else "0",
            )

        if resume_run:
            resume_workspace(
                snapshot.get("BRANCH", ""),
                state,
                Path.cwd(),
                presenter.err,
            )
        else:
            workspace = setup_workspace(settings, run_id, Path.cwd())
            if workspace != Path.cwd():
                os.chdir(workspace)
                presenter.out(
                    "Created worktree:{workspace} "
                    "(branch {branch}; "
                    "remove later with git worktree remove)",
                    workspace=workspace,
                    branch=current_branch(workspace),
                )
        workspace = Path.cwd()
        wf = workspace / WORK_DIR

        # Claim the ignored subtree before anything is written into it: the
        # "*" here is what keeps every machine-only artifact out of git, and
        # relying on another call to create the parent first would make that
        # guarantee depend on statement order.
        wf.mkdir(parents=True, exist_ok=True)
        (wf / ".gitignore").write_text("*\n", encoding="utf-8")

        archive = establish_run_archive(wf / "archive", run_id, settings)
        if resume_run:
            init_live_state(wf, resume=True)
        else:
            state = RunState.create(wf / "state", run_id, task)
            init_live_state(wf, resume=False)
        archive.write_run_metadata(spec_dir=settings.spec_dir, wf=str(wf))
        archive.write_log_metadata()
        archive.archive_task(task_arg, task_source_kind, task_source_path, task)
        (wf / "latest-run.txt").write_text(
            str(archive.run_dir) + "\n", encoding="utf-8"
        )
        archive.log_section("startup settings", "workflow", None, "startup", 0)

        bootstrap_agents_md(
            workspace,
            default_agents_template(env),
            presenter.out,
            presenter.err,
        )

        gate_cmd = (
            env.get("GATE_CMD")
            or snapshot.get("GATE_CMD")
            or detect_gate(workspace)
        )
        build_gate_cmd = (
            env.get("BUILD_GATE_CMD")
            or snapshot.get("BUILD_GATE_CMD")
            or detect_build_gate(workspace)
        )
        phase_gate_cmd = (
            env.get("PHASE_GATE_CMD") or snapshot.get("PHASE_GATE_CMD") or ""
        )
        if gate_cmd:
            presenter.out("Quality gate:{cmd}", cmd=gate_cmd)
        else:
            presenter.err(
                "(warning: no quality gate command detected; deterministic "
                "gates are disabled. Set GATE_CMD to enable one.)"
            )

        ctx = WorkflowContext(
            settings=settings,
            archive=archive,
            state=state,
            session=AgentSession(),
            workspace=workspace,
            wf=wf,
            prompts_dir=default_prompts_dir(env),
            spec_dir=workspace / settings.spec_dir,
            gate_cmd=gate_cmd,
            build_gate_cmd=build_gate_cmd,
            phase_gate_cmd=phase_gate_cmd,
            run_id=run_id,
            echo=presenter.out,
            echo_err=presenter.err,
            ask=bind_ask(_default_ask, lang),
        )
        ctx.spec_dir.mkdir(parents=True, exist_ok=True)
        branch = current_branch(workspace)
        # Before the first stage, so the commit-spec stage sweeps it into the
        # branch with git add -A semantics. A resumed run keeps the original.
        write_run_manifest(
            ctx.spec_dir,
            run_id=run_id,
            request=task,
            branch=branch,
            settings=settings,
        )

        write_snapshot(
            state.state_dir,
            snapshot_values(
                settings,
                branch=branch,
                gate_cmd=gate_cmd,
                build_gate_cmd=build_gate_cmd,
                phase_gate_cmd=phase_gate_cmd,
                task_arg=task_arg,
                task_source_kind=task_source_kind,
                task_source_path=task_source_path,
            ),
        )

        run_workflow(ctx, task)
        return 0

    except KeyboardInterrupt:
        _abort_message(
            130, state, run_id, use_worktree, workspace, hint_printed,
            presenter.err,
        )
        return 130
    except WorkflowAbort as exc:
        emit_exception(presenter.err, exc)
        _abort_message(
            exc.rc, state, run_id, use_worktree, workspace, hint_printed,
            presenter.err,
        )
        return exc.rc
    except (SettingsError, RunStateError, PromptTemplateError) as exc:
        emit_exception(presenter.err, exc)
        _abort_message(
            1, state, run_id, use_worktree, workspace, hint_printed,
            presenter.err,
        )
        return 1
    finally:
        if state is not None:
            state.release_lock()


def _abort_message(
    rc: int,
    state,
    run_id,
    use_worktree,
    workspace,
    hint_printed,
    echo_err,
) -> None:
    if rc != 0 and state is not None and not state.is_completed():
        echo_err("!! Workflow interrupted (exit={rc}).", rc=rc)
        _print_resume_hint(
            run_id, use_worktree, workspace, hint_printed, echo_err
        )


def main_entry() -> None:
    _configure_stdio()
    sys.exit(main())
