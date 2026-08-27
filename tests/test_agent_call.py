"""Ports tests/helpers.test.sh:651-694 (engine_call stub retry behavior)."""

from datetime import datetime

import pytest

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.agents import AgentResult
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC, RetryEvents, agent_call

RATE_LIMITED = 'api_error_status":429 hit your session limit'
NON_RETRYABLE_QUOTAS = (
    "personal-team-blocked:spending-limit: You have run out of credits or "
    "need a Grok subscription. (status 403)",
    "API error (status 402 Payment Required): "
    "Grok Build usage balance exhausted",
)
NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())


class Stub:
    """fake_agent from the bash suite: reports stub text and fails, or succeeds."""

    def __init__(self, stub_text: str):
        self.stub_text = stub_text
        self.calls = 0

    def __call__(self) -> AgentResult:
        self.calls += 1
        if not self.stub_text:
            return AgentResult(0, "ok")
        return AgentResult(1, self.stub_text, quota_text=self.stub_text)


def run(tmp_path, stub_text, retry_on_limit="1", retry_max="2", notes=None):
    stub = Stub(stub_text)
    slept, archived = [], []
    notes = [] if notes is None else notes
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: archived.append((attempt, rc)),
        log_retry=lambda msg: notes.append(msg),
        notify=lambda msg: notes.append(msg),
        sleep=slept.append,
    )
    settings = Settings.from_env(
        {"RETRY_ON_LIMIT": retry_on_limit, "RETRY_BASE_WAIT": "1", "RETRY_MAX": retry_max},
        run_id="r",
    )
    result = agent_call(stub, settings=settings, events=events, now=lambda: NOW)
    return result, stub, slept, archived


def test_default_retries_to_limit_then_typed_quota_abort(tmp_path):
    result, stub, slept, _ = run(tmp_path, RATE_LIMITED)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 3  # 1 call + 2 retries
    assert len(slept) == 2


def test_retry_off_no_retry_typed_quota_abort(tmp_path):
    result, stub, slept, _ = run(tmp_path, RATE_LIMITED, retry_on_limit="0")
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 1
    assert slept == []


@pytest.mark.parametrize("quota_text", NON_RETRYABLE_QUOTAS)
def test_exhausted_balance_aborts_without_retrying_or_sleeping(tmp_path, quota_text):
    result, stub, slept, archived = run(tmp_path, quota_text)

    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 1
    assert slept == []
    assert archived == [(1, 1)]


def test_ordinary_error_no_retry(tmp_path):
    result, stub, _, _ = run(tmp_path, "ordinary build failure")
    assert result.rc == 1
    assert stub.calls == 1


def test_success_passes_immediately(tmp_path):
    result, stub, _, _ = run(tmp_path, "")
    assert result.rc == 0
    assert stub.calls == 1


def test_reset_beyond_ceiling_aborts_without_sleeping(tmp_path):
    # bash: date -d "+10 days"; here a fixed absolute date 10 days past NOW.
    far = "You've hit your usage limit. try again at Jul 20, 2026 9:00 AM."
    result, stub, slept, _ = run(tmp_path, far)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 1
    assert slept == []


def test_reset_beyond_ceiling_logs_and_notifies_with_local_eta(tmp_path):
    far = "You've hit your usage limit. try again at Jul 20, 2026 9:00 AM."
    notes = []

    run(tmp_path, far, notes=notes)

    assert notes == [
        "!! Quota resets in 240h 0m (about 2026-07-20 09:00), beyond "
        "RETRY_MAX_RESET_WAIT=21600s. Not waiting; rerun after the reset.",
        "adversarial-ai-coding: quota exhausted until 2026-07-20 09:00; "
        "run aborted",
    ]


def test_reset_within_ceiling_waits_and_retries(tmp_path):
    near = "You've hit your usage limit. try again at Jul 10, 2026 10:00 AM."
    result, stub, slept, _ = run(tmp_path, near)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 3
    assert slept == [3600 + 30, 3600 + 30]


def test_retry_logs_and_notifies_with_local_eta(tmp_path):
    near = "You've hit your usage limit. try again at Jul 10, 2026 10:00 AM."
    notes = []

    run(tmp_path, near, retry_max="1", notes=notes)

    assert notes[:2] == [
        "== Rate limit hit; waiting 60 minutes, about until 10:00, before retry 1/1 ==",
        "adversarial-ai-coding: rate limit hit; retry around 10:00 (attempt 1)",
    ]


def test_retry_decision_reads_injected_clock_once(tmp_path):
    stub = Stub("rate limit but no reset info")
    current_times = iter([NOW, NOW + 3600])
    clock_calls = []
    notes = []
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: None,
        log_retry=notes.append,
        notify=notes.append,
        sleep=lambda seconds: None,
    )
    settings = Settings.from_env(
        {"RETRY_BASE_WAIT": "60", "RETRY_MAX": "1"}, run_id="r"
    )

    agent_call(
        stub,
        settings=settings,
        events=events,
        now=lambda: (clock_calls.append(True), next(current_times))[1],
    )

    assert len(clock_calls) == 1
    assert notes[0] == (
        "== Rate limit hit; waiting 1 minutes, about until 09:01, "
        "before retry 1/1 =="
    )


def test_exponential_backoff_when_unparseable(tmp_path):
    result, stub, slept, _ = run(tmp_path, "rate limit but no reset info",
                                 retry_max="3")
    assert stub.calls == 4
    assert slept == [1, 2, 4]  # RETRY_BASE_WAIT=1 doubling per retry


class EpochStub:
    """An agent that reports an exact reset time instead of wording."""

    def __init__(self, reset_epoch: int, quota_text: str):
        self.reset_epoch = reset_epoch
        self.quota_text = quota_text
        self.calls = 0

    def __call__(self) -> AgentResult:
        self.calls += 1
        return AgentResult(
            1,
            "limited",
            quota_text=self.quota_text,
            quota_reset_epoch=self.reset_epoch,
        )


def run_stub(stub, retry_max="1", notes=None):
    notes = [] if notes is None else notes
    slept = []
    events = RetryEvents(
        archive_attempt=lambda attempt, rc: None,
        log_retry=notes.append,
        notify=notes.append,
        sleep=slept.append,
    )
    settings = Settings.from_env(
        {"RETRY_BASE_WAIT": "1", "RETRY_MAX": retry_max}, run_id="r"
    )
    result = agent_call(stub, settings=settings, events=events, now=lambda: NOW)
    return result, slept


def test_reported_epoch_sets_the_wait_instead_of_parsed_wording():
    # The wording says 90 seconds; the reported epoch says an hour. The
    # exact value the agent gave us wins.
    stub = EpochStub(NOW + 3600, RATE_LIMITED + " try again in 90s")

    _, slept = run_stub(stub)

    assert slept == [3600 + 120]


def test_wording_is_used_when_no_epoch_is_reported():
    stub = EpochStub(None, RATE_LIMITED + " try again in 90s")

    _, slept = run_stub(stub)

    assert slept == [90 + 30]


def test_implausible_epoch_falls_back_to_wording():
    # An elapsed or absurd reset time must not skip the retry entirely.
    stub = EpochStub(NOW - 3600, RATE_LIMITED + " try again in 90s")

    _, slept = run_stub(stub)

    assert slept == [90 + 30]


def test_reported_epoch_beyond_the_ceiling_aborts_without_sleeping():
    stub = EpochStub(NOW + 10 * 86400, RATE_LIMITED)

    result, slept = run_stub(stub)

    assert result.rc == QUOTA_ABORT_RC
    assert slept == []
    assert stub.calls == 1


def test_every_attempt_is_archived_with_rc(tmp_path):
    # helpers.test.sh: "engine_call:saves raw output for every retry attempt"
    _, _, _, archived = run(tmp_path, RATE_LIMITED)
    assert archived == [(1, 1), (2, 1), (3, 1)]
