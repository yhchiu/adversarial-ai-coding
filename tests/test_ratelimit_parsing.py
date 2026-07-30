"""Ports tests/helpers.test.sh:576-649 (rate-limit detection, reset parser).

The bash suite derives clock strings from the real current time; here we
fix `now` instead, which makes the same cases deterministic.
"""

import json
from datetime import datetime

import pytest

from adversarial_ai_coding.ratelimit import (
    human_duration,
    is_rate_limited,
    parse_reset_wait,
    wait_until_epoch,
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
# Real codex CLI (v0.144.x) quota message: clock-only reset time, no date.
CODEX_QUOTA_CLOCK = (
    "ERROR: You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
    "visit https://chatgpt.com/codex/settings/usage to purchase more credits or "
    "try again at 12:50 AM.\n"
)

NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())  # local 09:00


@pytest.mark.parametrize(
    "sample",
    [
        CLAUDE_429,
        "HTTP 429 Too Many Requests\n",
        CODEX_429,
        "You've reached your usage limit.\n",
        CODEX_QUOTA,
        CODEX_QUOTA_CLOCK,
    ],
    ids=[
        "claude-429-json",
        "too-many-requests",
        "codex-429-json",
        "reached-usage",
        "codex-quota-wrapped",
        "codex-quota-clock",
    ],
)
def test_rate_limit_samples_detected(sample):
    assert is_rate_limited(sample)


def test_ordinary_error_is_not_misclassified():
    p = "strutil_test.go:47:14: undefined: IsPalindrome\n"
    assert not is_rate_limited(p)


def test_empty_quota_channel_is_not_rate_limited():
    # An adapter that reported nothing must never look rate limited.
    assert not is_rate_limited("")


# --- claude reports the status as a field, so wording is not consulted ---

def _envelope(**fields):
    return json.dumps({"type": "result", "subtype": "success", **fields})


def test_envelope_status_429_is_rate_limited_without_any_wording():
    assert is_rate_limited(_envelope(api_error_status=429, result="all done"))


def test_envelope_with_another_status_is_not_rate_limited():
    # The field decides on its own: quota wording in the agent's own reply
    # must not make an ordinary 500 look like an exhausted quota.
    text = _envelope(
        api_error_status=500,
        result="I read the rate limit parser in ratelimit.py",
    )

    assert not is_rate_limited(text)


@pytest.mark.parametrize(
    "fields",
    [
        {"api_error_status": None},
        {"api_error_status": True},
        {"api_error_status": "429"},
        {},
    ],
    ids=["null", "bool", "string", "absent"],
)
def test_envelope_without_a_usable_status_falls_back_to_wording(fields):
    assert is_rate_limited(_envelope(result="You've hit your session limit", **fields))
    assert not is_rate_limited(_envelope(result="ordinary failure", **fields))


def test_non_envelope_text_still_uses_wording():
    # Codex and agy have no such field; their channel is plain text.
    assert is_rate_limited("You've hit your usage limit.")
    assert not is_rate_limited("strutil_test.go:47:14: undefined: IsPalindrome")


# --- a reported reset epoch replaces parsing a wall-clock string ---

def test_reported_epoch_becomes_a_wait_with_the_clock_buffer():
    assert wait_until_epoch(NOW + 3600, NOW) == 3600 + 120


def test_reported_epoch_already_elapsed_is_rejected():
    assert wait_until_epoch(NOW - 3600, NOW) is None


def test_reported_epoch_beyond_the_sanity_guard_is_rejected():
    assert wait_until_epoch(NOW + 40 * 86400, NOW) is None


def test_clock_two_hours_ahead_waits_2h_plus_buffer():
    p = "You have hit your session limit - resets 11:00am (Asia/Taipei)\n"
    assert parse_reset_wait(p, NOW) == 7200 + 120


def test_past_clock_time_rolls_to_tomorrow():
    p = "resets 8:00am\n"
    assert parse_reset_wait(p, NOW) == 86400 - 3600 + 120  # 82920


def test_pm_clock_parses():
    p = "resets 12:30pm\n"
    assert parse_reset_wait(p, NOW) == int(3.5 * 3600) + 120


def test_no_reset_info_returns_none_for_backoff():
    assert parse_reset_wait("no reset info here\n", NOW) is None


def test_empty_quota_channel_returns_none():
    assert parse_reset_wait("", NOW) is None


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
def test_relative_durations(text, expected):
    assert parse_reset_wait(text, NOW) == expected


def test_beyond_30_days_hits_sanity_guard():
    assert parse_reset_wait("try again in 900 hours\n", NOW) is None


def test_absolute_date_across_line_break():
    # helpers.test.sh: "reset parser:real codex 'try again at <date>' across a line break"
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = CODEX_QUOTA
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_absolute_date_already_elapsed_short_buffer():
    p = "try again at Jan 2nd, 2020 7:23 PM.\n"
    assert parse_reset_wait(p, NOW) == 30


def test_malformed_clock_minute_falls_through():
    # bash: date -d "5:99am" fails silently and the parser keeps scanning.
    assert parse_reset_wait("resets 5:99am\n", NOW) is None


def test_malformed_clock_still_finds_relative_duration():
    p = "resets 5:99am, please try again in 90s\n"
    assert parse_reset_wait(p, NOW) == 120


def test_out_of_range_hour_falls_through():
    assert parse_reset_wait("resets 19:30pm\n", NOW) is None


def test_hour_zero_clock_falls_through():
    # GNU date rejects "0:30am"; bash fell through to no match.
    assert parse_reset_wait("resets 0:30am\n", NOW) is None


def test_hour_zero_absolute_date_returns_none():
    p = "try again at Jul 14th, 2026 0:23 PM.\n"
    assert parse_reset_wait(p, NOW) is None


def test_impossible_absolute_date_returns_none():
    p = "try again at Feb 30th, 2026 7:23 PM.\n"
    assert parse_reset_wait(p, NOW) is None


def test_resets_at_absolute_date_parses():
    # Deliberate divergence from bash, where "resets at/on" are dead branches.
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = "resets at Jul 14th, 2026 7:23 PM\n"
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_resets_on_absolute_date_parses():
    now_fixed = int(datetime(2026, 7, 8, 7, 0, 0).timestamp())
    target = int(datetime(2026, 7, 14, 19, 23, 0).timestamp())
    p = "resets on Jul 14th, 2026 7:23 PM\n"
    assert parse_reset_wait(p, now_fixed) == target - now_fixed + 30


def test_clock_only_try_again_at_waits_until_next_occurrence():
    # Real incident (2026-07-12 E2E run): hit at 00:37, codex said
    # "try again at 12:50 AM" with no date; expect a precise 13-minute wait.
    now_fixed = int(datetime(2026, 7, 12, 0, 37, 0).timestamp())
    p = CODEX_QUOTA_CLOCK
    assert parse_reset_wait(p, now_fixed) == 13 * 60 + 30


def test_clock_only_past_time_rolls_to_tomorrow():
    p = "try again at 8:00 AM.\n"
    assert parse_reset_wait(p, NOW) == 86400 - 3600 + 30  # 82830


def test_clock_only_pm_parses():
    p = "try again at 12:30 PM.\n"
    assert parse_reset_wait(p, NOW) == int(3.5 * 3600) + 30


def test_clock_only_across_line_break():
    now_fixed = int(datetime(2026, 7, 12, 0, 37, 0).timestamp())
    p = "purchase more credits or try again at\n12:50 AM.\n"
    assert parse_reset_wait(p, now_fixed) == 13 * 60 + 30


def test_clock_only_hour_zero_falls_through():
    # Mirrors the "resets 0:30am" rule: hour 0 with an am/pm marker is invalid.
    assert parse_reset_wait("try again at 0:30 AM.\n", NOW) is None


def test_clock_only_malformed_minute_returns_none():
    assert parse_reset_wait("try again at 5:99 AM.\n", NOW) is None


def test_human_duration():
    assert human_duration(11520) == "3h 12m"
    assert human_duration(2700) == "45m"
    assert human_duration(59) == "0m"
