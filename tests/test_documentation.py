import re
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _settings_row(text: str, name: str) -> str:
    """The one settings-table row documenting a variable."""
    rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` |")]
    assert len(rows) == 1, f"expected exactly one `{name}` settings row, got {len(rows)}"
    return rows[0]


def _clause_about(row: str, topic: str) -> str:
    """The clauses of a settings row that talk about one topic.

    Needed because a knob's own name can satisfy a naive substring check:
    the COLOR row lists a `never` mode, so "is the run-log guarantee
    unconditional" cannot be asked of the whole row.
    """
    clauses = [part for part in re.split(r"[;。.]", row) if topic in part]
    assert clauses, f"no clause about {topic!r} in: {row}"
    return " ".join(clauses)


def _adapter_cells(text: str, label: str) -> list[str]:
    """The Claude, Codex and Agy cells of one adapter-comparison row.

    Reading the row is what keeps assertions about "both Claude and Codex
    do X" from being written as a count over the whole file, where an
    unrelated third mention elsewhere would break them.
    """
    for line in text.splitlines():
        if line.startswith(f"| {label} |"):
            return [cell.strip() for cell in line.strip().strip("|").split("|")][1:]
    raise AssertionError(f"no {label!r} row in the adapter comparison table")


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
    # Codex reports tool calls too, and its shell wrapper is stripped. Read
    # the two adapter cells rather than counting the phrase over the whole
    # file: documenting a third streaming adapter must not fail this.
    claude, codex, agy = _adapter_cells(english, "Live output")
    assert "one-line summary per tool call" in claude
    assert "one-line summary per tool call" in codex
    assert "Raw merged output" in agy
    claude, codex, agy = _adapter_cells(chinese, "即時輸出")
    assert "每個工具呼叫一行摘要" in claude
    assert "每個工具呼叫一行摘要" in codex
    assert "原始合併輸出" in agy
    assert "`powershell -Command` or `bash -c` wrapper" in english
    assert "`powershell -Command` 或 `bash -c` 這層包裝會被剝掉" in chinese


def test_agent_session_lifecycle_is_documented_bilingually():
    documents = {
        "README.md": "docs/agent-session-lifecycle.md",
        "README.zh-TW.md": "docs/agent-session-lifecycle.zh-TW.md",
    }
    for readme_name, guide_name in documents.items():
        assert guide_name in _read(readme_name)
        guide = _read(guide_name)
        for detail in (
            "write-spec",
            "write-implementation-plan",
            "write-acceptance-tests",
            "write-code",
            "final-review-and-fixes",
            "phase-N-write-tests",
            "phase-N-implement",
            "write-spec-a",
            "finalize-spec",
            "RESUME_RUN",
            "AgentSession",
        ):
            assert detail in guide
    assert "Every reviewer call is fresh" in _read(
        "docs/agent-session-lifecycle.md"
    )
    assert "每次 reviewer 呼叫都是全新 session" in _read(
        "docs/agent-session-lifecycle.zh-TW.md"
    )


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


def test_aac_lang_is_documented_bilingually():
    for name in ("README.md", "README.zh-TW.md"):
        row = _settings_row(_read(name), "AAC_LANG")
        assert "zh-TW" in row
        assert "zh-CN" in row
        assert "ja-JP" in row
        assert "ko-KR" in row
        assert "scripts/aac" in row
        assert "scripts/aac.cmd" in row
        assert "LANG" in row
        assert "English" in row or "英文" in row


def test_color_settings_are_documented_bilingually():
    """The COLOR row names every knob, the redirect rule and the log rule.

    This used to pin four whole sentences per language, which meant any
    rewording of the row broke it in both. What each knob actually does
    already has a behavioural test in test_style.py (auto follows isatty,
    always beats non-tty, NO_COLOR beats auto, FORCE_COLOR enables
    non-tty, NO_COLOR beats FORCE_COLOR, TERM=dumb disables auto), so the
    prose adds no coverage. What is left is the review finding that put
    this test here (dfa38b3): the row has to cover how the modes interact
    with redirects, and the run-log guarantee has to stay unconditional.
    """
    redirect = {"README.md": "redirect", "README.zh-TW.md": "重導向"}
    unconditional = {"README.md": "never", "README.zh-TW.md": "永遠不"}
    for name in ("README.md", "README.zh-TW.md"):
        readme = _read(name)
        color = _settings_row(readme, "COLOR")
        for knob in (
            "`auto`", "`always`", "`never`", "NO_COLOR", "FORCE_COLOR", "TERM=dumb"
        ):
            assert knob in color, f"{name}: the COLOR row must document {knob}"
        assert redirect[name] in color
        assert unconditional[name] in _clause_about(color, "run log")
        assert "COLOR_THEME" in readme
        assert "COLOR_ERROR" in readme
        assert "bold-bright-red" in readme
        assert "1;91" in readme


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
