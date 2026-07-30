"""cli.py styler wiring: colored terminal, plain run log and redirects.

Spec: docs/superpowers/specs/2026-07-20-terminal-colors-design.md

The wiring is two lines in cli.main: the WorkflowContext gets styler.out
and styler.err as its sinks, and ctx.log splits into that sink plus a
plain file. Reaching those lines used to mean driving a whole fake
workflow, which cost 36 seconds for two assertions. A stub workflow that
emits the same lines through the real begin_stage proves the same thing,
and what the styler does with a line is already covered by test_style.py.
"""

from pathlib import Path

from adversarial_ai_coding import cli
from adversarial_ai_coding import workflow as wf_mod
from adversarial_ai_coding.style import Styler

BASE_ENV = {
    "AGENT_A": "sh",
    "AGENT_B": "pwd",
    "AUTO_BRANCH": "0",
    "HUMAN_GATE": "0",
}


def drive(new_repo, monkeypatch, **overrides) -> int:
    """cli.main over a stub workflow that emits one line of each kind."""

    def stub_workflow(ctx, task):
        # begin_stage emits the stage banner through ctx.log, the one line
        # that has to reach the terminal painted and the run log plain.
        wf_mod.begin_stage(ctx, "write-spec")
        # What work() emits before every agent call.
        ctx.echo(">>> Worker(A) is running...")

    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(cli, "run_workflow", stub_workflow)
    return cli.main(["demo task"], {**BASE_ENV, **overrides}, stdin_isatty=False)


def run_log_text(repo: Path) -> str:
    logs = list((repo / "aac/.run" / "archive").rglob("*-run.log"))
    assert logs, "expected an archived run log"
    return "\n".join(log.read_text(encoding="utf-8") for log in logs)


def test_invalid_color_value_fails_fast(new_repo, monkeypatch, capsys):
    assert drive(new_repo, monkeypatch, COLOR="sometimes") == 1
    assert "COLOR must be auto, always, or never" in capsys.readouterr().err


def test_color_always_paints_terminal_but_not_run_log(
    new_repo, monkeypatch, capsys
):
    assert drive(new_repo, monkeypatch, COLOR="always") == 0
    out = capsys.readouterr().out
    # Stage banner and progress lines carry dark-theme SGR codes.
    assert "\x1b[1;96m================" in out
    assert "\x1b[36m>>> " in out
    # The archived run log stays plain.
    assert "\x1b[" not in run_log_text(new_repo)


def test_auto_mode_emits_no_codes_when_not_a_tty(new_repo, monkeypatch, capsys):
    # pytest capture streams are not ttys, so auto behaves like a redirect.
    assert drive(new_repo, monkeypatch) == 0
    captured = capsys.readouterr()
    assert "\x1b[" not in captured.out
    assert "\x1b[" not in captured.err
    assert "\x1b[" not in run_log_text(new_repo)


def test_ctx_log_file_stays_plain_when_echo_is_styled(make_ctx, capsys):
    styler = Styler.from_env(
        {"COLOR": "always"},
        stdout_isatty=False,
        stderr_isatty=False,
        enable_vt=lambda _stream: True,
    )
    ctx = make_ctx()
    ctx.echo = styler.out
    ctx.log("!! Protected acceptance test files were modified:")
    assert "\x1b[1;91m" in capsys.readouterr().out
    log_text = ctx.archive.log_path.read_text(encoding="utf-8")
    assert "\x1b[" not in log_text
    assert "!! Protected acceptance test files were modified:" in log_text
