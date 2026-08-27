# 02: Make Implementation Overrides Use Independent Arguments

**What to build:** Make an independent implementation slot use only
`IMPL_ARGS`. Owner arguments and adapter-wide arguments must not leak into I
after any `IMPL_*` override is set. Keep the exact-owner behavior when all
implementation settings are empty, and validate ambiguous dual-spec arguments
only after the real owner is known.

**Blocked by:** 01: Expand Built-In Slots to Accept Slot Arguments.

**Status:** ready-for-agent

- [ ] When every `IMPL_*` setting is empty, implementation uses the exact owner reference, including its slot arguments.
- [ ] When any `IMPL_*` setting is present, implementation uses only `IMPL_ARGS` as its extra arguments.
- [ ] Setting only `IMPL_MODEL` does not carry `AGENT_A_ARGS`, `AGENT_B_ARGS`, or an adapter-wide argument into I.
- [ ] An empty `IMPL_MODEL` inherits the owner model only when implementation and owner use the same command.
- [ ] Changing the implementation command never carries a model name from another adapter.
- [ ] An explicit implementation adapter receives its full adapter-specific validation at startup.
- [ ] A single known owner adapter receives full `IMPL_ARGS` validation at startup.
- [ ] Dual-spec candidates using the same adapter receive full validation at startup.
- [ ] Dual-spec candidates using different adapters receive only shell-quoting validation before owner selection.
- [ ] After owner selection, `IMPL_ARGS` is validated against the actual implementation adapter.
- [ ] Custom implementation arguments remain pass-through after shell-quoting validation.
- [ ] Runtime calls and all recorded metadata show only the effective I-slot arguments.
- [ ] Focused implementation, dual-spec, session, and archive tests pass.
- [ ] The complete test suite remains green before Ticket 3 starts.
