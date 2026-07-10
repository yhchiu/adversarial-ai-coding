"""Environment-variable settings.

Port of the bash Settings section: adversarial-ai-coding.sh:49-64
(alias_env_or_default) and 285-330 (defaults). Resolution order for
persisted keys: environment, then the resume snapshot, then the default —
matching bash "${VAR:-${RESUMED_VAR:-default}}".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_TOOLS = "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"


class SettingsError(Exception):
    """A configuration problem the user must fix before the run starts."""


def alias_env_or_default(
    env: Mapping[str, str], preferred: str, legacy: str, default: str
) -> str:
    preferred_value = env.get(preferred, "")
    legacy_value = env.get(legacy, "")
    if preferred_value and legacy_value and preferred_value != legacy_value:
        raise SettingsError(
            f"Conflicting {preferred} and {legacy}; set only one or use the same value."
        )
    return preferred_value or legacy_value or default


def _to_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer, got: {raw}") from None


@dataclass(frozen=True)
class Settings:
    engine_a: str
    engine_b: str
    model_a: str
    model_b: str
    claude_args: str
    codex_args: str
    agy_args: str
    engine_a_args: str
    engine_b_args: str
    max_rounds: int
    auto_branch: bool
    use_worktree: bool
    human_gate: bool
    dual_spec: bool
    open_pr: bool
    notify_cmd: str
    retry_on_limit: bool
    retry_max: int
    retry_base_wait: int
    retry_max_wait: int
    retry_max_reset_wait: int
    tools: str
    spec_dir: str
    runs_dir: str

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        run_id: str,
        snapshot: Mapping[str, str] | None = None,
    ) -> "Settings":
        snap = snapshot or {}

        def persisted(key: str, default: str) -> str:
            return env.get(key) or snap.get(key) or default

        return cls(
            engine_a=alias_env_or_default(
                env, "AGENT_A", "ENGINE_A", snap.get("ENGINE_A") or "claude"
            ),
            engine_b=alias_env_or_default(
                env, "AGENT_B", "ENGINE_B", snap.get("ENGINE_B") or "codex"
            ),
            model_a=persisted("MODEL_A", ""),
            model_b=persisted("MODEL_B", ""),
            claude_args=persisted("CLAUDE_ARGS", ""),
            codex_args=persisted("CODEX_ARGS", ""),
            agy_args=persisted("AGY_ARGS", ""),
            engine_a_args=alias_env_or_default(
                env, "AGENT_A_ARGS", "ENGINE_A_ARGS", snap.get("ENGINE_A_ARGS") or ""
            ),
            engine_b_args=alias_env_or_default(
                env, "AGENT_B_ARGS", "ENGINE_B_ARGS", snap.get("ENGINE_B_ARGS") or ""
            ),
            max_rounds=_to_int("MAX_ROUNDS", persisted("MAX_ROUNDS", "3")),
            auto_branch=persisted("AUTO_BRANCH", "1") == "1",
            use_worktree=persisted("USE_WORKTREE", "0") == "1",
            human_gate=persisted("HUMAN_GATE", "1") == "1",
            dual_spec=persisted("DUAL_SPEC", "0") == "1",
            open_pr=persisted("OPEN_PR", "0") == "1",
            # Deliberately never from the snapshot (bash line 307): provide per attempt.
            notify_cmd=env.get("NOTIFY_CMD", ""),
            retry_on_limit=env.get("RETRY_ON_LIMIT", "1") == "1",
            retry_max=_to_int("RETRY_MAX", env.get("RETRY_MAX", "6")),
            retry_base_wait=_to_int("RETRY_BASE_WAIT", env.get("RETRY_BASE_WAIT", "300")),
            retry_max_wait=_to_int("RETRY_MAX_WAIT", env.get("RETRY_MAX_WAIT", "3600")),
            retry_max_reset_wait=_to_int(
                "RETRY_MAX_RESET_WAIT", env.get("RETRY_MAX_RESET_WAIT", "21600")
            ),
            tools=persisted("TOOLS", DEFAULT_TOOLS),
            spec_dir=persisted("SPEC_DIR", f"specs/{run_id}"),
            runs_dir=env.get("RUNS_DIR") or ".workflow/runs",
        )
