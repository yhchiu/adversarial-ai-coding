# 03: Deliver the OpenCode 2 prompt unchanged and fold one variant

**What to build:** On OpenCode 2, the worker, the reviewer, and the implementation slot receive the prompt AAC assembled, with spaces, newlines, and quotes unchanged. One `--variant` in the published settings becomes part of the model reference. The archive still shows the settings the user wrote. Both READMEs keep the `--variant` example and record the `external_directory` default.

**Blocked by:** 01 Select the OpenCode product line at startup, 02 Reject two variant values on one slot

**Status:** done

- [x] An OpenCode 2 worker, reviewer, and implementation call write the prompt to stdin and pass no positional message. The stdin bytes are the prompt exactly.
- [x] A bare word in OpenCode 2 slot args fails startup.
- [x] A `--` separator in OpenCode 2 slot args fails startup.
- [x] The values of `--file`, `--title`, `--agent`, `--model`, `-m`, `--format`, `--session`, `-s`, `--server`, and `--worktree` / `-w` are not bare words.
- [x] `--worktree` or `-w` alone stays a flag. The following word is its name only when that word does not start with `-`.
- [x] A boolean flag does not consume the next word. An unrecognized flag does not consume the next word either.
- [x] OpenCode 1 slot args are unchanged aside from the two-value rule in ticket 02.
- [x] On OpenCode 2, one `--variant` plus a `MODEL_*` that has no `#variant` is passed as `provider/model#variant` on `-m`. The `--variant` flag is not forwarded.
- [x] A `MODEL_*` that already contains `#variant`, with no `--variant`, is passed through on `-m`.
- [x] A `MODEL_*` that already contains a variant, plus a `--variant`, fails startup and names both values.
- [x] On OpenCode 2, `--variant` with no `MODEL_*` fails startup. On OpenCode 1 it is still forwarded.
- [x] `IMPL_ARGS` follows the same OpenCode 2 rules, with the same dual-spec timing as ticket 02.
- [x] The archive records the user's `MODEL_*` and the user's raw slot args. No archive field is added or renamed.
- [x] Both READMEs still show the `--variant` example as valid for both product lines.
- [x] Both READMEs say OpenCode 2 allows `external_directory` by default, and show the permission entry `{ "action": "external_directory", "resource": "*", "effect": "ask" }`. AAC does not write that entry into the user's configuration.
- [x] Tests drive the worker and reviewer calls, and startup validation, with a fake `opencode`. The bilingual documentation tests lock the new sentences.
