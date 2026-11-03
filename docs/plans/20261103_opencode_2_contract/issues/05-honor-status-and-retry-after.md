# 05: Treat an OpenCode 2 429 as a rate limit and wait for retryAfterMs

**What to build:** A provider 429 reported as `error.status` still counts as a rate limit when the message does not say "rate limit". When that failure includes `retryAfterMs`, that duration is the wait. OpenCode 1 quota detection, spending-limit handling, and cost are unchanged.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A 429 in `error.status` counts as a rate limit even when the text does not say "rate limit".
- [ ] The process exit code is preserved, so a non-zero failure is what lets the existing quota loop retry a classified 429.
- [ ] A usable `retryAfterMs` on a classified quota failure is the wait.
- [ ] A `retryAfterMs` longer than `RETRY_MAX_RESET_WAIT` stops the run as resumable and does not sleep.
- [ ] A missing or unusable `retryAfterMs` leaves the current wording parser and backoff in place.
- [ ] Grok spending-limit text still stops the run as resumable and does not retry.
- [ ] OpenCode 1 still treats `error.data.statusCode` 429 as a rate limit.
- [ ] Reported cost remains the sum of `step_finish` costs. A reported zero and a missing report stay distinct.
- [ ] Tests drive the worker and reviewer calls with a fake `opencode` and the existing quota retry. They do not need a live binary, and they do not add a separate parser test seam.
