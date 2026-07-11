"""Quota/rate-limit detection and reset-time parsing.

Port of adversarial-ai-coding.sh:702-769. Only parsing lives here; the
caller decides whether a wait is worth sitting through. Plan 2 adds the
retry loop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .config import Settings
    from .engines import EngineResult

# EX_TEMPFAIL: an agent call gave up on quota/rate limit; the run is resumable (sh:72).
QUOTA_ABORT_RC = 75

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
# Deliberate divergence: bash strips a 3-word prefix before date parsing, so
# its "resets at"/"resets on" alternates never actually parse (dead branches).
# The Python port resurrects them; tests pin this.
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
    # bash uses `date -d "$t" 2>/dev/null || true`: unparseable clocks fail
    # silently and the parser keeps scanning the other formats.
    m = _CLOCK.search(norm)
    if m:
        hour12 = int(m.group(1))
        if not 1 <= hour12 <= 12:
            m = None  # GNU date rejects hour 0 or >12 with an am/pm marker
    if m:
        try:
            base = datetime.fromtimestamp(now)
            target = base.replace(
                hour=_hour24(hour12, m.group(3)),
                minute=int(m.group(2)),
                second=0,
                microsecond=0,
            )
            if int(target.timestamp()) <= now:
                # bash adds exactly 86400s; timedelta(days=1) on a naive local datetime can
                # differ by 1h across a DST edge. Wall-clock rollover is the intended meaning.
                target += timedelta(days=1)
            wait = int(target.timestamp()) - now + 120
            return wait if wait <= RESET_SANITY_MAX else None
        except ValueError:
            pass  # e.g. "5:99am": fall through to Formats 2 and 3.

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
        hour12 = int(m.group(4))
        if not 1 <= hour12 <= 12:
            m = None  # GNU date rejects hour 0 or >12 with an am/pm marker
    if m:
        month, day, year = m.group(1), int(m.group(2)), int(m.group(3))
        try:
            parsed_month = datetime.strptime(month[:3].title(), "%b").month
            target = datetime(
                year, parsed_month, day,
                _hour24(hour12, m.group(6)), int(m.group(5)),
            )
        except ValueError:
            # e.g. "Feb 30th": bash date -d fails silently; nothing left to
            # try, so fall through to the final None.
            return None
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


@dataclass
class RetryEvents:
    archive_attempt: Callable[[int, int], None]
    log_retry: Callable[[str], None]
    notify: Callable[[str], None]
    sleep: Callable[[float], None]


def engine_call(
    attempt: "Callable[[], EngineResult]",
    *,
    engine_out: Path,
    settings: "Settings",
    events: RetryEvents,
    now: Callable[[], int] | None = None,
) -> "EngineResult":
    """Port of engine_call (sh:1131-1169): retry only on rate limits.

    Every quota give-up returns rc=QUOTA_ABORT_RC so callers abort the run
    as resumable instead of treating it like a quality failure.
    """
    from .engines import EngineResult  # local import to avoid a cycle

    n = 0
    attempt_no = 1
    while True:
        result = attempt()
        events.archive_attempt(attempt_no, result.rc)
        if result.rc == 0:
            return result
        if not is_rate_limited(engine_out):
            return result
        if not settings.retry_on_limit:
            return EngineResult(QUOTA_ABORT_RC, result.text)
        if n >= settings.retry_max:
            events.log_retry(
                f"!! Rate limit did not clear after {settings.retry_max} retries; giving up."
            )
            return EngineResult(QUOTA_ABORT_RC, result.text)
        current_epoch = now() if now else int(datetime.now().timestamp())
        wait = parse_reset_wait(engine_out, current_epoch)
        if wait is not None and wait > settings.retry_max_reset_wait:
            # The message told us exactly when the quota returns and it is far
            # away. Backing off would burn hours of sleep and still fail (sh:1149-1155).
            eta = datetime.fromtimestamp(current_epoch + wait).strftime(
                "%Y-%m-%d %H:%M"
            )
            events.log_retry(
                f"!! Quota resets in {human_duration(wait)} (about {eta}), beyond "
                f"RETRY_MAX_RESET_WAIT={settings.retry_max_reset_wait}s. "
                "Not waiting; rerun after the reset."
            )
            events.notify(
                f"adversarial-ai-coding: quota exhausted until {eta}; run aborted"
            )
            return EngineResult(QUOTA_ABORT_RC, result.text)
        n += 1
        if wait is None:
            wait = min(settings.retry_base_wait * (1 << (n - 1)), settings.retry_max_wait)
        eta = datetime.fromtimestamp(current_epoch + wait).strftime("%H:%M")
        events.log_retry(
            f"== Rate limit hit; waiting {wait // 60} minutes, about until {eta}, "
            "before retry "
            f"{n}/{settings.retry_max} =="
        )
        events.notify(
            f"adversarial-ai-coding: rate limit hit; retry around {eta} (attempt {n})"
        )
        events.sleep(wait)
        attempt_no += 1
