"""CLI entry point: startup checks, state claiming, abort handling.

Port of adversarial-ai-coding.sh:91-123, 332-339, 1813-1894, and
2006-2008. jq is no longer required; resume hints name the console script.
"""

from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping

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
from .workflow import WorkflowContext, plan_gate_preflight, run_workflow

USAGE = """Usage:adversarial-ai-coding "task description"
      adversarial-ai-coding task.md         # If the argument is a file, use its contents as the task
      adversarial-ai-coding print-agents    # Print the AGENTS.md rule template and exit"""


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
) -> None:
    if printed:
        return
    printed.add(True)
    if use_worktree:
        print(
            f"To resume this run:\n  cd {workspace} && "
            f"RESUME_RUN={run_id} adversarial-ai-coding",
            file=sys.stderr,
        )
    else:
        print(
            f"To resume this run:\n  RESUME_RUN={run_id} adversarial-ai-coding",
            file=sys.stderr,
        )


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    *,
    stdin_isatty: bool | None = None,
) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = dict(os.environ) if env is None else dict(env)
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
    try:
        task_source_kind, task_source_path = "literal", ""
        task = task_arg
        if task_arg and Path(task_arg).is_file():
            task_source_kind = "file"
            task_source_path = str(Path(task_arg).resolve())
            print(f"Reading task description from file:{task_arg}")
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
            print(
                f"Resuming run {run_id} (state: {state.state_dir})", file=sys.stderr
            )
        else:
            run_id = datetime.now().strftime("%Y%m%d-%H%M%S")

        settings = Settings.from_env(env, run_id, snapshot)
        use_worktree = settings.use_worktree

        # Pass the lookup explicitly so startup checks share the CLI's
        # injectable command resolver instead of a definition-time default.
        validate_agents(settings, which=shutil.which)
        if not is_inside_work_tree(Path.cwd()):
            print(
                "Run this script from the root of the target git repository.",
                file=sys.stderr,
            )
            return 1
        dual_spec_preflight(settings, stdin_isatty)
        plan_gate_preflight(settings, stdin_isatty)

        print(
            f"Workflow settings:A={settings.agent_a}  B={settings.agent_b}  "
            f"DUAL_SPEC={'1' if settings.dual_spec else '0'}  "
            f"MAX_ROUNDS={settings.max_rounds}  SPEC_DIR={settings.spec_dir}  "
            f"PHASES={'1' if settings.phases else '0'}"
        )
        print(f"Task:{task}")

        if resume_run:
            resume_workspace(
                snapshot.get("BRANCH", ""),
                state,
                Path.cwd(),
                lambda message: print(message, file=sys.stderr),
            )
        else:
            workspace = setup_workspace(settings, run_id, Path.cwd())
            if workspace != Path.cwd():
                os.chdir(workspace)
                print(
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
            print,
            lambda message: print(message, file=sys.stderr),
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
            print(f"Quality gate:{gate_cmd}")
        else:
            print(
                "(warning: no quality gate command detected; deterministic "
                "gates are disabled. Set GATE_CMD to enable one.)",
                file=sys.stderr,
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
            130, state, run_id, use_worktree, workspace, hint_printed
        )
        return 130
    except WorkflowAbort as exc:
        print(exc, file=sys.stderr)
        _abort_message(
            exc.rc, state, run_id, use_worktree, workspace, hint_printed
        )
        return exc.rc
    except (SettingsError, RunStateError, PromptTemplateError) as exc:
        print(exc, file=sys.stderr)
        _abort_message(1, state, run_id, use_worktree, workspace, hint_printed)
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
) -> None:
    if rc != 0 and state is not None and not state.is_completed():
        print(f"!! Workflow interrupted (exit={rc}).", file=sys.stderr)
        _print_resume_hint(
            run_id, use_worktree, workspace, hint_printed
        )


def main_entry() -> None:
    _configure_stdio()
    sys.exit(main())
