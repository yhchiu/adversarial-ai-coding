# 04: Resume an OpenCode 2 conversation only when it still exists

**What to build:** An OpenCode 2 worker Resume sends the captured id only when that conversation is known to still exist. A confirmed miss warns the user and becomes a Fresh call. Any other check result fails that call. A reviewer stays Fresh. The check looks at the same server the call will use.

**Blocked by:** 01 Select the OpenCode product line at startup

**Status:** ready-for-agent

- [ ] The first worker call of a stage stays Fresh and does not look up a session.
- [ ] Every reviewer call stays Fresh and is not checked.
- [ ] An OpenCode 2 worker call that already holds a captured id checks for that conversation before it passes `--session`.
- [ ] The check is a separate invocation of the same resolved command. It is not `run`, and it does not receive the prompt. It receives the same `--server` and `--standalone` as the call. Those two flags stay legal in slot args.
- [ ] Combined output of `Session not found:` for that exact id is absence. AAC warns, on the same channel as the existing missing-id warning, that this call is not the captured conversation, drops the id, and makes a Fresh call.
- [ ] An id reported by that Fresh call becomes the captured id, so a later worker call in the same stage can Resume it.
- [ ] A Fresh call that reports no id leaves the process with no captured id and the existing missing-id warning. The lost id is not sent again.
- [ ] Any other check result, including a check that does not run, fails that call and shows the check's explanation. The call does not become Fresh and is not a quota retry.
- [ ] A conversation that exists and has no messages is still Resume.
- [ ] Entering the next stage still clears the captured id. A new process started with `RESUME_RUN` does not restore an OpenCode conversation.
- [ ] OpenCode 1 worker Resume still passes `--session` with no existence check.
- [ ] Both session-lifecycle docs describe the Fresh call, the check, and the reviewer's permanent Freshness.
- [ ] Tests drive the worker and reviewer calls with a fake `opencode` and do not need a live binary.
