"""Terminal color styling for the workflow's own status messages.

Colors are applied only at print time (the echo boundary wired in
cli.py), so message strings, the run log, and archived artifacts always
stay plain text. Configuration is environment-only and never persisted
to the resume snapshot -- like NOTIFY_CMD, colors are per-attempt
presentation settings.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Callable, Mapping, TextIO

from .config import SettingsError

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "error": "1;91",
        "stage": "1;96",
        "checkpoint": "1;95",
        "progress": "36",
        "warning": "93",
        "success": "32",
    },
    "light": {
        "error": "1;31",
        "stage": "1;34",
        "checkpoint": "1;35",
        "progress": "34",
        "warning": "33",
        "success": "32",
    },
}

_NAMED_COLORS = {
    "black": 30,
    "red": 31,
    "green": 32,
    "yellow": 33,
    "blue": 34,
    "magenta": 35,
    "cyan": 36,
    "white": 37,
}

_RAW_SGR = re.compile(r"^[0-9]+(;[0-9]+)*$")

# Fixed phrases from workflow.py / review.py / phaseflow.py; the only
# non-prefix classification rules.
_SUCCESS_SUFFIXES = ("check passed", "Review approved", "approved by human")

ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004


def classify(line: str) -> str | None:
    """Map one output line to a style category by message conventions."""
    text = line.strip()
    if text.startswith("!! "):
        return "error"
    if text.startswith(("====", "== skip", "--- Task")):
        return "stage"
    if text.startswith("### "):
        return "checkpoint"
    if text.startswith(">>> "):
        return "progress"
    if text.startswith("(") and text.endswith(")"):
        return "warning"
    if text.endswith(_SUCCESS_SUFFIXES):
        return "success"
    return None


def _sgr_from_value(name: str, raw: str) -> str:
    """Parse a COLOR_<CATEGORY> value: a color name or raw SGR params."""
    if _RAW_SGR.match(raw):
        return raw
    parts = raw.split("-")
    bold = parts[0] == "bold"
    if bold:
        parts = parts[1:]
    bright = bool(parts) and parts[0] == "bright"
    if bright:
        parts = parts[1:]
    if len(parts) == 1 and parts[0] in _NAMED_COLORS:
        code = _NAMED_COLORS[parts[0]] + (60 if bright else 0)
        return f"1;{code}" if bold else str(code)
    raise SettingsError(
        f"{name} must be a color name like red, bright-cyan, or "
        f"bold-bright-red, or raw SGR parameters like 1;91, got: {raw}"
    )


def _resolve_mode(env: Mapping[str, str]) -> str:
    mode = env.get("COLOR") or "auto"
    if mode not in ("auto", "always", "never"):
        raise SettingsError(
            f"COLOR must be auto, always, or never, got: {mode}"
        )
    return mode


def _stream_enabled(
    mode: str, env: Mapping[str, str], isatty: bool
) -> tuple[bool, bool]:
    """Return (enabled, forced); forced skips the VT-failure degrade."""
    if mode == "always":
        return True, True
    if mode == "never":
        return False, False
    if env.get("NO_COLOR"):
        return False, False
    if env.get("FORCE_COLOR"):
        return True, True
    if env.get("TERM") == "dumb":
        return False, False
    return isatty, False


def enable_windows_vt(stream: TextIO) -> bool:
    """Enable VT processing on a Windows console stream; True on success.

    Always True off Windows. Windows Terminal ships with VT enabled;
    this call is for classic conhost.
    """
    if sys.platform != "win32":
        return True
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(stream.fileno())
    except (OSError, ValueError):
        return False
    kernel32 = ctypes.windll.kernel32
    mode = ctypes.c_uint32()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return False
    if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
        return True
    return bool(
        kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
        )
    )


@dataclass(frozen=True)
class Styler:
    """Paints classified lines; per-stream on/off fixed at construction."""

    colors: Mapping[str, str]
    out_enabled: bool
    err_enabled: bool

    @classmethod
    def plain(cls) -> "Styler":
        """A never-coloring styler, for use before config parsing succeeds."""
        return cls(colors=THEMES["dark"], out_enabled=False, err_enabled=False)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str],
        *,
        stdout_isatty: bool | None = None,
        stderr_isatty: bool | None = None,
        enable_vt: Callable[[TextIO], bool] | None = None,
    ) -> "Styler":
        mode = _resolve_mode(env)
        theme_name = env.get("COLOR_THEME") or "dark"
        if theme_name not in THEMES:
            raise SettingsError(
                f"COLOR_THEME must be dark or light, got: {theme_name}"
            )
        colors = dict(THEMES[theme_name])
        for category in colors:
            var = f"COLOR_{category.upper()}"
            raw = env.get(var) or ""
            if raw:
                colors[category] = _sgr_from_value(var, raw)
        if stdout_isatty is None:
            stdout_isatty = sys.stdout.isatty()
        if stderr_isatty is None:
            stderr_isatty = sys.stderr.isatty()
        if enable_vt is None:
            enable_vt = enable_windows_vt
        out_enabled, out_forced = _stream_enabled(mode, env, stdout_isatty)
        err_enabled, err_forced = _stream_enabled(mode, env, stderr_isatty)
        if out_enabled and not enable_vt(sys.stdout) and not out_forced:
            out_enabled = False
        if err_enabled and not enable_vt(sys.stderr) and not err_forced:
            err_enabled = False
        return cls(
            colors=colors, out_enabled=out_enabled, err_enabled=err_enabled
        )

    def paint(self, text: str) -> str:
        """Style each classified line; unclassified lines pass through."""
        styled = []
        for line in text.split("\n"):
            category = classify(line)
            sgr = self.colors.get(category) if category else None
            styled.append(f"\x1b[{sgr}m{line}\x1b[0m" if sgr else line)
        return "\n".join(styled)

    def out(self, text: str) -> None:
        print(self.paint(text) if self.out_enabled else text)

    def err(self, text: str) -> None:
        print(
            self.paint(text) if self.err_enabled else text, file=sys.stderr
        )
