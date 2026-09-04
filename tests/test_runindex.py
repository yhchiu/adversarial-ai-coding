"""The committed run manifest and the listing built from it.

The point of run.json is that a timestamped directory name stops being the
only thing a reader has months later. Two properties carry that: it lands
in the committed half of the artifact root, and nothing an agent does can
remove or rewrite it. Both are tested here; the git-reachability half lives
in test_artifact_layout.py, next to the rest of the ignore-rule invariant.
"""

import json
from datetime import datetime, timedelta, timezone

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.runindex import (
    MANIFEST_NAME,
    STATUS_COMPLETED,
    STATUS_UNFINISHED,
    STATUS_UNKNOWN,
    first_line,
    read_manifest,
    run_status,
    spec_title,
    write_run_manifest,
)


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


def test_first_line_skips_blanks_collapses_space_and_cuts(tmp_path):
    assert first_line("\n\n  Add   a   feature  \nmore\n") == "Add a feature"
    assert first_line("") == ""
    assert first_line("   \n\t\n") == ""
    assert first_line("abcdef", width=3) == "abc"


def test_spec_title_reads_the_first_h1_only(tmp_path):
    spec = tmp_path / "spec.md"
    spec.write_text(
        "Intro line\n## Not the title\n# The Title\n# Second\n", encoding="utf-8"
    )
    assert spec_title(spec) == "The Title"


def test_spec_title_is_blank_when_there_is_no_h1_or_no_file(tmp_path):
    missing = tmp_path / "nope.md"
    assert spec_title(missing) == ""
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
