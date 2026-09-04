"""The committed per-run manifest, and the index built from it.

aac/docs/<RUN_ID>/ is the only half of the artifact root git keeps
(docs/adr/0001-single-aac-root-for-run-artifacts.md). Everything that says
what a run was about otherwise lives in aac/.run/state/<RUN_ID>/task.txt,
which the nested ".gitignore" containing "*" keeps out of every branch and
therefore out of every clone. run.json carries those facts across into the
committed half, so a reader coming back to a repository months later can
tell the timestamped directories apart without opening each spec.

Two properties are deliberate:

- Workflow-owned. No prompt asks an agent to produce it, so no review or
  revise round can drop it, and an imported spec is not second-class.
- Write-once. A resume reuses the run id and must not rewrite the original
  start time, so an existing manifest is left exactly as it was.

Nothing parses it as control flow. A missing, unreadable, or unknown-schema
manifest degrades to a spec.md title and then to no title at all, because a
run listing that refuses to print is worse than one with a blank cell.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .archive import generated_at
from .config import Settings

MANIFEST_NAME = "run.json"
MANIFEST_SCHEMA = 1

# The request is a title here, not the record: aac/docs/<RUN_ID>/spec.md is
# the full statement. 72 matches the pull-request title cut in finish().
TITLE_WIDTH = 72

STATUS_COMPLETED = "completed"
STATUS_UNFINISHED = "unfinished"
STATUS_UNKNOWN = "unknown"


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_run_manifest(
    spec_dir: Path,
    *,
    run_id: str,
    request: str,
    branch: str,
    settings: Settings,
    now: datetime | None = None,
) -> Path | None:
    """Record this run's identity in the committed half. Write-once.

    Returns the path when the manifest was created, None when one was
    already there (a resumed attempt, or a re-run of the same id).
    """

    path = spec_dir / MANIFEST_NAME
    if path.exists():
        return None
    payload = {
        "schema": MANIFEST_SCHEMA,
        "run_id": run_id,
        "request": request,
        "started_at": generated_at(now),
        "branch": branch,
        "agent_a": settings.agent_a,
        "agent_b": settings.agent_b,
        "dual_spec": settings.dual_spec,
        "phases": settings.phases,
        "imported_spec": bool(settings.import_spec),
    }
    spec_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def first_line(text: str, width: int = TITLE_WIDTH) -> str:
    """The first non-blank line, collapsed and cut to one column cell."""

    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped:
            return stripped[:width]
    return ""


def spec_title(spec_path: Path, width: int = TITLE_WIDTH) -> str:
    """The spec's first Markdown H1, for runs older than the manifest.

    No prompt requires a title (resources/prompts/write-spec.md asks for
    sections, not a heading), so this is a best-effort fallback and never a
    contract.
    """

    try:
        text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        if line.startswith("# "):
            return " ".join(line[2:].split())[:width]
    return ""


def read_manifest(spec_dir: Path) -> dict:
    """The manifest as a dict, or {} for anything unusable."""

    try:
        data = json.loads((spec_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        return {}
    return data


def run_status(state_root: Path, run_id: str) -> str:
    """Derived, never stored: a stored status lies when a run is killed.

    Unknown is the honest answer in a fresh clone, where aac/.run/ was never
    committed and the state directory does not exist at all.
    """

    state_dir = state_root / run_id
    if (state_dir / "completed").is_file():
        return STATUS_COMPLETED
    if state_dir.is_dir():
        return STATUS_UNFINISHED
    return STATUS_UNKNOWN


@dataclass(frozen=True)
class RunEntry:
    run_id: str
    title: str
    status: str
    started_at: str
    path: Path


def load_run_entries(docs_root: Path, state_root: Path) -> list[RunEntry]:
    """Every run directory under docs_root, newest run id first."""

    if not docs_root.is_dir():
        return []
    entries: list[RunEntry] = []
    for spec_dir in sorted(docs_root.iterdir(), reverse=True):
        if not spec_dir.is_dir():
            continue
        manifest = read_manifest(spec_dir)
        title = first_line(str(manifest.get("request", "")))
        if not title:
            title = spec_title(spec_dir / "spec.md")
        entries.append(
            RunEntry(
                run_id=str(manifest.get("run_id") or spec_dir.name),
                title=title,
                status=run_status(state_root, spec_dir.name),
                started_at=str(manifest.get("started_at", "")),
                path=spec_dir,
            )
        )
    return entries


def format_run_index(entries: list[RunEntry]) -> str:
    """A grep-friendly fixed-column table; empty string for no runs."""

    if not entries:
        return ""
    header = ("RUN_ID", "STATUS", "REQUEST")
    rows = [(entry.run_id, entry.status, entry.title or "-") for entry in entries]
    widths = [
        max(len(row[column]) for row in (header, *rows)) for column in range(2)
    ]
    lines = [
        f"{row[0]:<{widths[0]}}  {row[1]:<{widths[1]}}  {row[2]}".rstrip()
        for row in (header, *rows)
    ]
    return "\n".join(lines) + "\n"
