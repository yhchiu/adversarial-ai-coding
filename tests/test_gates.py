"""Ports helpers.test.sh:40-61 (detect_gate) and adds gate_loop unit tests."""

import json

import pytest

from adversarial_ai_coding.config import WorkflowAbort
from adversarial_ai_coding.gates import detect_build_gate, detect_gate, gate_loop
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
