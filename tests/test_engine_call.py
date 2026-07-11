"""Ports tests/helpers.test.sh:651-694 (engine_call stub retry behavior)."""

from datetime import datetime
from pathlib import Path

from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.engines import EngineResult
from adversarial_ai_coding.ratelimit import QUOTA_ABORT_RC, RetryEvents, engine_call

RATE_LIMITED = 'api_error_status":429 hit your session limit'
NOW = int(datetime(2026, 7, 10, 9, 0, 0).timestamp())


class Stub:
    """fake_engine from the bash suite: writes stub text and fails, or succeeds."""

    def __init__(self, engine_out: Path, stub_text: str):
        self.engine_out = engine_out
        self.stub_text = stub_text
        self.calls = 0

    def __call__(self) -> EngineResult:
        self.calls += 1
        if not self.stub_text:
            return EngineResult(0, "ok")
        self.engine_out.write_text(self.stub_text + "\n", encoding="utf-8")
        return EngineResult(1, self.stub_text)


def run(tmp_path, stub_text, retry_on_limit="1", retry_max="2"):
    engine_out = tmp_path / "engine-out.txt"
    stub = Stub(engine_out, stub_text)
    slept, archived, notes = [], [], []
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
    result = engine_call(stub, engine_out=engine_out, settings=settings,
                         events=events, now=lambda: NOW)
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


def test_reset_within_ceiling_waits_and_retries(tmp_path):
    near = "You've hit your usage limit. try again at Jul 10, 2026 10:00 AM."
    result, stub, slept, _ = run(tmp_path, near)
    assert result.rc == QUOTA_ABORT_RC
    assert stub.calls == 3
    assert slept == [3600 + 30, 3600 + 30]


def test_exponential_backoff_when_unparseable(tmp_path):
    result, stub, slept, _ = run(tmp_path, "rate limit but no reset info",
                                 retry_max="3")
    assert stub.calls == 4
    assert slept == [1, 2, 4]  # RETRY_BASE_WAIT=1 doubling per retry


def test_every_attempt_is_archived_with_rc(tmp_path):
    # helpers.test.sh: "engine_call:saves raw output for every retry attempt"
    _, _, _, archived = run(tmp_path, RATE_LIMITED)
    assert archived == [(1, 1), (2, 1), (3, 1)]
