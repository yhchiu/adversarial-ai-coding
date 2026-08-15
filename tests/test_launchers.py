import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]


def _capture_program(tmp_path: Path) -> Path:
    capture = tmp_path / "capture_uv.py"
    capture.write_text(
        """
import json
import os
from pathlib import Path
import sys

Path(os.environ["AAC_TEST_LOG"]).write_text(
    json.dumps({
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "aac_lang": os.environ.get("AAC_LANG"),
    }),
    encoding="utf-8",
)
raise SystemExit(int(os.environ["AAC_TEST_EXIT"]))
""".lstrip(),
        encoding="utf-8",
    )
    return capture


def _launcher_env(tmp_path: Path, capture: Path, fake_bin: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("AAC_LANG", None)
    env.update(
        {
            "AAC_TEST_CAPTURE": str(capture),
            "AAC_TEST_EXIT": "23",
            "AAC_TEST_LOG": str(tmp_path / "uv-call.json"),
            "AAC_TEST_PYTHON": sys.executable,
            "PATH": str(fake_bin) + os.pathsep + env.get("PATH", ""),
        }
    )
    return env


def _assert_forwarded_call(tmp_path: Path, proc: subprocess.CompletedProcess) -> None:
    assert proc.returncode == 23
    call = json.loads((tmp_path / "uv-call.json").read_text(encoding="utf-8"))
    assert call["argv"][:2] == ["run", "--project"]
    assert Path(call["argv"][2]).resolve() == ROOT.resolve()
    assert call["argv"][3:] == [
        "--locked",
        "adversarial-ai-coding",
        "task with spaces.md",
        "--literal=value",
    ]
    assert Path(call["cwd"]).resolve() == tmp_path.resolve()


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_forwards_project_cwd_arguments_and_exit_code(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nexec "$AAC_TEST_PYTHON" "$AAC_TEST_CAPTURE" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    proc = subprocess.run(
        [
            str(ROOT / "scripts" / "aac"),
            "task with spaces.md",
            "--literal=value",
        ],
        cwd=tmp_path,
        env=_launcher_env(tmp_path, capture, fake_bin),
        check=False,
    )

    _assert_forwarded_call(tmp_path, proc)


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_windows_launcher_forwards_project_cwd_arguments_and_exit_code(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv.cmd").write_text(
        '@echo off\r\n"%AAC_TEST_PYTHON%" "%AAC_TEST_CAPTURE%" %*\r\n',
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            str(ROOT / "scripts" / "aac.cmd"),
            "task with spaces.md",
            "--literal=value",
        ],
        cwd=tmp_path,
        env=_launcher_env(tmp_path, capture, fake_bin),
        check=False,
    )

    _assert_forwarded_call(tmp_path, proc)


def _captured_lang(tmp_path: Path) -> str | None:
    return json.loads((tmp_path / "uv-call.json").read_text(encoding="utf-8"))[
        "aac_lang"
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_infers_zh_tw_from_lang(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nexec "$AAC_TEST_PYTHON" "$AAC_TEST_CAPTURE" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = _launcher_env(tmp_path, capture, fake_bin)
    env["LANG"] = "zh_TW.UTF-8"
    env.pop("LC_ALL", None)
    env.pop("LC_MESSAGES", None)
    env.pop("AAC_LANG", None)
    subprocess.run(
        [str(ROOT / "scripts" / "aac"), "task"],
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert _captured_lang(tmp_path) == "zh-TW"


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_does_not_infer_zh_cn(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nexec "$AAC_TEST_PYTHON" "$AAC_TEST_CAPTURE" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = _launcher_env(tmp_path, capture, fake_bin)
    env["LANG"] = "zh_CN.UTF-8"
    env["LC_ALL"] = "zh_CN.UTF-8"
    env.pop("AAC_LANG", None)
    subprocess.run(
        [str(ROOT / "scripts" / "aac"), "task"],
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert _captured_lang(tmp_path) == "zh-CN"


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_infers_ja_and_ko(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nexec "$AAC_TEST_PYTHON" "$AAC_TEST_CAPTURE" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    for lang, expected in (
        ("ja_JP.UTF-8", "ja-JP"),
        ("ko_KR.UTF-8", "ko-KR"),
        ("pt_BR.UTF-8", "pt-BR"),
    ):
        env = _launcher_env(tmp_path, capture, fake_bin)
        env["LANG"] = lang
        env.pop("LC_ALL", None)
        env.pop("LC_MESSAGES", None)
        env.pop("AAC_LANG", None)
        subprocess.run(
            [str(ROOT / "scripts" / "aac"), "task"],
            cwd=tmp_path,
            env=env,
            check=False,
        )
        assert _captured_lang(tmp_path) == expected, lang


@pytest.mark.skipif(os.name == "nt", reason="POSIX launcher")
def test_posix_launcher_keeps_explicit_english(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        '#!/bin/sh\nexec "$AAC_TEST_PYTHON" "$AAC_TEST_CAPTURE" "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = _launcher_env(tmp_path, capture, fake_bin)
    env["LANG"] = "zh_TW.UTF-8"
    env["AAC_LANG"] = "en"
    subprocess.run(
        [str(ROOT / "scripts" / "aac"), "task"],
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert _captured_lang(tmp_path) == "en"


@pytest.mark.skipif(os.name != "nt", reason="Windows launcher")
def test_windows_launcher_keeps_explicit_english(tmp_path):
    capture = _capture_program(tmp_path)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "uv.cmd").write_text(
        '@echo off\r\n"%AAC_TEST_PYTHON%" "%AAC_TEST_CAPTURE%" %*\r\n',
        encoding="utf-8",
    )
    env = _launcher_env(tmp_path, capture, fake_bin)
    env["AAC_LANG"] = "en"
    subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            str(ROOT / "scripts" / "aac.cmd"),
            "task",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
    )
    assert _captured_lang(tmp_path) == "en"
