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

Finding the manifests is its own problem: SPEC_DIR moves a run's documents
anywhere it likes, so the default location is a convention rather than a
guarantee. discover_spec_dirs() unions the three sources that between them
cover every run that can still be found — see its docstring.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from .archive import generated_at
from .config import DOCS_ROOT, WORK_DIR, Settings

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


def _run_git_default(args: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, proc.stdout


def _snapshot_spec_dir(state_dir: Path) -> str:
    """Where one run wrote its documents, per its settings snapshot.

    Deliberately not runstate.load_snapshot: that one refuses anything it
    does not fully understand because a bad snapshot must never be resumed.
    A listing has no such stake, so a damaged snapshot costs one row's
    location, not the whole command.
    """

    try:
        data = json.loads((state_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    spec_dir = data.get("spec_dir")
    return spec_dir if isinstance(spec_dir, str) else ""


def _tracked_manifest_dirs(root: Path, run_git) -> list[Path]:
    """Directories holding a manifest git tracks, wherever SPEC_DIR put them.

    After a clone this is the only source left: aac/.run/ is never
    committed, so the snapshots that recorded a custom SPEC_DIR are gone
    while the manifests themselves are still on the branch. Runs committed
    on branches this checkout does not have stay invisible, which is the
    same thing every other git-aware tool reports.
    """

    rc, out = run_git(["ls-files", "-z", "--", f"*{MANIFEST_NAME}"], root)
    if rc != 0:
        return []
    return [
        root / name
        for name in (part for part in out.split("\0") if part)
        # The pathspec is a suffix match, so "myrun.json" reaches here too.
        if PurePosixPath(name).name == MANIFEST_NAME
    ]


def discover_spec_dirs(root: Path, run_git=_run_git_default) -> list[Path]:
    """Every directory that might hold a run's documents.

    SPEC_DIR moves a run's documents anywhere, so a scan of the default
    location alone would silently omit those runs. Three sources cover the
    ways a run can still be found, and each covers a gap the others leave:

    1. `aac/docs/*/` — the default, with no git and no state needed.
    2. The settings snapshot of every run in `aac/.run/state/` — catches a
       custom SPEC_DIR, including a run that stopped before its first
       commit, and is the only source that knows about an uncommitted one.
    3. Manifests git tracks — the only source that survives a clone.
    """

    found: list[Path] = []
    docs_root = root / DOCS_ROOT
    if docs_root.is_dir():
        found += [path for path in docs_root.iterdir() if path.is_dir()]
    state_root = root / WORK_DIR / "state"
    if state_root.is_dir():
        for state_dir in state_root.iterdir():
            spec_dir = _snapshot_spec_dir(state_dir)
            if spec_dir:
                # An absolute SPEC_DIR wins the join, as it does in the run.
                found.append(root / spec_dir)
    found += [path.parent for path in _tracked_manifest_dirs(root, run_git)]

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved in seen or not path.is_dir():
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


@dataclass(frozen=True)
class RunEntry:
    run_id: str
    title: str
    status: str
    started_at: str
    path: Path
    default_location: bool
    # The whole request, untouched. title is this reduced twice — first line
    # only, then cut to a column width — so a listing stays scannable; the
    # detail view needs the original back, and a run with no manifest has
    # nothing to give here at all.
    request: str = ""


def load_run_entries(
    root: Path,
    run_git=_run_git_default,
    *,
    prefer_spec_title: bool = False,
) -> list[RunEntry]:
    """Every run discoverable from root, newest run id first.

    One run is one row even when two sources find it, which they routinely
    do: a committed run in the default location is reported by all three.

    prefer_spec_title swaps which source names a run. The default is the
    request, because the workflow wrote it and it is therefore always
    there. The spec heading often reads better — a request submitted as a
    file frequently starts with a line like "## Goal", which names every
    run identically — but no prompt asks an agent for a heading, so it is
    an opt-in preference and never a guarantee. Either way the other
    source is the fallback, so a row is never left blank by the choice.
    """

    state_root = root / WORK_DIR / "state"
    entries: dict[str, RunEntry] = {}
    for spec_dir in discover_spec_dirs(root, run_git):
        manifest = read_manifest(spec_dir)
        run_id = str(manifest.get("run_id") or spec_dir.name)
        if run_id in entries:
            continue
        request = str(manifest.get("request", ""))
        spec = spec_dir / "spec.md"
        if prefer_spec_title:
            title = spec_title(spec) or first_line(request)
        else:
            title = first_line(request) or spec_title(spec)
        try:
            shown = spec_dir.relative_to(root)
        except ValueError:
            shown = spec_dir
        entries[run_id] = RunEntry(
            run_id=run_id,
            title=title,
            status=run_status(state_root, run_id),
            started_at=str(manifest.get("started_at", "")),
            path=shown,
            default_location=shown.as_posix() == f"{DOCS_ROOT}/{run_id}",
            request=request,
        )
    return sorted(entries.values(), key=lambda entry: entry.run_id, reverse=True)


def format_run_index(entries: list[RunEntry]) -> str:
    """A grep-friendly fixed-column table; empty string for no runs.

    The PATH column appears only when some run sits outside the default
    location, because there it is the answer to the question being asked
    and everywhere else it only repeats the run id.
    """

    if not entries:
        return ""
    show_path = any(not entry.default_location for entry in entries)
    header = ("RUN_ID", "STATUS", "PATH", "REQUEST")
    rows = [
        (entry.run_id, entry.status, entry.path.as_posix(), entry.title or "-")
        for entry in entries
    ]
    if not show_path:
        header = (header[0], header[1], header[3])
        rows = [(row[0], row[1], row[3]) for row in rows]
    last = len(header) - 1
    widths = [
        max(len(row[column]) for row in (header, *rows)) for column in range(last)
    ]
    lines = [
        "  ".join(
            [f"{cell:<{widths[column]}}" for column, cell in enumerate(row[:last])]
            + [row[last]]
        ).rstrip()
        for row in (header, *rows)
    ]
    return "\n".join(lines) + "\n"


def format_run_details(entries: list[RunEntry]) -> str:
    """One block per run, carrying the request whole; "" for no runs.

    A table cannot hold a request that came from a file: the column view
    keeps one line per run so it stays scannable and greppable, which costs
    every line after the first and everything past the column width. This
    is the view that gives all of it back, so it also shows started_at,
    which the table has no room to carry.

    A run with no readable manifest has no request to print. Its spec
    heading is all that was ever recoverable, so the block says where the
    line came from rather than passing it off as the request.
    """

    if not entries:
        return ""
    blocks = []
    for entry in entries:
        head = f"{entry.run_id}  {entry.status}  {entry.path.as_posix()}"
        if entry.started_at:
            head += f"  {entry.started_at}"
        if entry.request:
            body = entry.request.rstrip().splitlines() or [""]
        elif entry.title:
            body = [entry.title, "(heading from spec.md; no request recorded)"]
        else:
            body = ["(no request recorded)"]
        blocks.append("\n".join([head, *(f"    {line}".rstrip() for line in body)]))
    return "\n\n".join(blocks) + "\n"
