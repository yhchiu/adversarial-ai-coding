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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from .agents import agent_model, resolve_model_args
from .config import Settings

METRICS_HEADER = [
    "run_id", "stage", "role", "agent", "round",
    "duration_s", "cost_usd", "model", "model_args", "generated_at",
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
    """One run's archive under .workflow/runs/<run-id>[-N]/ (sh:593-606)."""

    run_dir: Path
    run_id: str
    settings: Settings
    log_path: Path
    metrics_path: Path

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
        agent: str = "workflow",
        stage: str = "startup",
        round: int = 0,
        now: datetime | None = None,
    ) -> None:
        payload = {
            "generated_at": generated_at(now),
            "generator_role": role,
            "agent": agent,
            "model": agent_model(agent, self.settings),
            "model_args": resolve_model_args(agent, self.settings),
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
        agent: str = "workflow",
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
        agent: str = "workflow",
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
        agent: str,
        slug: str,
        attempt: int,
        rc: int,
        agent_out: Path,
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

    def write_run_metadata(
        self, *, spec_dir: str, wf: str, now: datetime | None = None
    ) -> None:
        dst = self.art_path("run-metadata.json")
        settings = self.settings
        payload = {
            "generated_at": generated_at(now),
            "run_id": self.run_id,
            "spec_dir": spec_dir,
            "wf": wf,
            "runs_dir": str(self.run_dir.parent),
            "wf_run": str(self.run_dir),
            "log": str(self.log_path),
            "metrics": str(self.metrics_path),
            "agent_a": settings.agent_a,
            "model_a": agent_model(settings.agent_a, settings),
            "args_a": resolve_model_args(settings.agent_a, settings),
            "agent_b": settings.agent_b,
            "model_b": agent_model(settings.agent_b, settings),
            "args_b": resolve_model_args(settings.agent_b, settings),
            "dual_spec": "1" if settings.dual_spec else "0",
            "max_rounds": str(settings.max_rounds),
            "auto_branch": "1" if settings.auto_branch else "0",
            "use_worktree": "1" if settings.use_worktree else "0",
        }
        dst.write_text(json.dumps(payload), encoding="utf-8")

    def write_log_metadata(self, now: datetime | None = None) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        settings = self.settings
        payload = {
            "generated_at": generated_at(now),
            "generator_role": "workflow",
            "run_id": self.run_id,
            "log_path": str(self.log_path),
            "agent_a": settings.agent_a,
            "model_a": agent_model(settings.agent_a, settings),
            "args_a": resolve_model_args(settings.agent_a, settings),
            "agent_b": settings.agent_b,
            "model_b": agent_model(settings.agent_b, settings),
            "args_b": resolve_model_args(settings.agent_b, settings),
            "dual_spec": "1" if settings.dual_spec else "0",
        }
        meta_path = self.log_path.with_name(self.log_path.name + ".meta.json")
        _atomic_write(meta_path, json.dumps(payload))

    def log_section(
        self,
        title: str,
        role: str = "workflow",
        agent: str = "workflow",
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
            f"[{generated_at(now)}] {title} | role={role} agent={agent} "
            f"model={agent_model(agent, self.settings)} "
            f"args={resolve_model_args(agent, self.settings)} "
            f"stage={stage} round={round}\n"
            + "-" * 80
        )
        with self.log_path.open("a", encoding="utf-8") as log:
            log.write(banner + "\n")
        echo(banner)

    def metric(
        self,
        role: str,
        agent: str,
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
                agent,
                round,
                seconds,
                cost,
                agent_model(agent, self.settings),
                resolve_model_args(agent, self.settings),
                generated_at(now),
            ],
        )

    def archive_git_state(
        self,
        role: str = "worker",
        agent: str = "workflow",
        slug: str = "git-state",
        stage: str = "startup",
        round: int = 0,
        cwd: Path | None = None,
    ) -> None:
        import subprocess

        def git(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=check,
            )

        if git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return

        status_art = self.art_path(f"{slug}-git-status.txt")
        status_art.write_text(
            git("status", "--porcelain").stdout, encoding="utf-8"
        )
        self.write_meta(status_art, role, agent, stage, round)

        diff_art = self.art_path(f"{slug}-git-diff.patch")
        chunks = [
            "# git diff --binary HEAD --\n",
            git("diff", "--binary", "HEAD", "--").stdout,
            "\n",
            "# untracked files\n",
        ]
        listing = git("ls-files", "--others", "--exclude-standard", "-z").stdout
        for name in filter(None, listing.split("\0")):
            chunks += [
                f"\n## {name}\n",
                git("diff", "--no-index", "--binary", "--", os.devnull, name).stdout,
            ]
        diff_art.write_text("".join(chunks), encoding="utf-8")
        self.write_meta(diff_art, role, agent, stage, round)


def establish_run_archive(
    runs_dir: Path, run_id: str, settings: Settings
) -> RunArchive:
    base = runs_dir / run_id
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = runs_dir / f"{run_id}-{suffix}"
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
