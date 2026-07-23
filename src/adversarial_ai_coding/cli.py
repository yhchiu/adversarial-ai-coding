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

from .agents import AgentSession, validate_agents
from .archive import establish_run_archive
from .config import Settings, SettingsError, WorkflowAbort
from .dual_spec import dual_spec_preflight
from .gates import detect_build_gate, detect_gate
from .gitops import (
    current_branch,
    is_inside_work_tree,
    resume_workspace,
    setup_workspace,
)
from .imports import import_preflight
from .prompts import (
    PromptTemplateError,
    bootstrap_agents_md,
    default_agents_template,
    default_prompts_dir,
    write_agents_section,
)
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
from .workflow import WorkflowContext, plan_gate_preflight, run_workflow

USAGE = """Usage:adversarial-ai-coding "task description"
      adversarial-ai-coding task.md         # If the argument is a file, use its contents as the task
      adversarial-ai-coding print-agents    # Print the AGENTS.md rule template and exit"""


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
    echo_err: Callable[[str], None],
) -> None:
    if printed:
        return
    printed.add(True)
    if use_worktree:
        echo_err(
            f"To resume this run:\n  cd {workspace} && "
            f"RESUME_RUN={run_id} adversarial-ai-coding"
        )
    else:
        echo_err(
            f"To resume this run:\n  RESUME_RUN={run_id} adversarial-ai-coding"
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

    task_arg = argv[0] if argv else ""
    resume_run = env.get("RESUME_RUN", "")
    if not task_arg and not resume_run:
        print(USAGE, file=sys.stderr)
        return 1
    if task_arg == "print-agents":
        try:
            print(write_agents_section(default_agents_template(env)), end="")
            return 0
        except PromptTemplateError as exc:
            print(exc, file=sys.stderr)
            return 1

    state: RunState | None = None
    hint_printed: set = set()
    run_id = ""
    use_worktree = False
    workspace = Path.cwd()
    styler = Styler.plain()
    try:
        styler = Styler.from_env(env)
        task_source_kind, task_source_path = "literal", ""
        task = task_arg
        if task_arg and Path(task_arg).is_file():
            task_source_kind = "file"
            task_source_path = str(Path(task_arg).resolve())
            styler.out(f"Reading task description from file:{task_arg}")
            task = Path(task_arg).read_text(encoding="utf-8")

        snapshot: dict[str, str] = {}
        wf = Path(".workflow")
        if resume_run:
            state = RunState.resume(wf / "state", resume_run)
            run_id = state.run_id
            snapshot = load_snapshot(state.state_dir)
            check_immutable(env, snapshot)
            task_snapshot = state.task_text()
            if task and task != task_snapshot:
                raise RunStateError(
                    "!! The task argument resolves to different text than the "
                    "resumed run's task snapshot.\n   Resume without a task "
                    "argument (the snapshot is used), or start a fresh run."
                )
            task = task_snapshot
            task_arg = snapshot.get("TASK_ARG", "")
            task_source_kind = snapshot.get("TASK_SOURCE_KIND", "literal")
            task_source_path = snapshot.get("TASK_SOURCE_PATH", "")
            styler.err(
                f"Resuming run {run_id} (state: {state.state_dir})"
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
            styler.err(
                "Run this script from the root of the target git repository."
            )
            return 1
        import_preflight(settings, env, fresh_run=not resume_run)
        dual_spec_preflight(settings, stdin_isatty)
        plan_gate_preflight(settings, stdin_isatty)

        styler.out(
            f"Workflow settings:A={settings.agent_a}  B={settings.agent_b}  "
            f"DUAL_SPEC={'1' if settings.dual_spec else '0'}  "
            f"MAX_ROUNDS={settings.max_rounds}  SPEC_DIR={settings.spec_dir}  "
            f"PHASES={'1' if settings.phases else '0'}"
        )
        print(f"Task:{task}")
        if settings.import_spec:
            styler.out(
                f"Importing spec:{settings.import_spec}"
                + (
                    f"  plan:{settings.import_plan}"
                    if settings.import_plan
                    else ""
                )
                + f"  IMPORT_REVIEW={'1' if settings.import_review else '0'}"
            )

        if resume_run:
            resume_workspace(
                snapshot.get("BRANCH", ""),
                state,
                Path.cwd(),
                styler.err,
            )
        else:
            workspace = setup_workspace(settings, run_id, Path.cwd())
            if workspace != Path.cwd():
                os.chdir(workspace)
                styler.out(
                    f"Created worktree:{workspace} (branch auto/{run_id}; "
                    "remove later with git worktree remove)"
                )
        workspace = Path.cwd()
        wf = workspace / ".workflow"

        archive = establish_run_archive(wf / "runs", run_id, settings)
        if resume_run:
            init_live_state(wf, resume=True)
        else:
            state = RunState.create(wf / "state", run_id, task)
            init_live_state(wf, resume=False)
        (wf / ".gitignore").write_text("*\n", encoding="utf-8")
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
            styler.out,
            styler.err,
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
            styler.out(f"Quality gate:{gate_cmd}")
        else:
            styler.err(
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
            echo=styler.out,
            echo_err=styler.err,
        )
        ctx.spec_dir.mkdir(parents=True, exist_ok=True)

        write_snapshot(
            state.state_dir,
            snapshot_values(
                settings,
                branch=current_branch(workspace),
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
            styler.err,
        )
        return 130
    except WorkflowAbort as exc:
        styler.err(str(exc))
        _abort_message(
            exc.rc, state, run_id, use_worktree, workspace, hint_printed,
            styler.err,
        )
        return exc.rc
    except (SettingsError, RunStateError, PromptTemplateError) as exc:
        styler.err(str(exc))
        _abort_message(
            1, state, run_id, use_worktree, workspace, hint_printed,
            styler.err,
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
        echo_err(f"!! Workflow interrupted (exit={rc}).")
        _print_resume_hint(
            run_id, use_worktree, workspace, hint_printed, echo_err
        )


def main_entry() -> None:
    _configure_stdio()
    sys.exit(main())
