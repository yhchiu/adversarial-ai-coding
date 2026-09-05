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


def render_template(
    template: str, fields: Mapping[str, object] | None = None
) -> str:
    if not fields:
        return template
    return template.format(**fields)

# One allowlist per ecosystem, each naming the commands that
# ecosystem's detected gate runs. `gates.detect_tools` picks the
# sets a workspace actually needs; DEFAULT_TOOLS is their union,
# used when nothing is detected and by callers with no workspace.
VCS_TOOLS = "Bash(git *)"
GO_TOOLS = "Bash(go test *),Bash(go build *),Bash(go vet *)"
NPM_TOOLS = "Bash(npm test)"
CARGO_TOOLS = "Bash(cargo build),Bash(cargo test)"
PYTEST_TOOLS = (
    "Bash(pytest *),Bash(uv run pytest *),Bash(poetry run pytest *),"
    "Bash(python -m pytest *),Bash(python3 -m pytest *)"
)
DEFAULT_TOOLS = ",".join(
    (VCS_TOOLS, GO_TOOLS, NPM_TOOLS, CARGO_TOOLS, PYTEST_TOOLS)
)
# Rejected configuration names. They are not settings fields.
REMOVED_ADAPTER_ARG_VARS = ("CLAUDE_ARGS", "CODEX_ARGS", "AGY_ARGS", "OPENCODE_ARGS")

# The one top-level directory this workflow claims in the target repository
# (docs/adr/0001-single-aac-root-for-run-artifacts.md). ARTIFACT_ROOT is
# visible because DOCS_ROOT holds the spec and plan a human reads at the
# human gates. WORK_DIR is hidden inside it and carries its own .gitignore
# containing "*", so the ignored subtree needs no negation pattern and a
# new committed artifact can never be dropped from git by accident.
ARTIFACT_ROOT = "aac"
DOCS_ROOT = f"{ARTIFACT_ROOT}/docs"
WORK_DIR = f"{ARTIFACT_ROOT}/.run"


class SettingsError(Exception):
    """A configuration problem the user must fix before the run starts."""

    def __init__(self, template: str, **fields: object) -> None:
        self.template = template
        self.fields = fields
        super().__init__(render_template(template, fields))


class WorkflowAbort(Exception):
    """Typed workflow stop; cli maps rc to the process exit code.

    rc=1 mirrors bash's human-intervention exits; rc=QUOTA_ABORT_RC (75)
    mirrors resumable quota aborts. Lives here (the common leaf) so gates,
    review, and workflow can raise it without import cycles.
    """

    def __init__(self, template: str, rc: int = 1, **fields: object) -> None:
        self.template = template
        self.fields = fields
        super().__init__(render_template(template, fields))
        self.rc = rc


def _to_int(name: str, raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise SettingsError(
            "{name} must be an integer, got: {raw}", name=name, raw=raw
        ) from None


def _reject_removed_adapter_args(
    env: Mapping[str, str], snapshot: Mapping[str, str]
) -> None:
    present = [
        name for name in REMOVED_ADAPTER_ARG_VARS if name in env or name in snapshot
    ]
    if present:
        raise SettingsError(
            "Removed adapter-wide argument variable(s): {names}. "
            "Use AGENT_A_ARGS, AGENT_B_ARGS, or IMPL_ARGS instead.",
            names=", ".join(present),
        )


def _to_binary_flag(name: str, raw: str) -> bool:
    if raw not in {"0", "1"}:
        raise SettingsError(
            "{name} must be 0 or 1, got: {raw}", name=name, raw=raw
        )
    return raw == "1"


@dataclass(frozen=True)
class Settings:
    agent_a: str
    agent_b: str
    impl_agent: str
    impl_model: str
    impl_args: str
    model_a: str
    model_b: str
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
    phases_explicit: bool
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

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        run_id: str,
        snapshot: Mapping[str, str] | None = None,
    ) -> "Settings":
        snap = snapshot or {}
        _reject_removed_adapter_args(env, snap)

        def persisted(key: str, default: str) -> str:
            return env.get(key) or snap.get(key) or default

        import_review_raw = (
            env["IMPORT_REVIEW"]
            if "IMPORT_REVIEW" in env
            else snap.get("IMPORT_REVIEW", "1")
        )

        return cls(
            agent_a=persisted("AGENT_A", "claude"),
            agent_b=persisted("AGENT_B", "codex"),
            impl_agent=persisted("IMPL_AGENT", ""),
            impl_model=persisted("IMPL_MODEL", ""),
            impl_args=persisted("IMPL_ARGS", ""),
            model_a=persisted("MODEL_A", ""),
            model_b=persisted("MODEL_B", ""),
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
            import_review=_to_binary_flag("IMPORT_REVIEW", import_review_raw),
            phases=persisted("PHASES", "0") == "1",
            # Explicit PHASES in the launching environment (empty string
            # behaves as unset, matching persisted()). Deliberately never
            # snapshotted: it describes this attempt's command line.
            phases_explicit=bool(env.get("PHASES")),
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
            spec_dir=persisted("SPEC_DIR", f"{DOCS_ROOT}/{run_id}"),
        )
