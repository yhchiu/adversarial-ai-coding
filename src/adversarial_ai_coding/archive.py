"""Run archive helpers — pure text/CSV parts.

Port of adversarial-ai-coding.sh:377-398 (generated_at, safe_slug) and
424-452 (csv_row, write_csv_row, metrics_summary). The artifact and
run-directory I/O functions join this module in plan 3.
"""

from __future__ import annotations

import csv
import json
import os
import shutil as _shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .agents import AgentRef, agent_model, agent_ref, resolve_model_args
from .config import Settings

METRICS_HEADER = [
    "run_id", "stage", "role", "agent", "round",
    "duration_s", "cost_usd", "model", "model_args", "generated_at",
    "agent_slot",
]
METRICS_FIELDNAMES = METRICS_HEADER

_SLUG_UNSAFE = set("/\\ :;|<>\"'")


def generated_at(now: datetime | None = None) -> str:
    dt = now if now is not None else datetime.now().astimezone()
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


def safe_slug(s: str) -> str:
    return "".join("-" if c in _SLUG_UNSAFE else c for c in s)


def csv_row(fields: Sequence[object]) -> str:
    if isinstance(fields, (str, bytes)):
        raise TypeError("csv_row expects a sequence of fields, not a bare string")
    quoted = ('"' + str(f).replace('"', '""') + '"' for f in fields)
    return ",".join(quoted) + "\n"


def write_csv_row(path: Path, fields: Sequence[object]) -> None:
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write(csv_row(fields))


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _num(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return 0.0  # awk treats non-numeric fields as 0


def metrics_summary(path: Path) -> str:
    if not path.is_file():
        return ""
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    stats: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if len(row) < 7:
            continue
        st = stats.setdefault(row[1], {"calls": 0, "round": 0, "secs": 0.0, "cost": 0.0})
        st["calls"] += 1
        st["round"] = max(st["round"], _num(row[4]))
        st["secs"] += _num(row[5])
        st["cost"] += _num(row[6])
    lines = [
        "  %-14s AI calls %d, review rounds %d, %d seconds, $%.4f"
        % (stage, st["calls"], int(st["round"]), int(st["secs"]), st["cost"])
        for stage, st in stats.items()
    ]
    return "\n".join(lines) + ("\n" if lines else "")


@dataclass
class RunArchive:
    """One run's archive under aac/.run/archive/<run-id>[-N]/ (sh:593-606)."""

    run_dir: Path
    run_id: str
    settings: Settings
    log_path: Path
    metrics_path: Path
    # cwd -> (is a work tree, path to its index). See _work_tree.
    _work_trees: dict[str, tuple[bool, Path | None]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def art_path(self, name: str) -> Path:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        seq_file = self.run_dir / ".artifact-seq"
        seq = (
            int(seq_file.read_text(encoding="utf-8").strip())
            if seq_file.is_file()
            else 0
        )
        seq += 1
        _atomic_write(seq_file, f"{seq}\n")
        return self.run_dir / f"{seq:03d}-{name}"

    def write_meta(
        self,
        artifact: Path,
        role: str = "workflow",
        agent: AgentRef | None = None,
        stage: str = "startup",
        round: int = 0,
        now: datetime | None = None,
    ) -> None:
        payload = {
            "generated_at": generated_at(now),
            "generator_role": role,
            "agent": agent.name if agent is not None else "workflow",
            "agent_slot": agent.slot if agent is not None else "workflow",
            "model": agent_model(agent, self.settings) if agent is not None else "",
            "model_args": (
                resolve_model_args(agent, self.settings) if agent is not None else ""
            ),
            "stage": stage,
            "round": str(round),
            "run_id": self.run_id,
            "artifact": str(artifact),
        }
        meta_path = artifact.with_name(artifact.name + ".meta.json")
        _atomic_write(meta_path, json.dumps(payload))

    def archive_snapshot(
        self,
        src: Path,
        name: str,
        role: str = "workflow",
        agent: AgentRef | None = None,
        stage: str = "startup",
        round: int = 0,
        now: datetime | None = None,
    ) -> Path | None:
        if not src.is_file():
            return None
        dst = self.art_path(name)
        _shutil.copyfile(src, dst)
        self.write_meta(dst, role, agent, stage, round, now)
        return dst

    def archive_text(
        self,
        name: str,
        text: str,
        role: str = "workflow",
        agent: AgentRef | None = None,
        stage: str = "startup",
        round: int = 0,
        now: datetime | None = None,
    ) -> Path:
        dst = self.art_path(name)
        dst.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
        self.write_meta(dst, role, agent, stage, round, now)
        return dst

    def archive_task(
        self, task_arg: str, kind: str, source_path: str, resolved: str
    ) -> None:
        lines = ["# Task Source", "", f"- kind: {kind}", f"- argument: {task_arg}"]
        if kind == "file":
            lines.append(f"- path: {source_path}")
        lines += ["", "```", resolved.rstrip("\n"), "```"]
        src_art = self.art_path("task-source.md")
        src_art.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.write_meta(src_art)
        task_art = self.art_path("task.txt")
        task_art.write_text(resolved.rstrip("\n") + "\n", encoding="utf-8")
        self.write_meta(task_art)

    def archive_agent_attempt(
        self,
        role: str,
        agent: AgentRef,
        slug: str,
        attempt: int,
        rc: int,
        agent_out: Path,
        raw_out: Path,
        stage: str,
        round: int,
    ) -> None:
        dst = self.art_path(f"{slug}-attempt-{attempt}-rc{rc}.raw")
        if agent_out.is_file():
            _shutil.copyfile(agent_out, dst)
        else:
            dst.write_text(
                "(agent output was not written for this attempt)\n",
                encoding="utf-8",
            )
        self.write_meta(dst, role, agent, stage, round)
        if raw_out != agent_out and raw_out.is_file():
            raw_dst = self.art_path(f"{slug}-attempt-{attempt}-rc{rc}.cli.raw")
            _shutil.copyfile(raw_out, raw_dst)
            self.write_meta(raw_dst, role, agent, stage, round)

    def write_run_metadata(
        self, *, spec_dir: str, wf: str, now: datetime | None = None
    ) -> None:
        dst = self.art_path("run-metadata.json")
        settings = self.settings
        ref_a = agent_ref("A", settings)
        ref_b = agent_ref("B", settings)
        payload = {
            "generated_at": generated_at(now),
            "run_id": self.run_id,
            "spec_dir": spec_dir,
            "wf": wf,
            "archive_dir": str(self.run_dir.parent),
            "wf_run": str(self.run_dir),
            "log": str(self.log_path),
            "metrics": str(self.metrics_path),
            "agent_a": settings.agent_a,
            "model_a": agent_model(ref_a, settings),
            "args_a": resolve_model_args(ref_a, settings),
            "agent_b": settings.agent_b,
            "model_b": agent_model(ref_b, settings),
            "args_b": resolve_model_args(ref_b, settings),
            "impl_agent": settings.impl_agent,
            "impl_model": settings.impl_model,
            "impl_args": settings.impl_args,
            "dual_spec": "1" if settings.dual_spec else "0",
            "max_rounds": str(settings.max_rounds),
            "auto_branch": "1" if settings.auto_branch else "0",
            "use_worktree": "1" if settings.use_worktree else "0",
        }
        dst.write_text(json.dumps(payload), encoding="utf-8")

    def write_log_metadata(self, now: datetime | None = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        settings = self.settings
        ref_a = agent_ref("A", settings)
        ref_b = agent_ref("B", settings)
        payload = {
            "generated_at": generated_at(now),
            "generator_role": "workflow",
            "run_id": self.run_id,
            "log_path": str(self.log_path),
            "agent_a": settings.agent_a,
            "model_a": agent_model(ref_a, settings),
            "args_a": resolve_model_args(ref_a, settings),
            "agent_b": settings.agent_b,
            "model_b": agent_model(ref_b, settings),
            "args_b": resolve_model_args(ref_b, settings),
            "impl_agent": settings.impl_agent,
            "impl_model": settings.impl_model,
            "impl_args": settings.impl_args,
            "dual_spec": "1" if settings.dual_spec else "0",
        }
        meta_path = self.log_path.with_name(self.log_path.name + ".meta.json")
        _atomic_write(meta_path, json.dumps(payload))

    def log_section(
        self,
        title: str,
        role: str = "workflow",
        agent: AgentRef | None = None,
        stage: str = "startup",
        round: int = 0,
        echo: Callable[[str], None] = print,
        now: datetime | None = None,
    ) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        banner = (
            "\n"
            + "-" * 80
            + "\n"
            f"[{generated_at(now)}] {title} | role={role} "
            f"agent={agent.name if agent is not None else 'workflow'} "
            f"model={agent_model(agent, self.settings) if agent is not None else ''} "
            f"args={resolve_model_args(agent, self.settings) if agent is not None else ''} "
            f"stage={stage} round={round} "
            f"agent_slot={agent.slot if agent is not None else 'workflow'}\n"
            + "-" * 80
        )
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(banner + "\n")
        echo(banner)

    def metric(
        self,
        role: str,
        agent: AgentRef,
        round: int,
        seconds: int,
        cost: str,
        stage: str,
        now: datetime | None = None,
    ) -> None:
        if not self.metrics_path.parent.is_dir():
            return
        if not self.metrics_path.is_file():
            self.metrics_path.write_text(
                ",".join(METRICS_HEADER) + "\n", encoding="utf-8"
            )
        write_csv_row(
            self.metrics_path,
            [
                self.run_id,
                stage,
                role,
                agent.name,
                round,
                seconds,
                cost,
                agent_model(agent, self.settings),
                resolve_model_args(agent, self.settings),
                generated_at(now),
                agent.slot,
            ],
        )

    def _work_tree(self, cwd: Path | None) -> tuple[bool, Path | None]:
        """Is cwd a work tree, and where is its index? Asked once per tree.

        archive_git_state runs after every single agent call, and both
        answers are fixed for the life of a run, so asking git each time
        was one wasted process per call.
        """
        import subprocess

        key = str(cwd) if cwd is not None else ""
        if key in self._work_trees:
            return self._work_trees[key]
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree", "--git-path", "index"],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        answer: tuple[bool, Path | None] = (False, None)
        if proc.returncode == 0:
            lines = proc.stdout.splitlines()
            index = Path(lines[1]) if len(lines) > 1 and lines[1] else None
            # --git-path answers relative to the working directory.
            if index is not None and not index.is_absolute() and cwd is not None:
                index = Path(cwd) / index
            answer = (True, index)
        self._work_trees[key] = answer
        return answer

    def archive_git_state(
        self,
        role: str = "worker",
        agent: AgentRef | None = None,
        slug: str = "git-state",
        stage: str = "startup",
        round: int = 0,
        cwd: Path | None = None,
    ) -> None:
        import subprocess
        import tempfile

        def git(
            *args: str, env: dict[str, str] | None = None
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )

        def diff_text(index_path: Path | None, untracked: bool) -> str:
            """Tracked changes plus untracked file content, in one git call.

            Untracked files only reach a diff once something records them,
            and the obvious way to record them -- git add -N -- would leave
            intent-to-add entries behind in the repository the worker is
            about to commit (docs/plans/20260708_workflow_run_archive_v3.md,
            review #8-5). Point GIT_INDEX_FILE at a throwaway copy of the
            index instead: git writes the intent-to-add entries there, the
            repository's own index is never opened for writing, and there is
            no window in which a crash could leave the run dirty.

            The old code walked untracked files with ls-files and spawned one
            git diff --no-index per file, which was the largest single source
            of child processes in a run.
            """
            if not untracked or index_path is None or not index_path.is_file():
                # Nothing untracked to fold in, or no index to copy (an
                # unborn repository): one plain diff says everything.
                header = "# git diff --binary HEAD --\n"
                return header + git("diff", "--binary", "HEAD", "--").stdout

            handle, scratch_name = tempfile.mkstemp(prefix="aac-index-")
            os.close(handle)
            scratch = Path(scratch_name)
            try:
                _shutil.copyfile(index_path, scratch)
                env = {**os.environ, "GIT_INDEX_FILE": str(scratch)}
                git("add", "-N", "-A", env=env)
                header = (
                    "# git diff --binary HEAD -- (untracked files included via\n"
                    "# a scratch index; the repository's own index is untouched)\n"
                )
                return header + git("diff", "--binary", "HEAD", "--", env=env).stdout
            finally:
                scratch.unlink(missing_ok=True)

        inside, index_path = self._work_tree(cwd)
        if not inside:
            return

        status = git("status", "--porcelain").stdout
        status_art = self.art_path(f"{slug}-git-status.txt")
        status_art.write_text(status, encoding="utf-8")
        self.write_meta(status_art, role, agent, stage, round)

        # The status we already have says whether the scratch-index dance
        # below can buy anything: "??" is porcelain's untracked marker.
        untracked = any(
            line.startswith("??") for line in status.splitlines()
        )
        diff_art = self.art_path(f"{slug}-git-diff.patch")
        diff_art.write_text(diff_text(index_path, untracked), encoding="utf-8")
        self.write_meta(diff_art, role, agent, stage, round)


def establish_run_archive(
    archive_root: Path, run_id: str, settings: Settings
) -> RunArchive:
    base = archive_root / run_id
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = archive_root / f"{run_id}-{suffix}"
        suffix += 1
    logs = candidate / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return RunArchive(
        run_dir=candidate,
        run_id=run_id,
        settings=settings,
        log_path=logs / "001-run.log",
        metrics_path=candidate / "metrics.csv",
    )
