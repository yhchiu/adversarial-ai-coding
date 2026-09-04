"""The committed run manifest and the listing built from it.

The point of run.json is that a timestamped directory name stops being the
only thing a reader has months later. Two properties carry that: it lands
in the committed half of the artifact root, and nothing an agent does can
remove or rewrite it. Both are tested here; the git-reachability half lives
in test_artifact_layout.py, next to the rest of the ignore-rule invariant.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runindex import (
    MANIFEST_NAME,
    STATUS_COMPLETED,
    STATUS_UNFINISHED,
    STATUS_UNKNOWN,
    RunEntry,
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


def docs_with(tmp_path, runs):
    """A docs root holding one directory per (run_id, request, spec) triple."""
    docs_root = tmp_path / "aac" / "docs"
    for run_id, request, spec in runs:
        spec_dir = docs_root / run_id
        spec_dir.mkdir(parents=True)
        if request is not None:
            write_run_manifest(
                spec_dir,
                run_id=run_id,
                request=request,
                branch="aac/" + run_id,
                settings=settings(),
            )
        if spec is not None:
            (spec_dir / "spec.md").write_text(spec, encoding="utf-8")
    return docs_root


def test_entries_are_newest_run_first(tmp_path):
    docs_root = docs_with(
        tmp_path,
        [
            ("20260901-000000", "Older run", None),
            ("20260904-101500", "Newer run", None),
            ("20260902-120000", "Middle run", None),
        ],
    )
    entries = load_run_entries(docs_root, tmp_path / "state")
    assert [entry.run_id for entry in entries] == [
        "20260904-101500",
        "20260902-120000",
        "20260901-000000",
    ]
    assert entries[0].title == "Newer run"


def test_title_falls_back_to_the_spec_h1_for_runs_without_a_manifest(tmp_path):
    """Runs made before the manifest existed still have to list."""
    docs_root = docs_with(tmp_path, [("20260815-071227", None, SPEC_WITH_H1)])
    entry = load_run_entries(docs_root, tmp_path / "state")[0]
    assert entry.run_id == "20260815-071227"
    assert entry.title == "Resume a stopped run"


def test_a_run_with_neither_manifest_nor_spec_still_lists(tmp_path):
    """The two run directories this repository already has are both empty."""
    docs_root = docs_with(tmp_path, [("20260815-090255", None, None)])
    entry = load_run_entries(docs_root, tmp_path / "state")[0]
    assert entry.run_id == "20260815-090255"
    assert entry.title == ""
    assert entry.status == STATUS_UNKNOWN


def test_the_manifest_wins_over_the_spec_heading(tmp_path):
    docs_root = docs_with(
        tmp_path, [("20260904-101500", "The request", "# A rewritten heading\n")]
    )
    assert load_run_entries(docs_root, tmp_path / "state")[0].title == "The request"


def test_a_multi_line_request_lists_as_its_first_line(tmp_path):
    docs_root = docs_with(tmp_path, [("20260904-101500", MULTI_LINE_REQUEST, None)])
    assert load_run_entries(docs_root, tmp_path / "state")[0].title == "# Heading"


def test_stray_files_beside_the_run_directories_are_skipped(tmp_path):
    docs_root = docs_with(tmp_path, [("20260904-101500", "A run", None)])
    (docs_root / "README.md").write_text("not a run\n", encoding="utf-8")
    assert len(load_run_entries(docs_root, tmp_path / "state")) == 1


def test_a_missing_docs_root_lists_nothing(tmp_path):
    assert load_run_entries(tmp_path / "absent", tmp_path / "state") == []


def test_entries_carry_the_derived_status(tmp_path):
    docs_root = docs_with(
        tmp_path,
        [("20260904-101500", "Done", None), ("20260903-101500", "Stopped", None)],
    )
    state_root = tmp_path / "state"
    (state_root / "20260904-101500").mkdir(parents=True)
    (state_root / "20260904-101500" / "completed").write_text("", encoding="utf-8")
    (state_root / "20260903-101500").mkdir(parents=True)

    statuses = {e.run_id: e.status for e in load_run_entries(docs_root, state_root)}
    assert statuses == {
        "20260904-101500": STATUS_COMPLETED,
        "20260903-101500": STATUS_UNFINISHED,
    }


def entry(run_id="r", title="t", status=STATUS_COMPLETED):
    return RunEntry(
        run_id=run_id, title=title, status=status, started_at="", path=Path(run_id)
    )


def test_format_aligns_columns_against_the_header():
    text = format_run_index(
        [
            entry("20260904-101500", "Add slot-specific agent arguments"),
            entry("short", "Another", STATUS_UNFINISHED),
        ]
    )
    lines = text.splitlines()
    assert lines[0].startswith("RUN_ID")
    # Every row's title starts in the header's REQUEST column.
    starts = {line.index("A") for line in lines[1:]}
    assert len(starts) == 1
    assert lines[0].index("REQUEST") == starts.pop()
    assert text.endswith("\n")


def test_format_marks_a_missing_title_and_never_leaves_trailing_space():
    text = format_run_index([entry("20260904-101500", "")])
    assert text.splitlines()[1].endswith("  -")
    assert not any(line.endswith(" ") for line in text.splitlines())


def test_format_of_nothing_is_empty_not_a_bare_header():
    assert format_run_index([]) == ""
