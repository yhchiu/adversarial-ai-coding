"""Ports helpers.test.sh:40-61 (detect_gate) and adds gate_loop unit tests."""

import json

import pytest

import adversarial_ai_coding.gates as gates

from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.config import (
    CARGO_TOOLS,
    DEFAULT_TOOLS,
    GO_TOOLS,
    NPM_TOOLS,
    PYTEST_TOOLS,
    VCS_TOOLS,
)
from adversarial_ai_coding.gates import (
    detect_build_gate,
    detect_gate,
    detect_tools,
    gate_loop,
    run_shell,
)
from adversarial_ai_coding.prompts import default_prompts_dir

PROMPTS = default_prompts_dir({})


def test_detect_gate_go_project(tmp_path):
    (tmp_path / "go.mod").touch()
    assert detect_gate(tmp_path) == "go build ./... && go vet ./... && go test ./..."
    assert detect_build_gate(tmp_path) == "go build ./..."


def test_detect_gate_npm_with_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "jest"}}), encoding="utf-8"
    )
    assert detect_gate(tmp_path) == "npm test"


def test_detect_gate_npm_without_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {}}), encoding="utf-8"
    )
    assert detect_gate(tmp_path) == ""
    (tmp_path / "package.json").write_text("broken json", encoding="utf-8")
    assert detect_gate(tmp_path) == ""


def _python_project(cwd, *, runner="pytest"):
    """A project whose files say pytest runs its tests."""
    (cwd / "pyproject.toml").write_text(
        f'[project]\nname = "x"\n[dependency-groups]\ndev = ["{runner}"]\n',
        encoding="utf-8",
    )
    tests = cwd / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")


def test_detect_gate_python_project_uses_the_interpreter_on_this_machine(
    tmp_path, monkeypatch
):
    _python_project(tmp_path)
    monkeypatch.setattr(gates.shutil, "which", lambda name: None)
    assert detect_gate(tmp_path) == ""
    monkeypatch.setattr(
        gates.shutil,
        "which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    assert detect_gate(tmp_path) == "python3 -m pytest"
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert detect_gate(tmp_path) == "python -m pytest"
    # Python projects have no compile step to gate per task.
    assert detect_build_gate(tmp_path) == ""


def test_detect_gate_python_prefers_the_tool_that_owns_the_environment(tmp_path):
    _python_project(tmp_path)
    (tmp_path / "poetry.lock").touch()
    assert detect_gate(tmp_path) == "poetry run pytest"
    (tmp_path / "uv.lock").touch()
    assert detect_gate(tmp_path) == "uv run pytest"


def test_detect_gate_python_venv_beats_the_bare_interpreter(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").touch()
    expected = gates.Path(".venv") / "Scripts" / "python.exe"
    assert detect_gate(tmp_path) == f"{expected} -m pytest"


def test_detect_gate_python_posix_venv_is_found_too(tmp_path, monkeypatch):
    _python_project(tmp_path)
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    binaries = tmp_path / ".venv" / "bin"
    binaries.mkdir(parents=True)
    (binaries / "python").touch()
    expected = gates.Path(".venv") / "bin" / "python"
    assert detect_gate(tmp_path) == f"{expected} -m pytest"


def test_detect_gate_python_without_pytest_or_tests_claims_nothing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    # A marker file alone is not a pytest project.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n', encoding="utf-8"
    )
    assert detect_gate(tmp_path) == ""
    # Nor is one whose runner is something else.
    _python_project(tmp_path, runner="nose2")
    assert detect_gate(tmp_path) == ""
    # pytest named, but nothing for it to collect: it would exit 5 and the
    # gate would read that as a failure to repair.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.pytest.ini_options]\naddopts = "-q"\n',
        encoding="utf-8",
    )
    for path in (tmp_path / "tests").rglob("*.py"):
        path.unlink()
    assert detect_gate(tmp_path) == ""
    (tmp_path / "test_root.py").write_text(
        "def test_root():\n    pass\n", encoding="utf-8"
    )
    assert detect_gate(tmp_path) == "python -m pytest"


def test_detect_gate_python_config_files_other_than_pyproject(tmp_path, monkeypatch):
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    (tmp_path / "setup.py").write_text("setup()\n", encoding="utf-8")
    (tmp_path / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    assert detect_gate(tmp_path) == ""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    assert detect_gate(tmp_path) == "python -m pytest"


def test_detect_gate_another_language_still_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(gates.shutil, "which", lambda name: f"/usr/bin/{name}")
    _python_project(tmp_path)
    (tmp_path / "go.mod").touch()
    assert detect_gate(tmp_path) == "go build ./... && go vet ./... && go test ./..."
    assert detect_build_gate(tmp_path) == "go build ./..."


def test_detect_tools_names_only_the_ecosystems_present(tmp_path):
    (tmp_path / "go.mod").touch()
    assert detect_tools(tmp_path) == f"{VCS_TOOLS},{GO_TOOLS}"
    (tmp_path / "Cargo.toml").touch()
    assert detect_tools(tmp_path) == f"{VCS_TOOLS},{GO_TOOLS},{CARGO_TOOLS}"


def test_detect_tools_is_a_union_where_the_gate_is_a_first_match(tmp_path):
    # A Go service with an npm front end runs both test commands, even
    # though only the Go gate is detected.
    (tmp_path / "go.mod").touch()
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert detect_tools(tmp_path) == f"{VCS_TOOLS},{GO_TOOLS},{NPM_TOOLS}"
    assert detect_gate(tmp_path) == "go build ./... && go vet ./... && go test ./..."


def test_detect_tools_allows_pytest_before_any_test_file_exists(tmp_path):
    # The gate needs test files; the allowlist is what lets the reviewer
    # write them and run them in the first place.
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[tool.pytest.ini_options]\naddopts = "-q"\n',
        encoding="utf-8",
    )
    assert detect_tools(tmp_path) == f"{VCS_TOOLS},{PYTEST_TOOLS}"
    assert detect_gate(tmp_path) == ""


def test_detect_tools_keeps_the_whole_default_when_nothing_is_detected(tmp_path):
    # The gate here is one the user set by hand, so narrowing would only
    # take away rules the run used to have.
    assert detect_tools(tmp_path) == DEFAULT_TOOLS
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n', encoding="utf-8"
    )
    assert detect_tools(tmp_path) == DEFAULT_TOOLS


def test_detect_gate_cargo_and_unknown(tmp_path):
    (tmp_path / "Cargo.toml").touch()
    assert detect_gate(tmp_path) == "cargo test"
    assert detect_build_gate(tmp_path) == "cargo build"
    for file in tmp_path.iterdir():
        file.unlink()
    assert detect_gate(tmp_path) == ""
    assert detect_build_gate(tmp_path) == ""


def run_gate(tmp_path, results, max_rounds=3):
    """results: list of (rc, output) returned per shell invocation."""
    calls = {"shell": 0, "work": []}

    def fake_shell(cmd, cwd):
        rc, out = results[calls["shell"]]
        calls["shell"] += 1
        return rc, out

    def fake_work(prompt):
        calls["work"].append(prompt)

    gate_loop(
        "make check",
        cwd=tmp_path,
        prompts_dir=PROMPTS,
        max_rounds=max_rounds,
        do_work=fake_work,
        log=lambda _message: None,
        notify=lambda _message: None,
        stage="write-code",
        run_shell=fake_shell,
    )
    return calls


def test_gate_loop_empty_cmd_skips(tmp_path):
    gate_loop(
        "",
        cwd=tmp_path,
        prompts_dir=PROMPTS,
        max_rounds=3,
        do_work=lambda prompt: pytest.fail("must not be called"),
        log=lambda _message: None,
        notify=lambda _message: None,
        stage="s",
        run_shell=lambda cmd, cwd: pytest.fail("must not run"),
    )


def test_gate_loop_pass_first_try(tmp_path):
    calls = run_gate(tmp_path, [(0, "all good")])
    assert calls["shell"] == 1
    assert calls["work"] == []


def test_gate_loop_failure_repair_then_pass(tmp_path):
    calls = run_gate(tmp_path, [(1, "FAIL: acc_test"), (0, "ok")])
    assert calls["shell"] == 2
    assert len(calls["work"]) == 1
    assert "make check" in calls["work"][0]
    assert "FAIL: acc_test" in calls["work"][0]


def test_gate_loop_max_rounds_aborts(tmp_path):
    with pytest.raises(WorkflowAbort) as exc:
        run_gate(tmp_path, [(1, "boom")] * 3, max_rounds=3)
    assert exc.value.rc == 1
    assert "Quality gate failed" in str(exc.value)


def test_gate_loop_output_tail_truncated(tmp_path):
    long_out = "\n".join(f"line{i}" for i in range(400))
    calls = run_gate(tmp_path, [(1, long_out), (0, "ok")])
    prompt = calls["work"][0]
    assert "line399" in prompt
    assert "line100" not in prompt


def test_run_shell_uses_platform_shell(tmp_path):
    rc, output = run_shell("exit 0", tmp_path)
    assert rc == 0
    assert output == ""
