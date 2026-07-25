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


def test_phased_suggestion_is_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "phased-suggestion.json" in readme
        assert "Enable Phased ATDD for this run?" in readme
    assert "phased-suggestion.json" in _read("resources/AGENTS.template.md")
    assert "phased-suggestion.json" in _read("docs/python-port-parity.md")
