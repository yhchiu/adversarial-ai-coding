"""Quota/rate-limit detection and reset-time parsing.

Port of adversarial-ai-coding.sh:702-769. Only parsing lives here; the
caller decides whether a wait is worth sitting through. Plan 2 adds the
retry loop.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

RESET_SANITY_MAX = 2_592_000  # 30 days; beyond this is a parsing artefact.

_RATE_LIMIT = re.compile(
    r'"api_error_status": *429'
    r"|(?:hit|reached) your (?:session|usage|weekly|rate) limit"
    r"|rate.?limit"
    r"|too many requests"
    r"|status.?429",
    re.IGNORECASE,
)

_CLOCK = re.compile(r"resets +(\d{1,2}):(\d{2}) ?([ap])m", re.IGNORECASE)
_RELATIVE = re.compile(
    r"try again in (\d+)(?:\.\d+)? ?"
    r"(ms|milliseconds?|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(
    r"(?:try again at|resets at|resets on) +([A-Za-z]{3,9}) +(\d{1,2})"
    r"(?:st|nd|rd|th)?,? +(\d{4}),? +(\d{1,2}):(\d{2}) *([ap])\.?m",
    re.IGNORECASE,
)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_rate_limited(path: Path) -> bool:
    text = _read(path)
    return text is not None and _RATE_LIMIT.search(text) is not None


def _hour24(hour12: int, ampm: str) -> int:
    h = hour12 % 12
    return h + 12 if ampm.lower() == "p" else h


def parse_reset_wait(path: Path, now: int | None = None) -> int | None:
    text = _read(path)
    if text is None:
        return None
    if now is None:
        now = int(datetime.now().timestamp())
    # Agents wrap their output, so a timestamp can straddle a newline.
    norm = re.sub(r"[ \t\r\n]+", " ", text)

    # Format 1, Claude: "resets 10:50am" -> next occurrence, plus 120s buffer.
    m = _CLOCK.search(norm)
    if m:
        base = datetime.fromtimestamp(now)
        target = base.replace(
            hour=_hour24(int(m.group(1)), m.group(3)),
            minute=int(m.group(2)),
            second=0,
            microsecond=0,
        )
        if int(target.timestamp()) <= now:
            target += timedelta(days=1)
        wait = int(target.timestamp()) - now + 120
        return wait if wait <= RESET_SANITY_MAX else None

    # Format 2, OpenAI/Codex: "try again in 20s / 2 minutes / 3 hours" + 30s buffer.
    m = _RELATIVE.search(norm)
    if m:
        num = int(m.group(1))
        unit = m.group(2).lower()
        if unit == "ms" or unit.startswith("millisecond"):
            wait = 1
        elif unit == "s" or unit.startswith(("sec", "second")):
            wait = num
        elif unit == "m" or unit.startswith(("min", "minute")):
            wait = num * 60
        else:  # h, hr(s), hour(s)
            wait = num * 3600
        wait += 30
        return wait if wait <= RESET_SANITY_MAX else None

    # Format 3, Codex quota: "try again at Jul 14th, 2026 7:23 PM" + 30s buffer.
    m = _ABSOLUTE.search(norm)
    if m:
        month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
        try:
            parsed_month = datetime.strptime(month[:3].title(), "%b").month
        except ValueError:
            return None
        target = datetime(
            year, parsed_month, day,
            _hour24(int(m.group(4)), m.group(6)), int(m.group(5)),
        )
        target_epoch = int(target.timestamp())
        if target_epoch <= now:
            return 30  # Already elapsed; retry after a short buffer.
        wait = target_epoch - now + 30
        return wait if wait <= RESET_SANITY_MAX else None

    return None


def human_duration(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 60}m"
