"""Unit tests for the terminal color engine.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md
"""

import io
import sys

import pytest

from adversarial_ai_coding.config import SettingsError
from adversarial_ai_coding.style import (
    THEMES,
    Styler,
    classify,
    enable_windows_vt,
)


def make(env=None, *, out_tty=True, err_tty=True, vt_ok=True):
    return Styler.from_env(
        env or {},
        stdout_isatty=out_tty,
        stderr_isatty=err_tty,
        enable_vt=lambda _stream: vt_ok,
    )


# --- classifier: real message samples from the codebase ---

@pytest.mark.parametrize(
    ("line", "category"),
    [
        ("!! Workflow interrupted (exit=1).", "error"),
        ("!! Protected acceptance test files were modified:", "error"),
        ("================ [spec] ================", "stage"),
        ("== skip [spec] (already completed in run test)", "stage"),
        ("--- Task 1/3:demo task ---", "stage"),
        (
            "== [phase-01-write-tests] protected controls already recorded; "
            "finishing the interrupted stage",
            "stage",
        ),
        ("--- Phase 1 task 2/3:implement parser ---", "stage"),
        (
            "--- Phase 1 tasks complete; running the phase gate. All tests "
            "written so far must pass. ---",
            "stage",
        ),
        (
            "--- All tasks complete; running full quality gate. Acceptance "
            "tests must pass. ---",
            "stage",
        ),
        ("### Human checkpoint: review spec.md, especially scope", "checkpoint"),
        (">>> Worker(claude) is running...", "progress"),
        ("(warning: reviewer execution failed)", "warning"),
        (
            "warning: IMPL_MODEL is ignored for custom implementation agent custom",
            "warning",
        ),
        (
            "(worker left uncommitted changes; script is creating a "
            "fallback commit)",
            "warning",
        ),
        ("Phase red check passed", "success"),
        ("[spec] Review approved", "success"),
        ("Spec approved by human", "success"),
        ("Task:demo", None),
        ("Quality gate:go test ./...", None),
        ("", None),
        ("plain line", None),
    ],
)
def test_classify(line, category):
    assert classify(line) == category


# --- on/off decision matrix ---

def test_auto_follows_per_stream_isatty():
    s = make(out_tty=True, err_tty=False)
    assert (s.out_enabled, s.err_enabled) == (True, False)


def test_never_beats_tty():
    s = make({"COLOR": "never"})
    assert (s.out_enabled, s.err_enabled) == (False, False)


def test_always_beats_non_tty():
    s = make({"COLOR": "always"}, out_tty=False, err_tty=False)
    assert (s.out_enabled, s.err_enabled) == (True, True)


def test_no_color_disables_auto():
    assert make({"NO_COLOR": "1"}).out_enabled is False


def test_explicit_color_beats_no_color():
    assert make({"COLOR": "always", "NO_COLOR": "1"}).out_enabled is True


def test_force_color_enables_non_tty():
    assert make({"FORCE_COLOR": "1"}, out_tty=False).out_enabled is True


def test_no_color_beats_force_color():
    assert make({"NO_COLOR": "1", "FORCE_COLOR": "1"}).out_enabled is False


def test_term_dumb_disables_auto():
    assert make({"TERM": "dumb"}).out_enabled is False


def test_invalid_color_mode_raises():
    with pytest.raises(SettingsError, match="COLOR must be auto, always, or never"):
        make({"COLOR": "sometimes"})


def test_empty_color_means_auto():
    assert make({"COLOR": ""}).out_enabled is True


# --- themes and per-category overrides ---

def test_dark_is_default_theme():
    assert make().colors == THEMES["dark"]
    assert make().colors["error"] == "1;91"


def test_light_theme_selected():
    assert make({"COLOR_THEME": "light"}).colors == THEMES["light"]
    assert make({"COLOR_THEME": "light"}).colors["error"] == "1;31"


def test_invalid_theme_raises():
    with pytest.raises(SettingsError, match="COLOR_THEME must be dark or light"):
        make({"COLOR_THEME": "solarized"})


def test_override_color_name():
    assert make({"COLOR_ERROR": "blue"}).colors["error"] == "34"


def test_override_bright_name():
    assert make({"COLOR_PROGRESS": "bright-cyan"}).colors["progress"] == "96"


def test_override_bold_bright_name():
    assert make({"COLOR_ERROR": "bold-bright-red"}).colors["error"] == "1;91"


def test_override_raw_sgr():
    assert make({"COLOR_STAGE": "1;42;30"}).colors["stage"] == "1;42;30"


def test_override_invalid_raises_naming_the_variable():
    with pytest.raises(SettingsError, match="COLOR_ERROR"):
        make({"COLOR_ERROR": "salmon"})


def test_override_wrong_prefix_order_raises():
    with pytest.raises(SettingsError, match="COLOR_ERROR"):
        make({"COLOR_ERROR": "bright-bold-red"})


def test_empty_override_means_theme_default():
    assert make({"COLOR_ERROR": ""}).colors["error"] == THEMES["dark"]["error"]


# --- Windows VT ---

def test_vt_failure_degrades_auto():
    s = make(vt_ok=False)
    assert (s.out_enabled, s.err_enabled) == (False, False)


def test_vt_failure_keeps_always():
    assert make({"COLOR": "always"}, vt_ok=False).out_enabled is True


def test_vt_failure_keeps_force_color():
    assert make({"FORCE_COLOR": "1"}, out_tty=False, vt_ok=False).out_enabled is True


def test_vt_not_probed_when_disabled():
    def boom(_stream):
        raise AssertionError("enable_vt must not be called when color is off")

    Styler.from_env(
        {"COLOR": "never"},
        stdout_isatty=True,
        stderr_isatty=True,
        enable_vt=boom,
    )


def test_enable_windows_vt_handles_non_console_stream():
    # io.StringIO has no OS handle: False on Windows, trivially True elsewhere.
    assert enable_windows_vt(io.StringIO()) is (sys.platform != "win32")


# --- painting ---

def test_paint_wraps_classified_line_with_sgr_and_reset():
    s = make({"COLOR": "always"})
    assert (
        s.paint(">>> Worker(claude) is running...")
        == "\x1b[36m>>> Worker(claude) is running...\x1b[0m"
    )


def test_paint_leaves_unclassified_lines_alone():
    s = make({"COLOR": "always"})
    assert s.paint("plain line") == "plain line"


def test_paint_styles_banner_after_leading_newline():
    s = make({"COLOR": "always"})
    first, second = s.paint(
        "\n================ [spec] ================"
    ).split("\n", 1)
    assert first == ""
    assert second == "\x1b[1;96m================ [spec] ================\x1b[0m"


def test_paint_multiline_styles_only_classified_lines():
    s = make({"COLOR": "always"})
    lines = s.paint(
        "!! Protected acceptance test files were modified:\n  tests/a_test.py"
    ).split("\n")
    assert lines[0] == (
        "\x1b[1;91m!! Protected acceptance test files were modified:\x1b[0m"
    )
    assert lines[1] == "  tests/a_test.py"


# --- printing and the plain() fallback ---

def test_out_paints_only_when_enabled(capsys):
    make({"COLOR": "always"}).out(">>> Worker(claude) is running...")
    make({"COLOR": "never"}).out(">>> Worker(claude) is running...")
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "\x1b[36m>>> Worker(claude) is running...\x1b[0m"
    assert out[1] == ">>> Worker(claude) is running..."


def test_err_prints_to_stderr_painted(capsys):
    make({"COLOR": "always"}).err("!! boom")
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "\x1b[1;91m!! boom\x1b[0m\n"


def test_plain_styler_never_colors(capsys):
    s = Styler.plain()
    assert (s.out_enabled, s.err_enabled) == (False, False)
    s.err("!! boom")
    assert capsys.readouterr().err == "!! boom\n"
