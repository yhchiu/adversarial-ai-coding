"""Engine adapters and selection helpers.

Port of adversarial-ai-coding.sh:341-359 (validate_engines), 400-422
(is_builtin_engine, resolve_model_args), 689-696 (engine_model),
1090-1096 (generic_engine_args). Task 2 adds the subprocess adapters.
"""

from __future__ import annotations

import shutil
from typing import Callable

from .config import Settings, SettingsError

BUILTIN_ENGINES = ("claude", "codex", "agy")


def is_builtin_engine(name: str) -> bool:
    return name in BUILTIN_ENGINES


def engine_model(name: str, settings: Settings) -> str:
    # Custom engines ignore MODEL_A/MODEL_B; they get args via ENGINE_*_ARGS.
    if not is_builtin_engine(name):
        return ""
    if name == settings.engine_a and settings.model_a:
        return settings.model_a
    if name == settings.engine_b and settings.model_b:
        return settings.model_b
    return ""


def resolve_model_args(name: str, settings: Settings) -> str:
    if name == "claude":
        return settings.claude_args
    if name == "codex":
        return settings.codex_args
    if name == "agy":
        return settings.agy_args
    return generic_engine_args(name, settings)


def generic_engine_args(name: str, settings: Settings) -> str:
    if name == settings.engine_a:
        return settings.engine_a_args
    if name == settings.engine_b:
        return settings.engine_b_args
    return ""


def validate_engines(
    settings: Settings, which: Callable[[str], str | None] = shutil.which
) -> None:
    for name in (settings.engine_a, settings.engine_b):
        if which(name) is None:
            raise SettingsError(f"Missing required command:{name}")
    # codex and agy resume the most recent session. Custom engines may have
    # the same limitation, so v1 requires distinct command names (bash :349-358).
    if settings.engine_a == settings.engine_b and settings.engine_a != "claude":
        if is_builtin_engine(settings.engine_a):
            raise SettingsError(
                f"A and B cannot both use {settings.engine_a} because session "
                "resume would interfere. Use different engines."
            )
        raise SettingsError(
            f"A and B cannot both use custom engine command {settings.engine_a}. "
            "Use separate wrapper command names for worker and reviewer."
        )
