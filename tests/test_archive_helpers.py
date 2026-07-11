"""Ports the pure-helper assertions from tests/helpers.test.sh:301-328."""

from datetime import datetime, timedelta, timezone

import pytest

from adversarial_ai_coding.archive import (
    METRICS_HEADER,
    csv_row,
    generated_at,
    metrics_summary,
    safe_slug,
    write_csv_row,
)


def test_generated_at_format_matches_bash():
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    assert generated_at(fixed) == "2026-01-02T03:04:05+0800"


def test_safe_slug_replaces_separators():
    assert safe_slug("a/b\\c d:e;f|g<h>i\"j'k") == "a-b-c-d-e-f-g-h-i-j-k"
    assert safe_slug("plain-name_1.txt") == "plain-name_1.txt"


def test_csv_row_quotes_every_field_and_escapes_quotes():
    # helpers.test.sh: "metric:CSV escaping preserves model_args with comma and quotes"
    row = csv_row(["a", 'x"y', "1,2"])
    assert row == '"a","x""y","1,2"\n'


def test_metrics_header_matches_bash():
    # helpers.test.sh: "metric:CSV header is correct"
    assert (
        ",".join(METRICS_HEADER)
        == "run_id,stage,role,agent,round,duration_s,cost_usd,model,model_args,generated_at"
    )


def _write_metrics(path, rows):
    path.write_text(",".join(METRICS_HEADER) + "\n", encoding="utf-8")
    for row in rows:
        write_csv_row(path, row)


def test_metrics_summary_sums_despite_quoted_fields(tmp_path):
    # helpers.test.sh: "metrics_summary:seconds/cost/max round" regression.
    csv = tmp_path / "metrics.csv"
    ts = "2026-01-02T03:04:05+0800"
    _write_metrics(
        csv,
        [
            ["run1", "stageX", "worker", "claude", 1, 12, 0.05, "", '-c model="x,y"', ts],
            ["run1", "stageX", "worker", "claude", 3, 8, 0.10, "", "", ts],
        ],
    )
    out = metrics_summary(csv)
    assert "stageX" in out
    assert "AI calls 2" in out
    assert "review rounds 3" in out
    assert "20 seconds" in out
    assert "$0.1500" in out


def test_metrics_summary_empty_cost_counts_as_zero(tmp_path):
    csv = tmp_path / "metrics.csv"
    _write_metrics(csv, [["run1", "s1", "reviewer", "codex", 2, 30, "", "", "", "t"]])
    assert "$0.0000" in metrics_summary(csv)


def test_metrics_summary_missing_file_is_empty(tmp_path):
    assert metrics_summary(tmp_path / "absent.csv") == ""


def test_metrics_summary_ignores_short_rows_and_keeps_first_seen_order(tmp_path):
    csv = tmp_path / "metrics.csv"
    _write_metrics(
        csv,
        [
            ["run1", "stage-b", "worker", "claude", 1, 2, 0, "", "", "t"],
            ["run1", "stage-a", "worker", "claude", 1, 3, 0, "", "", "t"],
        ],
    )
    with csv.open("a", encoding="utf-8") as metrics:
        metrics.write("truncated,row\n")
    lines = metrics_summary(csv).splitlines()
    assert "stage-b" in lines[0]
    assert "stage-a" in lines[1]
    assert len(lines) == 2


def test_csv_row_rejects_bare_string():
    with pytest.raises(TypeError):
        csv_row("abc")
