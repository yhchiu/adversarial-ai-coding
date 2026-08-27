# 03: Remove Adapter-Wide Arguments and Publish the Final Contract

**What to build:** Complete the contract step by removing `CLAUDE_ARGS`,
`CODEX_ARGS`, `AGY_ARGS`, and `OPENCODE_ARGS`. Reject every use clearly,
upgrade persisted settings to schema 2, and update active documentation so the
only supported argument settings are `AGENT_A_ARGS`, `AGENT_B_ARGS`, and
`IMPL_ARGS`.

**Blocked by:** 01: Expand Built-In Slots to Accept Slot Arguments; 02: Make
Implementation Overrides Use Independent Arguments.

**Status:** ready-for-agent

- [ ] The four adapter-wide argument fields are removed from normal settings and persisted state.
- [ ] A launch environment containing any removed variable fails even when its value is empty.
- [ ] An explicitly supplied snapshot mapping containing any removed variable also fails.
- [ ] One error lists every removed variable that was present and points to all three supported replacements.
- [ ] The removed-variable error is available through every existing locale.
- [ ] New settings snapshots use schema 2 and contain only the A, B, and I slot argument fields.
- [ ] Schema 1 snapshots are rejected without migration.
- [ ] Runtime argument resolution no longer reads or combines adapter-wide values.
- [ ] A resolves only `AGENT_A_ARGS`, B resolves only `AGENT_B_ARGS`, and an independent I resolves only `IMPL_ARGS`.
- [ ] The current non-empty resume override rule remains unchanged for all three slot argument settings.
- [ ] Active English and Traditional Chinese setup, session, troubleshooting, and parity documentation describes only the slot-specific interface.
- [ ] Historical plans, reviews, todos, and design records remain unchanged.
- [ ] Documentation tests reject active examples that teach the removed interface.
- [ ] No package version, changelog, or release workflow is added or changed.
- [ ] The complete offline test suite passes without live calls to external agent services.
