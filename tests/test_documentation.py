from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_artifact_layout_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        # The committed half and the ignored half, and the fact that one
        # ignore file is what separates them.
        assert "aac/docs/<RUN_ID>/" in readme
        assert "aac/.run/" in readme
        assert "git add -A" in readme
        assert "docs/adr/0001-single-aac-root-for-run-artifacts.md" in readme
        # The old locations must not linger anywhere in the docs.
        assert ".workflow" not in readme
        assert "specs/<RUN_ID>" not in readme
        # Removed knob.
        assert "RUNS_DIR" not in readme
    # Every file the workflow writes is listed, including the ones the
    # section omitted before the layout moved.
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        for name in (
            "spec-a.verdict-by-b.json",
            "spec-b.verdict-by-a.json",
            "phased-suggestion.json",
            "last-agent-output.txt",
            "last-agent-cli.raw",
            "settings.json",
            "ledger.json",
            "tasks-remaining",
            "last-head",
        ):
            assert name in readme
    # The AGENTS.md rules shipped into the target repo must name the same
    # paths the prompts substitute at render time.
    template = _read("resources/AGENTS.template.md")
    assert ".workflow" not in template
    for name in (
        "aac/.run/review.md",
        "aac/.run/verdict.json",
        "aac/.run/phased-suggestion.json",
        "aac/.run/protected-tests.txt",
        "aac/.run/spec-merge-request.md",
    ):
        assert name in template
    for name in ("docs/how-it-works.md", "docs/how-it-works.zh-TW.md"):
        assert ".workflow" not in _read(name)


def test_run_log_location_is_documented_bilingually():
    # The old text pointed at .workflow/logs/, which never existed: the log
    # lives inside each run's archive directory.
    assert "aac/.run/archive/<RUN_ID>/" in _read("README.md")
    assert "aac/.run/archive/<RUN_ID>/logs/001-run.log" in _read("README.zh-TW.md")
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "logs/001-run.log" in readme


def test_codex_reserved_aliases_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "--dangerously-bypass-approvals-and-sandbox" in readme
        assert "--yolo" in readme
        assert "--ephemeral" in readme
        assert "-mMODEL" in readme


def test_quota_detection_channel_is_documented_bilingually():
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    assert "Detection reads only the agent's own error channel" in english
    assert "Agy has no" in english and "whole output is still scanned" in english
    assert "| Reported reset time | Claude |" in english
    assert "判斷只讀 agent 自己的錯誤通道,絕不讀 agent 執行過的指令輸出" in chinese
    assert "agy 沒有結構化通道,仍然掃整包輸出" in chinese
    assert "claude 的串流會回報精確的重置時刻" in chinese


def test_agent_streaming_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "claude -p --output-format stream-json" in readme
        assert "`[A claude] `" in readme
        assert "`AGENT`" in readme
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    assert "`--output-format`, `--verbose`, or `--json-schema`" in english
    assert "archived artifacts and the run log never contain" in english
    assert "`--output-format`、`--verbose` 或 `--json-schema`" in chinese
    assert "封存產物與 run log 永遠不含前綴" in chinese
    # Codex reports tool calls too, and its shell wrapper is stripped.
    assert english.count("Messages and a one-line summary per tool call") == 2
    assert chinese.count("訊息,加上每個工具呼叫一行摘要") == 2
    assert "`powershell -Command` or `bash -c` wrapper" in english
    assert "`powershell -Command` 或 `bash -c` 這層包裝會被剝掉" in chinese


def test_cross_platform_launchers_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "adversarial-ai-coding/scripts" in readme
        assert "scripts/aac" in readme
        assert "scripts/aac.cmd" in readme
        assert "aac request.md" in readme
        assert "Windows PowerShell" in readme
        assert "--locked" in readme
        assert 'uv run --project "$AAC_PROJECT"' not in readme


def test_protected_test_recovery_requires_commit_before_new_base():
    english = _read("README.md").lower()
    recovery = english[english.index("## protected acceptance tests") :]
    assert recovery.index("commit") < recovery.index("protected-base.sha")


def test_empty_path_list_does_not_disable_control_integrity_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "protected-tests.txt" in readme
        assert "protected-base.sha" in readme


def test_phased_mode_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "PHASES" in readme
        assert "PHASE_GATE_CMD" in readme
        assert "PHASE_REVIEW" in readme
        assert "regression-guard" in readme
    assert "regression-guard" in _read("resources/AGENTS.template.md")


def test_import_mode_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "IMPORT_SPEC" in readme
        assert "IMPORT_PLAN" in readme
        assert "IMPORT_REVIEW" in readme
        assert "import-format" in readme
    contract = _read("docs/import-format.md")
    assert contract.isascii()
    assert "Assumptions" in contract and "Open Questions" in contract
    assert "- [ ] " in contract
    assert "## Phase" in contract
    assert "IMPORT_REVIEW" in contract
    prompt = _read("resources/import-authoring-prompt.md")
    assert prompt.isascii()
    assert "Assumptions and Open Questions" in prompt
    assert "- [ ] " in prompt


def test_color_settings_are_documented_bilingually():
    readmes = {
        "README.md": (
            "`auto` normally keeps redirected or non-terminal output plain",
            "`FORCE_COLOR` can force ANSI color in `auto` mode, including redirects",
            "`always` can emit ANSI color to redirected output",
            "the archived run log never contains color codes, even when color is forced",
        ),
        "README.zh-TW.md": (
            "`auto` 通常讓重導向或非終端機輸出保持無色碼",
            "`FORCE_COLOR` 可在 `auto` 模式強制 ANSI 色碼,包括重導向輸出",
            "`always` 可讓重導向輸出包含 ANSI 色碼",
            "封存的 run log 即使強制上色也永遠不含色碼",
        ),
    }
    for name, redirect_details in readmes.items():
        readme = _read(name)
        assert "`COLOR`" in readme
        assert "COLOR_THEME" in readme
        assert "NO_COLOR" in readme
        assert "COLOR_ERROR" in readme
        assert "bold-bright-red" in readme
        assert "1;91" in readme
        for detail in redirect_details:
            assert detail in readme


def test_agents_drift_note_is_documented_bilingually():
    for name in ("README.md", "README.zh-TW.md"):
        readme = _read(name)
        assert "adversarial-ai-coding:begin" in readme
        assert "adversarial-ai-coding:end" in readme
    assert "out of date" in _read("README.md")
    assert "過時" in _read("README.zh-TW.md")


def test_phased_suggestion_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "phased-suggestion.json" in readme
        assert "Enable Phased ATDD for this run? [y/N]:" in readme
        phases_rows = [
            line for line in readme.splitlines() if line.startswith("| `PHASES` |")
        ]
        assert len(phases_rows) == 1
        assert "`IMPORT_PLAN`" in phases_rows[0]
    assert "phased-suggestion.json" in _read("resources/AGENTS.template.md")
    for name in (
        "docs/how-it-works.md",
        "docs/how-it-works.zh-TW.md",
        "docs/python-port-parity.md",
    ):
        document = _read(name)
        suggestion = document.index("phased-suggestion.json")
        context = document[max(0, suggestion - 300) : suggestion + 300]
        assert "`PHASES`" in context
        assert "`IMPORT_PLAN`" in context


def test_imported_spec_without_review_has_no_phased_suggestion_bilingually():
    required_details = {
        "README.md": (
            "IMPORT_SPEC` + `IMPORT_REVIEW=0",
            "no spec reviewer runs",
            "no phased suggestion is produced or offered",
        ),
        "README.zh-TW.md": (
            "IMPORT_SPEC` + `IMPORT_REVIEW=0",
            "不會執行 spec reviewer",
            "不會產生或提供分階段模式建議",
        ),
    }
    for name, details in required_details.items():
        readme = _read(name)
        for detail in details:
            assert detail in readme
