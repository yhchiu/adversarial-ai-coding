# 01: Expand Built-In Slots to Accept Slot Arguments

**What to build:** Let built-in agents in A and B use `AGENT_A_ARGS` and
`AGENT_B_ARGS`. During this expand step, the old adapter-wide argument
variables continue to work and are applied before the new slot arguments.
Users can therefore start using slot-specific settings without losing current
behavior. Runtime calls and recorded metadata must show the same effective
arguments.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] A built-in agent in slot A receives `AGENT_A_ARGS` in fresh and resumed calls.
- [ ] A built-in agent in slot B receives `AGENT_B_ARGS` in fresh and resumed calls.
- [ ] A and B can use the same built-in CLI with different slot arguments, and neither call receives the other slot's value.
- [ ] The old adapter-wide arguments still work during this expand step and appear before slot arguments.
- [ ] Built-in slot arguments are checked with the rules for their exact adapter.
- [ ] Model flags and workflow-owned flags are rejected with an error that names `AGENT_A_ARGS` or `AGENT_B_ARGS`.
- [ ] A flag reserved by another adapter is not rejected.
- [ ] Custom A/B agents still receive pass-through slot arguments after shell-quoting validation.
- [ ] Logs, artifact metadata, run metadata, and metrics report the same effective arguments used by runtime calls.
- [ ] Focused configuration, adapter, session, and archive tests pass.
- [ ] The complete test suite remains green before Ticket 2 starts.
