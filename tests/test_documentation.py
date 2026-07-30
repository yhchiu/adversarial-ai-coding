from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_codex_reserved_aliases_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "--dangerously-bypass-approvals-and-sandbox" in readme
        assert "--yolo" in readme
        assert "--ephemeral" in readme
        assert "-mMODEL" in readme


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
        assert "aac task.md" in readme
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
