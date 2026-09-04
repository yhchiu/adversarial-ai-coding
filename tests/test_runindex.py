"""The committed run manifest and the listing built from it.

The point of run.json is that a timestamped directory name stops being the
only thing a reader has months later. Two properties carry that: it lands
in the committed half of the artifact root, and nothing an agent does can
remove or rewrite it. Both are tested here; the git-reachability half lives
in test_artifact_layout.py, next to the rest of the ignore-rule invariant.

Discovery gets its own section below. SPEC_DIR can put a run's documents
anywhere, so "scan aac/docs/" is a convention, not a guarantee, and each of
the three sources covers a gap the other two leave.
"""

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runindex import (
    MANIFEST_NAME,
    STATUS_COMPLETED,
    STATUS_UNFINISHED,
    STATUS_UNKNOWN,
    RunEntry,
    discover_spec_dirs,
    first_line,
    format_run_index,
    load_run_entries,
    read_manifest,
    run_status,
    spec_title,
    write_run_manifest,
)

SPEC_WITH_H1 = """Intro line

## Not the title

# Resume a stopped run

body
"""

MULTI_LINE_REQUEST = """
# Heading

the rest of the file
"""


def settings(**env):
    return Settings.from_env(env, run_id="20260904-101500")


def no_git(args, cwd):
    """Stand in for a directory git knows nothing about."""
    return 128, ""


def write(tmp_path, run_id="20260904-101500", request="Add a feature", **kw):
    spec_dir = tmp_path / "aac" / "docs" / run_id
    return write_run_manifest(
        spec_dir,
        run_id=run_id,
        request=request,
        branch=kw.pop("branch", "aac/20260904-101500"),
        settings=kw.pop("settings", settings()),
        **kw,
    )


def test_manifest_records_the_run_identity(tmp_path):
    now = datetime(2026, 9, 4, 10, 15, tzinfo=timezone(timedelta(hours=8)))
    path = write(
        tmp_path,
        request="Add slot-specific agent arguments",
        settings=settings(AGENT_A="codex", AGENT_B="agy", PHASES="1"),
        now=now,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "schema": 1,
        "run_id": "20260904-101500",
        "request": "Add slot-specific agent arguments",
        "started_at": "2026-09-04T10:15:00+0800",
        "branch": "aac/20260904-101500",
        "agent_a": "codex",
        "agent_b": "agy",
        "dual_spec": False,
        "phases": True,
        "imported_spec": False,
    }


def test_manifest_creates_the_spec_directory(tmp_path):
    path = write(tmp_path)
    assert path == tmp_path / "aac" / "docs" / "20260904-101500" / MANIFEST_NAME
    assert path.is_file()


def test_manifest_is_write_once_so_a_resume_keeps_the_start_time(tmp_path):
    first = write(tmp_path, request="Original request")
    original = first.read_text(encoding="utf-8")

    # A resumed attempt reuses the run id and calls this again.
    assert write(tmp_path, request="Original request") is None
    assert first.read_text(encoding="utf-8") == original


def test_manifest_keeps_non_ascii_requests_readable(tmp_path):
    path = write(tmp_path, request="支援繁體中文的請求")
    assert "支援繁體中文的請求" in path.read_text(encoding="utf-8")


def test_manifest_records_an_imported_spec(tmp_path):
    path = write(tmp_path, settings=settings(IMPORT_SPEC="/tmp/spec.md"))
    assert json.loads(path.read_text(encoding="utf-8"))["imported_spec"] is True


def test_read_manifest_refuses_unknown_and_broken_payloads(tmp_path):
    spec_dir = tmp_path / "run"
    spec_dir.mkdir()
    manifest = spec_dir / MANIFEST_NAME

    assert read_manifest(spec_dir) == {}
    manifest.write_text("{not json", encoding="utf-8")
    assert read_manifest(spec_dir) == {}
    manifest.write_text('["a list"]', encoding="utf-8")
    assert read_manifest(spec_dir) == {}
    manifest.write_text('{"schema": 99, "run_id": "r"}', encoding="utf-8")
    assert read_manifest(spec_dir) == {}
    manifest.write_text('{"schema": 1, "run_id": "r"}', encoding="utf-8")
    assert read_manifest(spec_dir)["run_id"] == "r"


def test_first_line_skips_blanks_collapses_space_and_cuts():
    assert first_line("\n\n  Add   a   feature  \nmore\n") == "Add a feature"
    assert first_line("") == ""
    assert first_line("   \n\t\n") == ""
    assert first_line("abcdef", width=3) == "abc"


def test_spec_title_reads_the_first_h1_only(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(SPEC_WITH_H1 + "# Second\n", encoding="utf-8")
    assert spec_title(spec) == "Resume a stopped run"


def test_spec_title_is_blank_when_there_is_no_h1_or_no_file(tmp_path):
    assert spec_title(tmp_path / "nope.md") == ""
    spec = tmp_path / "spec.md"
    spec.write_text("## Only a subheading\n", encoding="utf-8")
    assert spec_title(spec) == ""


def test_run_status_is_derived_from_the_state_directory(tmp_path):
    state_root = tmp_path / "state"
    (state_root / "done").mkdir(parents=True)
    (state_root / "done" / "completed").write_text("", encoding="utf-8")
    (state_root / "stopped").mkdir(parents=True)

    assert run_status(state_root, "done") == STATUS_COMPLETED
    assert run_status(state_root, "stopped") == STATUS_UNFINISHED
    # A fresh clone has no aac/.run/ at all: unknown, not "unfinished".
    assert run_status(state_root, "cloned") == STATUS_UNKNOWN


# --- the listing built from the manifests ------------------------------


def record(root, run_id, request=None, spec=None, spec_dir=None, completed=None):
    """One run as it exists on disk: documents somewhere, state or not.

    spec_dir defaults to the standard location; pass one to stand in for a
    run launched with SPEC_DIR. completed=None writes no state at all,
    which is what a fresh clone looks like.
    """
    resolved = root / (spec_dir or f"aac/docs/{run_id}")
    resolved.mkdir(parents=True, exist_ok=True)
    if request is not None:
        write_run_manifest(
            resolved,
            run_id=run_id,
            request=request,
            branch="aac/" + run_id,
            settings=settings(),
        )
    if spec is not None:
        (resolved / "spec.md").write_text(spec, encoding="utf-8")
    if completed is not None:
        state_dir = root / "aac" / ".run" / "state" / run_id
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "settings.json").write_text(
            json.dumps({"schema": 2, "spec_dir": str(resolved.relative_to(root))}),
            encoding="utf-8",
        )
        if completed:
            (state_dir / "completed").write_text("", encoding="utf-8")
    return resolved


def entries(root, run_git=no_git):
    return load_run_entries(root, run_git)


def test_entries_are_newest_run_first(tmp_path):
    record(tmp_path, "20260901-000000", "Older run")
    record(tmp_path, "20260904-101500", "Newer run")
    record(tmp_path, "20260902-120000", "Middle run")

    listed = entries(tmp_path)
    assert [entry.run_id for entry in listed] == [
        "20260904-101500",
        "20260902-120000",
        "20260901-000000",
    ]
    assert listed[0].title == "Newer run"


def test_title_falls_back_to_the_spec_h1_for_runs_without_a_manifest(tmp_path):
    """Runs made before the manifest existed still have to list."""
    record(tmp_path, "20260815-071227", spec=SPEC_WITH_H1)
    entry = entries(tmp_path)[0]
    assert entry.run_id == "20260815-071227"
    assert entry.title == "Resume a stopped run"


def test_a_run_with_neither_manifest_nor_spec_still_lists(tmp_path):
    """The two run directories this repository already has are both empty."""
    record(tmp_path, "20260815-090255")
    entry = entries(tmp_path)[0]
    assert entry.run_id == "20260815-090255"
    assert entry.title == ""
    assert entry.status == STATUS_UNKNOWN


def test_the_manifest_wins_over_the_spec_heading(tmp_path):
    record(tmp_path, "20260904-101500", "The request", "# A rewritten heading\n")
    assert entries(tmp_path)[0].title == "The request"


def test_a_multi_line_request_lists_as_its_first_line(tmp_path):
    record(tmp_path, "20260904-101500", MULTI_LINE_REQUEST)
    assert entries(tmp_path)[0].title == "# Heading"


def test_stray_files_beside_the_run_directories_are_skipped(tmp_path):
    record(tmp_path, "20260904-101500", "A run")
    (tmp_path / "aac" / "docs" / "README.md").write_text("no", encoding="utf-8")
    assert len(entries(tmp_path)) == 1


def test_an_empty_repository_lists_nothing(tmp_path):
    assert entries(tmp_path) == []


def test_entries_carry_the_derived_status(tmp_path):
    record(tmp_path, "20260904-101500", "Done", completed=True)
    record(tmp_path, "20260903-101500", "Stopped", completed=False)
    record(tmp_path, "20260902-101500", "Cloned")

    statuses = {entry.run_id: entry.status for entry in entries(tmp_path)}
    assert statuses == {
        "20260904-101500": STATUS_COMPLETED,
        "20260903-101500": STATUS_UNFINISHED,
        "20260902-101500": STATUS_UNKNOWN,
    }


# --- discovery: SPEC_DIR moves the documents ---------------------------


def test_a_custom_spec_dir_is_found_through_the_settings_snapshot(tmp_path):
    """The only source that knows about a run that never committed."""
    record(
        tmp_path,
        "20260904-101500",
        "Ported the archive module",
        spec_dir="specs/archive-port",
        completed=False,
    )
    listed = entries(tmp_path)
    assert len(listed) == 1
    assert listed[0].run_id == "20260904-101500"
    assert listed[0].title == "Ported the archive module"
    assert listed[0].path.as_posix() == "specs/archive-port"
    assert listed[0].status == STATUS_UNFINISHED
    assert not listed[0].default_location


def test_an_absolute_spec_dir_is_followed(tmp_path):
    outside = tmp_path / "elsewhere" / "docs"
    record(tmp_path, "20260904-101500", "Outside the repo", spec_dir=None)
    outside.mkdir(parents=True)
    write_run_manifest(
        outside,
        run_id="20260903-101500",
        request="An absolute SPEC_DIR",
        branch="main",
        settings=settings(),
    )
    state_dir = tmp_path / "aac" / ".run" / "state" / "20260903-101500"
    state_dir.mkdir(parents=True)
    state_dir.joinpath("settings.json").write_text(
        json.dumps({"schema": 2, "spec_dir": str(outside)}), encoding="utf-8"
    )

    titles = {entry.run_id: entry.title for entry in entries(tmp_path)}
    assert titles["20260903-101500"] == "An absolute SPEC_DIR"


def test_a_damaged_snapshot_costs_one_location_not_the_listing(tmp_path):
    record(tmp_path, "20260904-101500", "A readable run", completed=False)
    broken = tmp_path / "aac" / ".run" / "state" / "20260903-101500"
    broken.mkdir(parents=True)
    (broken / "settings.json").write_text("{truncated", encoding="utf-8")

    assert [entry.run_id for entry in entries(tmp_path)] == ["20260904-101500"]


def test_a_run_found_by_two_sources_is_one_row(tmp_path):
    """The common case: the default location, with state, tracked by git."""
    spec_dir = record(tmp_path, "20260904-101500", "A run", completed=True)
    manifest = (spec_dir / MANIFEST_NAME).relative_to(tmp_path).as_posix()

    def git(args, cwd):
        return 0, manifest + "\0"

    listed = entries(tmp_path, git)
    assert [entry.run_id for entry in listed] == ["20260904-101500"]
    assert listed[0].default_location


def test_git_finds_a_custom_spec_dir_after_a_clone(new_repo):
    """The state that recorded SPEC_DIR is never committed; git is what is left."""
    spec_dir = record(
        new_repo, "20260904-101500", "Committed elsewhere", spec_dir="specs/feature"
    )
    subprocess.run(["git", "-C", str(new_repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(new_repo), "commit", "-qm", "spec"], check=True
    )
    assert spec_dir.is_dir()

    # A clone has the branch and nothing else; the real git runner answers.
    listed = load_run_entries(new_repo)
    assert [entry.run_id for entry in listed] == ["20260904-101500"]
    assert listed[0].title == "Committed elsewhere"
    assert listed[0].path.as_posix() == "specs/feature"
    assert listed[0].status == STATUS_UNKNOWN


def test_discovery_ignores_files_that_merely_end_in_run_json(tmp_path):
    """The git pathspec is a suffix match, so myrun.json reaches the filter."""
    (tmp_path / "tool").mkdir()
    (tmp_path / "tool" / "myrun.json").write_text("{}", encoding="utf-8")

    def git(args, cwd):
        return 0, "tool/myrun.json\0"

    assert discover_spec_dirs(tmp_path, git) == []


def test_discovery_survives_a_directory_git_knows_nothing_about(tmp_path):
    record(tmp_path, "20260904-101500", "A run")
    assert len(discover_spec_dirs(tmp_path, no_git)) == 1


# --- rendering ---------------------------------------------------------


def entry(run_id="r", title="t", status=STATUS_COMPLETED, path=None, default=True):
    return RunEntry(
        run_id=run_id,
        title=title,
        status=status,
        started_at="",
        path=Path(path or f"aac/docs/{run_id}"),
        default_location=default,
    )


def test_format_aligns_columns_against_the_header():
    text = format_run_index(
        [
            entry("20260904-101500", "Add slot-specific agent arguments"),
            entry("short", "Another", STATUS_UNFINISHED),
        ]
    )
    lines = text.splitlines()
    assert lines[0].split() == ["RUN_ID", "STATUS", "REQUEST"]
    # Every row's title starts in the header's REQUEST column.
    starts = {line.index("A") for line in lines[1:]}
    assert len(starts) == 1
    assert lines[0].index("REQUEST") == starts.pop()
    assert text.endswith("\n")


def test_format_hides_the_path_column_while_every_run_is_where_it_belongs():
    text = format_run_index([entry("20260904-101500", "A run")])
    assert "PATH" not in text
    assert "aac/docs" not in text


def test_format_shows_the_path_column_as_soon_as_one_run_moved():
    text = format_run_index(
        [
            entry("20260904-101500", "Moved", path="specs/feature", default=False),
            entry("20260903-101500", "Not moved"),
        ]
    )
    lines = text.splitlines()
    assert lines[0].split() == ["RUN_ID", "STATUS", "PATH", "REQUEST"]
    # The column earns its width from every row, moved or not.
    assert "specs/feature" in lines[1]
    assert "aac/docs/20260903-101500" in lines[2]
    request_column = lines[0].index("REQUEST")
    assert lines[1].index("Moved") == request_column
    assert lines[2].index("Not moved") == request_column


def test_format_marks_a_missing_title_and_never_leaves_trailing_space():
    text = format_run_index([entry("20260904-101500", "")])
    assert text.splitlines()[1].endswith("  -")
    assert not any(line.endswith(" ") for line in text.splitlines())


def test_format_of_nothing_is_empty_not_a_bare_header():
    assert format_run_index([]) == ""
