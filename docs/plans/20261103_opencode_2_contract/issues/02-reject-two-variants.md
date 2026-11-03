# 02: Reject two variant values on one slot

**What to build:** A slot that names two variant values, or a `--variant` flag with no value, fails startup on both OpenCode 1 and OpenCode 2. The error names the slot variable and every value. One value is still forwarded on OpenCode 1, including when no model is set.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] `--variant low` and `--variant=low` are one value.
- [ ] Two `--variant` values on one slot fail startup on both product lines, and the message lists both values and the slot variable.
- [ ] A `--variant` flag with no value fails startup on both product lines.
- [ ] On OpenCode 1, a single `--variant` is still forwarded, including when that slot has no `MODEL_*`.
- [ ] `IMPL_ARGS` follows the same rule. Under dual spec, startup fails only when every candidate owner would reject the arguments. A violation that depends on the unchosen candidate still waits for that choice.
- [ ] The failure uses the existing settings-error locale path and happens before a paid stage.
- [ ] Tests cover both product lines and the dual-spec timing through startup validation. They do not need a live binary.
