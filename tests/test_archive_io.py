"""Ports helpers.test.sh:296-350 (metric, art_path, write_meta, archive_task)."""

import csv
import json
from datetime import datetime, timedelta, timezone

from adversarial_ai_coding.archive import RunArchive, establish_run_archive
from adversarial_ai_coding.agents import agent_ref
from adversarial_ai_coding.config import Settings

FIXED = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))


def make_archive(tmp_path, env=None) -> RunArchive:
    settings = Settings.from_env(env or {}, run_id="test")
    return establish_run_archive(tmp_path / "runs", "test", settings)


def test_establish_run_archive_collision_suffix(tmp_path):
    a1 = make_archive(tmp_path)
    a2 = make_archive(tmp_path)
    a3 = make_archive(tmp_path)
    assert a1.run_dir.name == "test"
    assert a2.run_dir.name == "test-2"
    assert a3.run_dir.name == "test-3"
    assert a1.log_path == a1.run_dir / "logs" / "001-run.log"
    assert a1.metrics_path == a1.run_dir / "metrics.csv"


def test_art_path_increments_and_survives_reload(tmp_path):
    # helpers.test.sh: "art_path:increments sequence"
    a = make_archive(tmp_path)
    p1 = a.art_path("first.txt")
    p2 = a.art_path("second.txt")
    assert p1.name == "001-first.txt"
    assert p2.name == "002-second.txt"
    # A new RunArchive over the same dir (resume) continues the sequence.
    b = RunArchive(
        run_dir=a.run_dir,
        run_id="test",
        settings=a.settings,
        log_path=a.log_path,
        metrics_path=a.metrics_path,
    )
    assert b.art_path("third.txt").name == "003-third.txt"


def test_write_meta_matches_bash_fields(tmp_path):
    # helpers.test.sh: "write_meta/archive_snapshot:write required metadata"
    a = make_archive(tmp_path, {"AGENT_A": "claude", "AGENT_B": "codex"})
    src = tmp_path / "src.txt"
    src.write_text("data\n", encoding="utf-8")
    dst = a.archive_snapshot(
        src,
        "snap.txt",
        role="worker",
        agent=agent_ref("A", a.settings),
        stage="stage",
        round=3,
        now=FIXED,
    )
    meta = json.loads(
        (dst.parent / (dst.name + ".meta.json")).read_text(encoding="utf-8")
    )
    assert meta["generated_at"] == "2026-01-02T03:04:05+0800"
    assert meta["generator_role"] == "worker"
    assert meta["agent"] == "claude"
    assert meta["stage"] == "stage"
    assert meta["round"] == "3"
    assert meta["run_id"] == "test"
    assert list(meta.keys()) == [
        "generated_at",
        "generator_role",
        "agent",
        "model",
        "model_args",
        "stage",
        "round",
        "run_id",
        "artifact",
    ]


def test_archive_snapshot_missing_source_is_noop(tmp_path):
    a = make_archive(tmp_path)
    assert a.archive_snapshot(tmp_path / "absent.txt", "x.txt") is None


def test_archive_text_normalizes_newline_and_writes_meta(tmp_path):
    a = make_archive(tmp_path)
    dst = a.archive_text(
        "note.txt",
        "archived text\n\n",
        role="reviewer",
        agent=agent_ref("B", a.settings),
        stage="review",
        round=2,
        now=FIXED,
    )
    assert dst.name == "001-note.txt"
    assert dst.read_text(encoding="utf-8") == "archived text\n"
    meta = json.loads(
        dst.with_name(dst.name + ".meta.json").read_text(encoding="utf-8")
    )
    assert meta["generator_role"] == "reviewer"
    assert meta["agent"] == "codex"
    assert meta["stage"] == "review"
    assert meta["round"] == "2"


def test_archive_task_file_kind(tmp_path):
    # helpers.test.sh: "archive_task:saves file task source and resolved text"
    a = make_archive(tmp_path)
    a.archive_task("task.md", "file", "C:/abs/task.md", "file task\n")
    source = (a.run_dir / "001-task-source.md").read_text(encoding="utf-8")
    assert "- kind: file" in source
    assert "- path: C:/abs/task.md" in source
    assert "file task" in source
    assert (a.run_dir / "002-task.txt").read_text(encoding="utf-8") == "file task\n"


def test_archive_task_literal_kind(tmp_path):
    a = make_archive(tmp_path)
    a.archive_task("literal task", "literal", "", "literal task")
    source = (a.run_dir / "001-task-source.md").read_text(encoding="utf-8")
    assert "- kind: literal" in source
    assert "- path:" not in source


def test_archive_agent_attempt_names_and_placeholder(tmp_path):
    # helpers.test.sh: "engine_call:saves raw output for every retry attempt"
    a = make_archive(tmp_path)
    out = tmp_path / "agent-out.txt"
    out.write_text("raw agent output\n", encoding="utf-8")
    cli_raw = tmp_path / "agent-cli.raw"
    cli_raw.write_text('{"type":"thread.started"}\n', encoding="utf-8")
    a.archive_agent_attempt(
        "worker",
        agent_ref("A", a.settings),
        "worker-stage-r1",
        1,
        1,
        out,
        cli_raw,
        stage="stage",
        round=1,
    )
    saved = a.run_dir / "001-worker-stage-r1-attempt-1-rc1.raw"
    assert saved.read_text(encoding="utf-8") == "raw agent output\n"
    cli_saved = a.run_dir / "002-worker-stage-r1-attempt-1-rc1.cli.raw"
    assert cli_saved.read_text(encoding="utf-8") == '{"type":"thread.started"}\n'
    meta = json.loads(
        (a.run_dir / "001-worker-stage-r1-attempt-1-rc1.raw.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert meta["generator_role"] == "worker" and meta["agent"] == "claude"
    a.archive_agent_attempt(
        "worker",
        agent_ref("A", a.settings),
        "worker-stage-r1",
        2,
        1,
        tmp_path / "gone.txt",
        tmp_path / "gone-cli.txt",
        stage="stage",
        round=1,
    )
    placeholder = a.run_dir / "003-worker-stage-r1-attempt-2-rc1.raw"
    assert "agent output was not written" in placeholder.read_text(encoding="utf-8")


def test_metric_header_and_rows(tmp_path):
    # helpers.test.sh: "metric:header plus two rows" / "CSV header is correct"
    a = make_archive(
        tmp_path,
        {
            "AGENT_A": "claude",
            "AGENT_B": "codex",
            "CODEX_ARGS": '-c model="x,y" --flag "quoted value"',
        },
    )
    a.metric("worker", agent_ref("A", a.settings), 1, 12, "0.05", stage="stage1")
    a.metric("reviewer", agent_ref("B", a.settings), 2, 30, "", stage="stage1")
    lines = a.metrics_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[0] == (
        "run_id,stage,role,agent,round,duration_s,cost_usd,"
        "model,model_args,generated_at"
    )
    row = next(csv.reader([lines[2]]))
    assert row[3] == "codex"
    assert row[8] == '-c model="x,y" --flag "quoted value"'
    assert len(row) == 10


def test_log_section_banner(tmp_path):
    a = make_archive(tmp_path)
    echoed = []
    a.log_section(
        "AI call",
        "worker",
        agent_ref("A", a.settings),
        "stage",
        2,
        echo=echoed.append,
        now=FIXED,
    )
    text = a.log_path.read_text(encoding="utf-8")
    assert "-" * 80 in text
    assert "[2026-01-02T03:04:05+0800] AI call | role=worker agent=claude" in text
    assert "stage=stage round=2" in text
    assert echoed


def test_run_and_log_metadata(tmp_path):
    a = make_archive(tmp_path, {"AGENT_A": "claude", "AGENT_B": "codex"})
    a.write_run_metadata(spec_dir="specs/test", wf=".workflow", now=FIXED)
    payload = json.loads(
        (a.run_dir / "001-run-metadata.json").read_text(encoding="utf-8")
    )
    assert payload["run_id"] == "test"
    assert payload["agent_a"] == "claude"
    a.write_log_metadata(now=FIXED)
    log_meta = json.loads(
        (a.log_path.parent / (a.log_path.name + ".meta.json")).read_text(
            encoding="utf-8"
        )
    )
    assert log_meta["generator_role"] == "workflow"


def test_same_agent_slots_keep_slot_specific_models_in_all_metadata(tmp_path):
    a = make_archive(
        tmp_path,
        {
            "AGENT_A": "codex",
            "AGENT_B": "codex",
            "MODEL_A": "gpt-a",
            "MODEL_B": "gpt-b",
        },
    )
    ref_b = agent_ref("B", a.settings)
    artifact = a.archive_text(
        "b-output.txt", "done", role="worker", agent=ref_b, stage="code"
    )
    a.log_section("AI call", "worker", ref_b, "code", 1, echo=lambda _: None)
    a.metric("worker", ref_b, 1, 2, "", stage="code")
    a.write_run_metadata(spec_dir="specs/test", wf=".workflow")

    meta = json.loads(
        artifact.with_name(artifact.name + ".meta.json").read_text(encoding="utf-8")
    )
    assert meta["agent"] == "codex" and meta["model"] == "gpt-b"
    assert "agent=codex model=gpt-b" in a.log_path.read_text(encoding="utf-8")
    metrics = list(csv.reader(a.metrics_path.read_text(encoding="utf-8").splitlines()))
    assert metrics[1][3] == "codex" and metrics[1][7] == "gpt-b"
    run_metadata = json.loads(
        next(a.run_dir.glob("*-run-metadata.json")).read_text(encoding="utf-8")
    )
    assert run_metadata["model_a"] == "gpt-a"
    assert run_metadata["model_b"] == "gpt-b"
