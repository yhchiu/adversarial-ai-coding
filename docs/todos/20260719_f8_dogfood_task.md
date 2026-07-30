# Task: Opt-in Codex danger-full-access sandbox setting

Add a first-class, opt-in setting that runs Codex agent slots with the
`danger-full-access` sandbox mode instead of the hardcoded
`workspace-write`.

Background: Codex CLI 0.144.x on Windows has three sandbox configurations
(elevated+trusted, elevated+untrusted, unelevated) and all three are
broken for headless workflow use: the elevated broker writes output files
with a deny-all ACL the parent process cannot read back, the untrusted
mode is a read-only sandbox that cannot write verdict.json, and the
unelevated token cannot even read the workspace. Bypassing the Codex
sandbox is currently the only reliable way to use Codex slots on Windows.
The workflow's existing unreadable-output recovery stays as the safety
net and must not be weakened.

Requirements:

- New first-class environment setting on Settings, following the same
  persisted-snapshot pattern as other first-class settings, so a resumed
  run keeps the value. Suggested name: `CODEX_DANGER_FULL_ACCESS`. The
  final name must contain the word DANGER. Default: off. `1` enables it.
- When off, behavior is byte-identical to today: fresh Codex exec calls
  use `--sandbox workspace-write`, and the exec-resume path overrides
  config with `-c sandbox_mode="workspace-write"`.
- When on, every Codex slot (A, B, and I when it resolves to codex) uses
  the danger-full-access sandbox mode in BOTH paths: the fresh exec call
  and the exec-resume config override.
- The reserved-flags contract is unchanged: `CODEX_ARGS` and
  Codex-targeted `IMPL_ARGS` still must not contain `--sandbox`, `-s`,
  `--dangerously-bypass-approvals-and-sandbox`, `--yolo`, `--ephemeral`,
  or a `sandbox_mode` override via `-c` / `--config`, regardless of the
  new setting. The setting is the only supported way to change the
  sandbox mode.
- When the setting is on, the workflow prints a clear startup warning
  that the Codex sandbox is bypassed and that a worktree or container is
  strongly recommended (similar in spirit to the existing agy
  `--dangerously-skip-permissions` safety note).
- Documentation: the README.md agent-behavior and safety sections and
  README.zh-TW.md document the setting, its default, and the warning.
  AGENTS templates are not part of this task.
- Tests cover: default-off argument construction (fresh and resume),
  enabled argument construction (both paths), snapshot persistence
  across resume, and that reserved-flags validation still rejects manual
  sandbox flags whether the setting is on or off.

Out of scope: reporting the Codex bugs upstream, any agy or claude
sandbox changes, container support.
