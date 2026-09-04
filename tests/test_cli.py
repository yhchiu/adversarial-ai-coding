"""Ports helpers.test.sh:551-574 (preflight) and 1003-1040 (CLI exits)."""

import io
import os
import shutil
import subprocess
import sys

import pytest

from adversarial_ai_coding import __version__, cli
from adversarial_ai_coding.config import Settings
from adversarial_ai_coding.prompts import AGENTS_MARKER


def test_configure_stdio_replaces_cp950_with_utf8(monkeypatch):
    stdout_bytes = io.BytesIO()
    stderr_bytes = io.BytesIO()
    stdout = io.TextIOWrapper(stdout_bytes, encoding="cp950", errors="strict")
    stderr = io.TextIOWrapper(stderr_bytes, encoding="cp950", errors="strict")
    monkeypatch.setattr(cli.sys, "stdout", stdout)
    monkeypatch.setattr(cli.sys, "stderr", stderr)

    cli._configure_stdio()

    assert stdout.encoding.lower().replace("-", "") == "utf8"
    assert stderr.encoding.lower().replace("-", "") == "utf8"
    print("codex private-use: \uf4c1")
    stdout.flush()
    assert "\uf4c1" in stdout_bytes.getvalue().decode("utf-8")


def test_main_entry_configures_stdio_before_main(monkeypatch):
    order = []
    monkeypatch.setattr(cli, "_configure_stdio", lambda: order.append("stdio"))
    monkeypatch.setattr(cli, "main", lambda: order.append("main") or 0)
    with pytest.raises(SystemExit) as exc:
        cli.main_entry()
    assert exc.value.code == 0
    assert order == ["stdio", "main"]


def test_main_entry_emits_agent_unicode_when_parent_encoding_is_cp950(tmp_path):
    template = tmp_path / "AGENTS.template.md"
    template.write_text(f"{AGENTS_MARKER}\ncodex private-use: \uf4c1\n", encoding="utf-8")
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "cp950"
    env["AGENTS_TEMPLATE"] = str(template)
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from adversarial_ai_coding.cli import main_entry; main_entry()",
            "print-agents",
        ],
        capture_output=True,
        env=env,
    )
    assert proc.returncode == 0
    assert "\uf4c1" in proc.stdout.decode("utf-8")


def test_no_args_prints_usage_rc1(capsys):
    assert cli.main([], {}, stdin_isatty=False) == 1
    err = capsys.readouterr().err
    assert "Usage:" in err
    assert "print-agents" in err
    assert "若參數是檔案" not in err


def test_no_args_usage_follows_aac_lang(capsys):
    assert cli.main([], {"AAC_LANG": "zh-TW"}, stdin_isatty=False) == 1
    err = capsys.readouterr().err
    assert "Usage:" in err
    assert "print-agents" in err
    assert "若參數是檔案" in err


@pytest.mark.parametrize(
    ("lang", "needle"),
    [
        ("zh-CN", "若参数是文件"),
        ("ja-JP", "引数がファイルなら"),
        ("ko-KR", "인자가 파일이면"),
        ("pt-BR", "Se o argumento for um arquivo"),
    ],
)
def test_no_args_usage_follows_new_locales(capsys, lang, needle):
    assert cli.main([], {"AAC_LANG": lang}, stdin_isatty=False) == 1
    err = capsys.readouterr().err
    assert "Usage:" in err
    assert needle in err


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_flag_prints_usage_to_stdout_rc0(capsys, flag):
    assert cli.main([flag], {}, stdin_isatty=False) == 0
    captured = capsys.readouterr()
    assert "Usage:" in captured.out
    assert "print-agents" in captured.out
    assert "--version" in captured.out
    assert captured.err == ""


def test_help_flag_follows_aac_lang(capsys):
    assert cli.main(["--help"], {"AAC_LANG": "zh-TW"}, stdin_isatty=False) == 0
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "若參數是檔案" in out


def test_help_does_not_require_git_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--help"], {}, stdin_isatty=False) == 0
    captured = capsys.readouterr()
    assert "Usage:" in captured.out
    assert "root of the target git repository" not in captured.err


@pytest.mark.parametrize("flag", ["-V", "-v", "--version"])
def test_version_flag_prints_package_version_rc0(capsys, flag):
    assert cli.main([flag], {}, stdin_isatty=False) == 0
    captured = capsys.readouterr()
    assert f"adversarial-ai-coding {__version__}" in captured.out
    assert captured.err == ""


def test_help_takes_precedence_over_task_and_version(capsys):
    assert cli.main(["request.md", "--version", "-h"], {}, stdin_isatty=False) == 0
    captured = capsys.readouterr()
    assert "Usage:" in captured.out
    assert __version__ not in captured.out


def test_unknown_option_prints_usage_rc1(capsys):
    assert cli.main(["--nope"], {}, stdin_isatty=False) == 1
    captured = capsys.readouterr()
    assert "unrecognized option:--nope" in captured.err
    assert "Usage:" in captured.err
    assert captured.out == ""


def test_unknown_option_follows_aac_lang(capsys):
    assert cli.main(["--nope"], {"AAC_LANG": "zh-TW"}, stdin_isatty=False) == 1
    err = capsys.readouterr().err
    assert "無法辨識的選項:--nope" in err
    assert "若參數是檔案" in err


def test_parse_argv_classifies_flags_and_operands():
    assert cli._parse_argv(["-h"]) == ("help", "")
    assert cli._parse_argv(["--version"]) == ("version", "")
    assert cli._parse_argv(["print-agents"]) == ("print-agents", "")
    assert cli._parse_argv(["task"]) == ("run", "task")
    assert cli._parse_argv([]) == ("run", "")
    assert cli._parse_argv(["--", "-h"]) == ("run", "-h")
    assert cli._parse_argv(["--", "print-agents"]) == ("run", "print-agents")
    assert cli._parse_argv(["--nope"]) == ("error", "--nope")
    assert cli._parse_argv(["-"]) == ("run", "-")
    assert cli._parse_argv(["list-runs"]) == ("list-runs", "")
    # A subcommand is a bare first positional only, so "--" still hands the
    # word through as a request and no run is ever unrunnable by its name.
    assert cli._parse_argv(["--", "list-runs"]) == ("run", "list-runs")
    assert cli._parse_argv(["task", "list-runs"]) == ("run", "task")


def test_parse_argv_scopes_flags_to_the_subcommand_that_takes_them():
    """A typo must not silently start a run instead of being reported."""
    assert cli._parse_argv(["list-runs", "--full"]) == ("list-runs", "full")
    assert cli._parse_argv(["--full", "list-runs"]) == ("list-runs", "full")
    assert cli._parse_argv(["list-runs", "--fll"]) == ("error", "--fll")
    assert cli._parse_argv(["--full"]) == ("error", "--full")
    assert cli._parse_argv(["--full", "a request"]) == ("error", "--full")
    assert cli._parse_argv(["print-agents", "--full"]) == ("error", "--full")
    # Help still wins, so "list-runs --help" explains rather than lists.
    assert cli._parse_argv(["list-runs", "--help"]) == ("help", "")
    assert cli._parse_argv(["--", "list-runs", "--full"]) == ("run", "list-runs")


def _record_run(repo, run_id, request, completed=False):
    """One run's committed manifest, plus the ignored state it is judged by."""
    from adversarial_ai_coding.runindex import write_run_manifest

    write_run_manifest(
        repo / "aac" / "docs" / run_id,
        run_id=run_id,
        request=request,
        branch=f"aac/{run_id}",
        settings=Settings.from_env({}, run_id=run_id),
    )
    state_dir = repo / "aac" / ".run" / "state" / run_id
    state_dir.mkdir(parents=True)
    if completed:
        (state_dir / "completed").write_text("", encoding="utf-8")


def test_list_runs_prints_a_row_per_run_newest_first(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _record_run(tmp_path, "20260901-000000", "Port the archive module", True)
    _record_run(tmp_path, "20260904-101500", "Add slot-specific agent arguments")

    assert cli.main(["list-runs"], {}, stdin_isatty=False) == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0].split() == ["RUN_ID", "STATUS", "REQUEST"]
    assert lines[1].startswith("20260904-101500  unfinished")
    assert lines[1].endswith("Add slot-specific agent arguments")
    assert lines[2].startswith("20260901-000000  completed")
    assert len(lines) == 3


def test_list_runs_says_so_on_stderr_when_there_is_nothing(
    tmp_path, monkeypatch, capsys
):
    """The note must not reach stdout: a listing is meant to pipe into grep."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["list-runs"], {}, stdin_isatty=False) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "aac/docs/" in captured.err


def test_list_runs_needs_no_git_repository_or_request(tmp_path, monkeypatch, capsys):
    """It has to work in a fresh clone, and without the usual startup checks."""
    monkeypatch.chdir(tmp_path)
    _record_run(tmp_path, "20260904-101500", "Add a feature")
    # Only the committed half survives a clone; drop the rest.
    shutil.rmtree(tmp_path / "aac" / ".run")

    assert cli.main(["list-runs"], {}, stdin_isatty=False) == 0
    assert "20260904-101500  unknown  Add a feature" in capsys.readouterr().out


def test_list_runs_finds_a_run_that_spec_dir_moved(tmp_path, monkeypatch, capsys):
    """SPEC_DIR is a supported setting, so its runs have to list too."""
    from adversarial_ai_coding.runindex import write_run_manifest

    monkeypatch.chdir(tmp_path)
    _record_run(tmp_path, "20260901-000000", "A run in the usual place")
    moved = tmp_path / "specs" / "archive-port"
    write_run_manifest(
        moved,
        run_id="20260904-101500",
        request="Port the archive module",
        branch="aac/20260904-101500",
        settings=Settings.from_env({}, run_id="20260904-101500"),
    )
    state_dir = tmp_path / "aac" / ".run" / "state" / "20260904-101500"
    state_dir.mkdir(parents=True)
    (state_dir / "settings.json").write_text(
        '{"schema": 2, "spec_dir": "specs/archive-port"}', encoding="utf-8"
    )

    assert cli.main(["list-runs"], {}, stdin_isatty=False) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["RUN_ID", "STATUS", "PATH", "REQUEST"]
    assert "specs/archive-port" in lines[1]
    assert lines[1].endswith("Port the archive module")
    assert "aac/docs/20260901-000000" in lines[2]


def test_list_runs_full_prints_the_whole_request(tmp_path, monkeypatch, capsys):
    """The table keeps one line per run; --full is where the rest lives."""
    monkeypatch.chdir(tmp_path)
    request = (
        "## Goal\nAdd a --json output option to the CLI.\n\n"
        "## Acceptance\n- `mytool list --json` emits a valid JSON array\n"
    )
    _record_run(tmp_path, "20260904-101500", request, completed=True)

    assert cli.main(["list-runs", "--full"], {}, stdin_isatty=False) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("20260904-101500  completed  aac/docs/")
    for line in request.strip().splitlines():
        assert (f"    {line}".rstrip()) in out.splitlines()

    # The default view still cuts it down to one scannable line.
    cli.main(["list-runs"], {}, stdin_isatty=False)
    assert "Acceptance" not in capsys.readouterr().out


def test_list_runs_follows_aac_lang(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["list-runs"], {"AAC_LANG": "zh-TW"}, stdin_isatty=False) == 0
    assert "找不到任何執行記錄" in capsys.readouterr().err


def test_print_agents(capsys):
    assert cli.main(["print-agents"], {}, stdin_isatty=False) == 0
    assert AGENTS_MARKER in capsys.readouterr().out


def test_print_agents_missing_template_fails(capsys, tmp_path):
    rc = cli.main(
        ["print-agents"],
        {"AGENTS_TEMPLATE": str(tmp_path / "gone")},
        stdin_isatty=False,
    )
    assert rc != 0
    assert "AGENTS.md template not found" in capsys.readouterr().err


def test_not_a_git_repo_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = cli.main(
        ["task"], {"AGENT_A": "sh", "AGENT_B": "pwd"}, stdin_isatty=False
    )
    assert rc == 1
    assert "root of the target git repository" in capsys.readouterr().err


def test_startup_does_not_require_jq(new_repo, monkeypatch):
    monkeypatch.chdir(new_repo)
    looked_up = []

    def fake_which(name):
        looked_up.append(name)
        return None if name == "jq" else "C:/fake/" + name

    monkeypatch.setattr(cli.shutil, "which", fake_which)
    monkeypatch.setattr(cli, "run_workflow", lambda ctx, task: None)
    rc = cli.main(
        ["task"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 0
    assert "jq" not in looked_up


def test_same_agent_blocked_without_branch_side_effect(
    new_repo, monkeypatch, capsys
):
    from adversarial_ai_coding.gitops import current_branch

    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    rc = cli.main(
        ["task"],
        {"AGENT_A": "wrapper", "AGENT_B": "wrapper"},
        stdin_isatty=False,
    )
    assert rc == 1
    assert "cannot both use custom agent command wrapper" in capsys.readouterr().err
    assert current_branch(new_repo) == "main"


def test_dual_spec_human_gate_blocked_before_branch(
    new_repo, monkeypatch, capsys
):
    from adversarial_ai_coding.gitops import current_branch

    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    rc = cli.main(
        ["task"],
        {
            "AGENT_A": "sh",
            "AGENT_B": "pwd",
            "DUAL_SPEC": "1",
            "HUMAN_GATE": "0",
        },
        stdin_isatty=False,
    )
    assert rc == 1
    assert "requires HUMAN_GATE=1" in capsys.readouterr().err
    assert current_branch(new_repo) == "main"


def test_import_conflict_precedes_dual_spec_mode_preflight(
    new_repo, tmp_path, monkeypatch, capsys
):
    from adversarial_ai_coding.gitops import current_branch

    spec = tmp_path / "external-spec.md"
    spec.write_text(
        "# Spec\n\n## Assumptions and Open Questions\n\n- none\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)

    rc = cli.main(
        ["task"],
        {
            "AGENT_A": "sh",
            "AGENT_B": "pwd",
            "IMPORT_SPEC": str(spec),
            "DUAL_SPEC": "1",
            "HUMAN_GATE": "0",
        },
        stdin_isatty=False,
    )

    assert rc == 1
    err = capsys.readouterr().err
    assert "IMPORT_SPEC and DUAL_SPEC=1 are incompatible" in err
    assert "requires HUMAN_GATE=1" not in err
    assert current_branch(new_repo) == "main"


def test_plan_gate_without_tty_blocked_before_branch(
    new_repo, monkeypatch, capsys
):
    from adversarial_ai_coding.gitops import current_branch

    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda ctx, task: pytest.fail("preflight must abort before any AI call"),
    )
    rc = cli.main(
        ["task"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "HUMAN_GATE_PLAN": "1"},
        stdin_isatty=False,
    )
    assert rc == 1
    assert "HUMAN_GATE_PLAN=0" in capsys.readouterr().err
    assert current_branch(new_repo) == "main"


def test_resume_hint_printed_once_and_lock_released(
    new_repo, monkeypatch, capsys
):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda ctx, task: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    rc = cli.main(
        ["task"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 130
    err = capsys.readouterr().err
    assert err.count("RESUME_RUN=") == 1
    state_root = new_repo / "aac/.run" / "state"
    run_dir = next(state_root.iterdir())
    assert not (run_dir / "lock").exists()
    assert not (run_dir / "completed").exists()


def test_zh_tw_surface_leaves_run_log_in_english(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda ctx, task: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    rc = cli.main(
        ["task"],
        {
            "AGENT_A": "sh",
            "AGENT_B": "pwd",
            "AUTO_BRANCH": "0",
            "AAC_LANG": "zh-TW",
        },
        stdin_isatty=False,
    )
    assert rc == 130
    err = capsys.readouterr().err
    assert "已中斷" in err
    assert "若要續跑這次 run" in err
    logs = list((new_repo / "aac/.run" / "archive").rglob("*-run.log"))
    log_text = "\n".join(path.read_text(encoding="utf-8") for path in logs)
    assert "!! Workflow interrupted" not in log_text
    assert "已中斷" not in log_text


def test_completed_run_does_not_advertise_resume(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)

    def finishing_workflow(ctx, task):
        ctx.state.mark_completed()
        raise SystemExit

    monkeypatch.setattr(cli, "run_workflow", finishing_workflow)
    with pytest.raises(SystemExit):
        cli.main(
            ["task"],
            {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
            stdin_isatty=False,
        )
    assert "RESUME_RUN=" not in capsys.readouterr().err


def test_quota_abort_maps_to_75(new_repo, monkeypatch, capsys):
    from adversarial_ai_coding.config import WorkflowAbort

    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(
        cli,
        "run_workflow",
        lambda ctx, task: (_ for _ in ()).throw(WorkflowAbort("quota", rc=75)),
    )
    rc = cli.main(
        ["task"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 75
    assert "RESUME_RUN=" in capsys.readouterr().err


def test_task_file_argument_reads_in_zh_tw(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(cli, "run_workflow", lambda ctx, task: None)
    (new_repo / "task.md").write_text("task from file\n", encoding="utf-8")
    rc = cli.main(
        ["task.md"],
        {
            "AGENT_A": "sh",
            "AGENT_B": "pwd",
            "AUTO_BRANCH": "0",
            "AAC_LANG": "zh-TW",
        },
        stdin_isatty=False,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "正在從檔案讀取 request:task.md" in out
    assert "Request:task from file" in out


def test_double_dash_keeps_dashed_request(new_repo, monkeypatch):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    captured = {}
    monkeypatch.setattr(
        cli, "run_workflow", lambda ctx, task: captured.update(task=task)
    )
    rc = cli.main(
        ["--", "--help"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 0
    assert captured["task"] == "--help"


def test_task_file_argument_is_read(new_repo, monkeypatch):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    captured = {}
    monkeypatch.setattr(
        cli, "run_workflow", lambda ctx, task: captured.update(task=task)
    )
    (new_repo / "task.md").write_text("task from file\n", encoding="utf-8")
    rc = cli.main(
        ["task.md"],
        {"AGENT_A": "sh", "AGENT_B": "pwd", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 0
    assert captured["task"] == "task from file\n"


def test_resume_task_conflict_fails(new_repo, monkeypatch, capsys):
    monkeypatch.chdir(new_repo)
    monkeypatch.setattr(cli.shutil, "which", lambda name: "C:/fake/" + name)
    monkeypatch.setattr(cli, "run_workflow", lambda ctx, task: None)
    from adversarial_ai_coding.runstate import RunState, write_snapshot

    state = RunState.create(
        new_repo / "aac/.run" / "state", "r1", "snapshot task\n"
    )
    write_snapshot(
        state.state_dir, {"agent_a": "sh", "agent_b": "pwd", "branch": "main"}
    )
    state.release_lock()
    rc = cli.main(
        ["different task"],
        {"RESUME_RUN": "r1", "AUTO_BRANCH": "0"},
        stdin_isatty=False,
    )
    assert rc == 1
    assert "request snapshot" in capsys.readouterr().err


def _summary(env):
    settings = Settings.from_env({"AGENT_B": "codex", **env}, "run-id")
    return cli._slot_summary("A", settings)


def test_slot_summary_stays_bare_when_nothing_is_overridden():
    assert _summary({}) == "claude"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"MODEL_A": "opus"}, "claude [model=opus]"),
        ({"AGENT_A_ARGS": "--effort=high"}, "claude [args=--effort=high]"),
        (
            {"MODEL_A": "opus", "AGENT_A_ARGS": "--effort=high"},
            "claude [model=opus  args=--effort=high]",
        ),
        # A custom agent has no model of its own; only its arguments show.
        (
            {"AGENT_A": "my-wrapper", "AGENT_A_ARGS": "--model custom"},
            "my-wrapper [args=--model custom]",
        ),
    ],
)
def test_slot_summary_reports_what_the_slot_resolved_to(env, expected):
    """The startup line has to agree with what the call will use.

    Permission flags a reserved name does not cover take effect silently
    otherwise: the only other record is run-metadata.json, which is read
    after the run rather than while it starts.
    """
    assert _summary(env) == expected
