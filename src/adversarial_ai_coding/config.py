"""Environment-variable settings.

Port of the bash Settings section: adversarial-ai-coding.sh:285-330
(defaults). Resolution order for persisted keys: environment, then the
resume snapshot, then the default — matching bash
"${VAR:-${RESUMED_VAR:-default}}".

Deliberate divergence: bash accepts ENGINE_A/ENGINE_B (and *_ARGS) as
legacy aliases of AGENT_A/AGENT_B (sh:49-64, alias_env_or_default). The
Python port drops the aliases — AGENT_* are the only names; ENGINE_*
variables are ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

DEFAULT_TOOLS = "Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)"


class SettingsError(Exception):
    """A configuration problem the user must fix before the run starts."""


class WorkflowAbort(Exception):
    """Typed workflow stop; cli maps rc to the process exit code.

    rc=1 mirrors bash's human-intervention exits; rc=QUOTA_ABORT_RC (75)
    mirrors resumable quota aborts. Lives here (the common leaf) so gates,
    review, and workflow can raise it without import cycles.
    """

    def __init__(self, message: str, rc: int = 1):
        super().__init__(message)
        self.rc = rc


def _to_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(f"{name} must be an integer, got: {raw}") from None


@dataclass(frozen=True)
class Settings:
    agent_a: str
    agent_b: str
    impl_agent: str
    impl_model: str
    impl_args: str
    model_a: str
    model_b: str
    claude_args: str
    codex_args: str
    agy_args: str
    agent_a_args: str
    agent_b_args: str
    max_rounds: int
    auto_branch: bool
    use_worktree: bool
    human_gate: bool
    human_gate_plan: bool
    dual_spec: bool
    import_spec: str
    import_plan: str
    import_review: bool
    phases: bool
    phase_review: bool
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
            agent_a=persisted("AGENT_A", "claude"),
            agent_b=persisted("AGENT_B", "codex"),
            impl_agent=persisted("IMPL_AGENT", ""),
            impl_model=persisted("IMPL_MODEL", ""),
            impl_args=persisted("IMPL_ARGS", ""),
            model_a=persisted("MODEL_A", ""),
            model_b=persisted("MODEL_B", ""),
            claude_args=persisted("CLAUDE_ARGS", ""),
            codex_args=persisted("CODEX_ARGS", ""),
            agy_args=persisted("AGY_ARGS", ""),
            agent_a_args=persisted("AGENT_A_ARGS", ""),
            agent_b_args=persisted("AGENT_B_ARGS", ""),
            max_rounds=_to_int("MAX_ROUNDS", persisted("MAX_ROUNDS", "3")),
            auto_branch=persisted("AUTO_BRANCH", "1") == "1",
            use_worktree=persisted("USE_WORKTREE", "0") == "1",
            human_gate=persisted("HUMAN_GATE", "1") == "1",
            human_gate_plan=persisted("HUMAN_GATE_PLAN", "0") == "1",
            dual_spec=persisted("DUAL_SPEC", "0") == "1",
            import_spec=persisted("IMPORT_SPEC", ""),
            import_plan=persisted("IMPORT_PLAN", ""),
            import_review=persisted("IMPORT_REVIEW", "1") == "1",
            phases=persisted("PHASES", "0") == "1",
            phase_review=persisted("PHASE_REVIEW", "0") == "1",
            open_pr=persisted("OPEN_PR", "0") == "1",
            # Deliberately never from the snapshot (bash line 307): provide per attempt.
            notify_cmd=env.get("NOTIFY_CMD") or "",
            retry_on_limit=(env.get("RETRY_ON_LIMIT") or "1") == "1",
            retry_max=_to_int("RETRY_MAX", env.get("RETRY_MAX") or "6"),
            retry_base_wait=_to_int("RETRY_BASE_WAIT", env.get("RETRY_BASE_WAIT") or "300"),
            retry_max_wait=_to_int("RETRY_MAX_WAIT", env.get("RETRY_MAX_WAIT") or "3600"),
            retry_max_reset_wait=_to_int(
                "RETRY_MAX_RESET_WAIT", env.get("RETRY_MAX_RESET_WAIT") or "21600"
            ),
            tools=persisted("TOOLS", DEFAULT_TOOLS),
            spec_dir=persisted("SPEC_DIR", f"specs/{run_id}"),
            runs_dir=env.get("RUNS_DIR") or ".workflow/runs",
        )
