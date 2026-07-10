"""Run archive helpers — pure text/CSV parts.

Port of adversarial-ai-coding.sh:377-398 (generated_at, safe_slug) and
424-452 (csv_row, write_csv_row, metrics_summary). The artifact and
run-directory I/O functions join this module in plan 3.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence

METRICS_HEADER = [
    "run_id", "stage", "role", "engine", "round",
    "duration_s", "cost_usd", "model", "model_args", "generated_at",
]

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
