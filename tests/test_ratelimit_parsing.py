"""Ports tests/helpers.test.sh:576-649 (rate-limit detection, reset parser).

The bash suite derives clock strings from the real current time; here we
fix `now` instead, which makes the same cases deterministic.
"""

from datetime import datetime

import pytest

from adversarial_ai_coding.ratelimit import (
    human_duration,
    is_rate_limited,
    parse_reset_wait,
)

CLAUDE_429 = (
    '{"type":"result","subtype":"success","is_error":true,'
    '"api_error_status":429,"result":"You\'ve hit your session limit - '
    'resets 10:50am (Asia/Taipei)"}\n'
)
CODEX_429 = (
    'ERROR: {"type":"error","status":429,"error":{"type":"rate_limit_exceeded",'
    '"message":"Rate limit reached for gpt-5.5. Please try again in 90s."}}\n'
)
# Real codex CLI quota message, wrapped across lines exactly as the CLI prints it.
CODEX_QUOTA = (
    "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit\n"
    "https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Jul\n"
    "14th, 2026 7:23 PM.\n"
)


def out_file(tmp_path, text):
    p = tmp_path / "engine-out.txt"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "sample",
    [
        CLAUDE_429,
        "HTTP 429 Too Many Requests\n",
        CODEX_429,
        "You've reached your usage limit.\n",
        CODEX_QUOTA,
    ],
    ids=["claude-429-json", "too-many-requests", "codex-429-json", "reached-usage", "codex-quota-wrapped"],
)
def test_rate_limit_samples_detected(tmp_path, sample):
    assert is_rate_limited(out_file(tmp_path, sample))


def test_ordinary_error_is_not_misclassified(tmp_path):
    p = out_file(tmp_path, "strutil_test.go:47:14: undefined: IsPalindrome\n")
    assert not is_rate_limited(p)


def test_missing_file_is_not_rate_limited(tmp_path):
    assert not is_rate_limited(tmp_path / "nothere.txt")


NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())  # local 09:00


def test_clock_two_hours_ahead_waits_2h_plus_buffer(tmp_path):
    p = out_file(tmp_path, "You have hit your session limit - resets 11:00am (Asia/Taipei)\n")
    assert parse_reset_wait(p, NOW) == 7200 + 120


def test_past_clock_time_rolls_to_tomorrow(tmp_path):
    p = out_file(tmp_path, "resets 8:00am\n")
    assert parse_reset_wait(p, NOW) == 86400 - 3600 + 120  # 82920


def test_pm_clock_parses(tmp_path):
    p = out_file(tmp_path, "resets 12:30pm\n")
    assert parse_reset_wait(p, NOW) == int(3.5 * 3600) + 120


def test_no_reset_info_returns_none_for_backoff(tmp_path):
    assert parse_reset_wait(out_file(tmp_path, "no reset info here\n"), NOW) is None


def test_missing_file_returns_none(tmp_path):
    assert parse_reset_wait(tmp_path / "nothere.txt", NOW) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (CODEX_429, 90 + 30),
        ("Rate limit reached. Please try again in 2 minutes.\n", 120 + 30),
        ("usage cap, try again in 3 hours\n", 3 * 3600 + 30),
        ("try again in 12 hours\n", 12 * 3600 + 30),  # parsed as-is; caller applies policy
        ("please try again in 250 ms\n", 1 + 30),
    ],
    ids=["90s", "2min", "3h", "12h-as-is", "ms"],
)
def test_relative_durations(tmp_path, text, expected):
    assert parse_reset_wait(out_file(tmp_path, text), NOW) == expected


def test_beyond_30_days_hits_sanity_guard(tmp_path):
    assert parse_reset_wait(out_file(tmp_path, "try again in 900 hours\n"), NOW) is None


def test_absolute_date_across_line_break(tmp_path):
    # helpers.test.sh: "reset parser:real codex 'try again at <date>' across a line break"
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = out_file(tmp_path, CODEX_QUOTA)
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_absolute_date_already_elapsed_short_buffer(tmp_path):
    p = out_file(tmp_path, "try again at Jan 2nd, 2020 7:23 PM.\n")
    assert parse_reset_wait(p, NOW) == 30


def test_malformed_clock_minute_falls_through(tmp_path):
    # bash: date -d "5:99am" fails silently and the parser keeps scanning.
    assert parse_reset_wait(out_file(tmp_path, "resets 5:99am\n"), NOW) is None


def test_malformed_clock_still_finds_relative_duration(tmp_path):
    p = out_file(tmp_path, "resets 5:99am, please try again in 90s\n")
    assert parse_reset_wait(p, NOW) == 120


def test_out_of_range_hour_falls_through(tmp_path):
    assert parse_reset_wait(out_file(tmp_path, "resets 19:30pm\n"), NOW) is None


def test_impossible_absolute_date_returns_none(tmp_path):
    p = out_file(tmp_path, "try again at Feb 30th, 2026 7:23 PM.\n")
    assert parse_reset_wait(p, NOW) is None


def test_resets_at_absolute_date_parses(tmp_path):
    # Deliberate divergence from bash, where "resets at/on" are dead branches.
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = out_file(tmp_path, "resets at Jul 14th, 2026 7:23 PM\n")
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_resets_on_absolute_date_parses(tmp_path):
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = out_file(tmp_path, "resets on Jul 14th, 2026 7:23 PM\n")
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_human_duration():
    assert human_duration(11520) == "3h 12m"
    assert human_duration(2700) == "45m"
    assert human_duration(59) == "0m"
