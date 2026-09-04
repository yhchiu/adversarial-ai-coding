import re
from pathlib import Path

from adversarial_ai_coding.config import DEFAULT_TOOLS


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
    """The Claude, Codex, Agy and OpenCode cells of one adapter-comparison row.

    Reading the row is what keeps assertions about "both Claude and Codex
    do X" from being written as a count over the whole file, where an
    unrelated extra mention elsewhere would break them.
    """
    for line in text.splitlines():
        if line.startswith(f"| {label} |"):
            return [cell.strip() for cell in line.strip().strip("|").split("|")][1:]
    raise AssertionError(f"no {label!r} row in the adapter comparison table")


def _reserved_row(text: str, adapter: str) -> str:
    """The reserved-flag cell of one built-in command.

    Read per adapter so that a flag reserved for one CLI can never
    satisfy -- or break -- an assertion about another. Agy reserves
    `--prompt`, which OpenCode must still never claim.
    """
    rows = [
        cells
        for cells in (
            line.strip().strip("|").split("|")
            for line in text.splitlines()
            if line.startswith(f"| {adapter}") and "AGENT_A_ARGS" in line
        )
        # Two cells is what tells the reserved table apart from the
        # four-column adapter comparison, which also names these variables.
        if len(cells) == 2
    ]
    assert len(rows) == 1, f"expected one {adapter} reserved row, got {len(rows)}"
    return rows[0][1]


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
    for guide in (
        _read("docs/troubleshooting.md"),
        _read("docs/troubleshooting.zh-TW.md"),
    ):
        assert "aac/.run/archive/<RUN_ID>/logs/001-run.log" in guide


def test_troubleshooting_guides_are_linked_and_actionable_bilingually():
    documents = {
        "README.md": "docs/troubleshooting.md",
        "README.zh-TW.md": "docs/troubleshooting.zh-TW.md",
    }
    default_tools = DEFAULT_TOOLS

    for readme_name, guide_name in documents.items():
        readme = _read(readme_name)
        assert guide_name in readme
        assert default_tools in _settings_row(readme, "TOOLS")

        guide = _read(guide_name)
        assert default_tools in guide
        for detail in (
            "Bash(gofmt *)",
            "Bash(npm test)",
            "Bash(cargo build)",
            "Bash(uv run pytest *)",
            "workspace-write",
            "--dangerously-skip-permissions",
            "--auto",
            "BUILD_GATE_CMD",
            "GATE_CMD",
            "RESUME_RUN",
        ):
            assert detail in guide

    assert "Setting `TOOLS` replaces that entire value" in _read(
        "docs/troubleshooting.md"
    )
    assert "設定 `TOOLS` 會取代整個值" in _read(
        "docs/troubleshooting.zh-TW.md"
    )
    assert "The rule added in that example is `Bash(gofmt *)`" in _read(
        "docs/troubleshooting.md"
    )
    assert "上例實際新增的是 `Bash(gofmt *)`" in _read(
        "docs/troubleshooting.zh-TW.md"
    )


def test_permission_prompt_adapter_table_is_documented_bilingually():
    guides = {
        "docs/troubleshooting.md": (
            "AAC calls agents non-interactively, so nobody can answer a prompt.",
            "| Adapter | Arguments and settings added by AAC | Effective behavior |",
        ),
        "docs/troubleshooting.zh-TW.md": (
            "AAC 以非互動模式呼叫 agent,沒有人能回答權限 prompt。",
            "| Adapter | AAC 加入的參數與設定 | 實際行為 |",
        ),
    }

    for guide_name, (opening, header) in guides.items():
        guide = _read(guide_name)
        assert f"{opening}\n\n{header}" in guide
        permission_section = guide.split(header, 1)[1].split("### Claude Code", 1)[0]
        for detail in (
            "--permission-mode acceptEdits",
            "--allowedTools <TOOLS>",
            "--sandbox workspace-write",
            'sandbox_mode="workspace-write"',
            "--approve-for-me",
            "--dangerously-skip-permissions",
            "--auto",
            "AGENT_A_ARGS",
            "AGENT_B_ARGS",
            "IMPL_ARGS",
        ):
            assert detail in permission_section


def test_codex_reserved_aliases_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "--dangerously-bypass-approvals-and-sandbox" in readme
        assert "--yolo" in readme
        assert "--ephemeral" in readme
        assert "-mMODEL" in readme


def test_quota_detection_channel_is_documented_bilingually():
    english = _read("docs/troubleshooting.md")
    chinese = _read("docs/troubleshooting.zh-TW.md")
    assert "AAC reads only an agent's own error channel" in english
    assert "Agy has no" in english and "complete output is scanned" in english
    assert "| Reported reset epoch | Claude |" in english
    assert "AAC 只讀 agent 自己的錯誤通道做 quota 判斷" in chinese
    assert "Agy 沒有結構化 event 邊界,所以仍掃完整輸出" in chinese
    assert "| 回報的 reset epoch | Claude |" in chinese
    # OpenCode relays each provider's own wording, so what makes a 429
    # detectable is the status it reports, not the words in the message.
    assert "retaining the provider's" in english and "HTTP status" in english
    assert "保留 provider HTTP status" in chinese


def test_opencode_permission_and_reserved_args_are_documented_bilingually():
    """`--auto` is a safety note, not only a table cell.

    It approves every permission the user has not explicitly denied, which
    is the same trust level the Agy note already warns about.
    """
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    assert "`opencode` runs with `--auto`" in english
    assert "explicitly denied" in english
    assert "**opencode agent 使用 `--auto`**" in chinese
    assert "自動核准" in chinese
    for readme in (english, chinese):
        # Reserved flags must be flags `opencode run` really has. --command
        # would replace the workflow prompt; --interactive and --prompt do
        # not exist and were never ours to reserve. Read from OpenCode's own
        # row, because agy does reserve a --prompt of its own.
        row = _reserved_row(readme, "OpenCode")
        assert "`--command`" in row
        assert "--interactive" not in row
        assert "`--prompt`" not in row


def test_tool_allowlist_belongs_to_the_tools_variable_bilingually():
    """The reserved row and the TOOLS row have to agree.

    Reserving the flag is only defensible while the docs point at the
    variable that replaced it, so both halves are asserted together.
    """
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    for readme in (english, chinese):
        row = _reserved_row(readme, "Claude")
        variable_row = _settings_row(readme, "TOOLS")
        assert "`--allowedTools`" in row
        assert "`--allowed-tools`" in row
        assert "TOOLS" in row
        assert "--allowedTools" in variable_row


def test_headless_impossible_permission_modes_are_documented_bilingually():
    """The refused modes are named, and so are the ones left to the user.

    Half the rule is useless: a reader who only learns that some modes are
    refused cannot tell whether the permission flag is usable at all.
    """
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    for readme in (english, chinese):
        claude = _reserved_row(readme, "Claude")
        for mode in ("`plan`", "`manual`", "`default`"):
            assert mode in claude
        assert "`--mode plan`" in _reserved_row(readme, "Agy")
        for allowed in ("acceptEdits", "auto", "bypassPermissions", "dontAsk"):
            assert allowed in readme
        assert "--dangerously-skip-permissions" in readme


def test_agy_prompt_and_session_flags_are_documented_bilingually():
    """Every spelling that could drop the workflow prompt is named.

    Agy carries the prompt in `--print` and its parser lets a later value
    replace an earlier one, so the reserved list has to cover the aliases
    and both dash spellings, not just the canonical name.
    """
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    for readme in (english, chinese):
        row = _reserved_row(readme, "Agy")
        for flag in (
            "`-c`",
            "`--continue`",
            "`--conversation`",
            "`--log-file`",
            "`-p`",
            "`--print`",
            "`--prompt`",
            "`-i`",
            "`--prompt-interactive`",
            "`--print-timeout`",
            "`--output-format`",
            "`--json-schema`",
        ):
            assert flag in row, f"{flag} missing from the Agy reserved row"
    assert "one dash or two" in _reserved_row(english, "Agy")
    assert "單破折號與雙破折號" in _reserved_row(chinese, "Agy")


def test_active_docs_teach_only_slot_specific_argument_variables():
    documents = (
        "README.md",
        "README.zh-TW.md",
        "docs/troubleshooting.md",
        "docs/troubleshooting.zh-TW.md",
        "docs/agent-session-lifecycle.md",
        "docs/agent-session-lifecycle.zh-TW.md",
        "docs/python-port-parity.md",
    )
    for name in documents:
        text = _read(name)
        for removed in ("CLAUDE_ARGS", "CODEX_ARGS", "AGY_ARGS", "OPENCODE_ARGS"):
            assert removed not in text, f"{name} still documents {removed}"
        assert "AGENT_A_ARGS" in text
        assert "AGENT_B_ARGS" in text
        assert "IMPL_ARGS" in text


def test_reasoning_level_is_documented_bilingually():
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    claude, codex, agy, opencode = _adapter_cells(english, "Reasoning level")
    assert "--effort=low" in claude
    assert "model_reasoning_effort=low" in codex
    assert "--effort=low" in agy
    assert "--variant low" in opencode
    claude, codex, agy, opencode = _adapter_cells(chinese, "推理深度")
    assert "--effort=low" in claude
    assert "model_reasoning_effort=low" in codex
    assert "--effort=low" in agy
    assert "--variant low" in opencode
    for readme in (english, chinese):
        assert "AGENT_A_ARGS='--effort=high'" in readme
        assert "AGENT_A_ARGS='--variant low'" in readme
        assert "AGENT_B_ARGS='--effort=low'" in readme


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
    claude, codex, agy, opencode = _adapter_cells(english, "Live output")
    assert "one-line summary per tool call" in claude
    assert "one-line summary per tool call" in codex
    assert "Raw merged output" in agy
    assert "one-line summary per tool call" in opencode
    # OpenCode reports a tool call only once it is over, so "streams while
    # it works" must not be read as "a slow tool call is visible up front".
    assert "(at completion)" in opencode
    claude, codex, agy, opencode = _adapter_cells(chinese, "即時輸出")
    assert "每個工具呼叫一行摘要" in claude
    assert "每個工具呼叫一行摘要" in codex
    assert "原始合併輸出" in agy
    assert "每個工具呼叫一行摘要" in opencode
    assert "(工具結束時)" in opencode
    assert "Claude and Codex report a" in english
    assert "OpenCode reports one only once the call has finished" in english
    assert "Claude 與 Codex 在工具呼叫「開始」時就印" in chinese
    assert "OpenCode 只在呼叫「結束」時才印" in chinese
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


def test_cli_help_and_version_flags_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "aac --help" in readme
        assert "aac --version" in readme


def test_readme_feature_sections_are_documented_bilingually():
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    section_pairs = (
        ("Multi-Agent Adversarial Coding Workflow", "多重 AI 對抗式程式開發工作流"),
        ("How It Works", "流程"),
        ("Core Design (Why It Works)", "核心設計(為什麼這樣做)"),
        ("Requirements", "前置需求"),
        ("Quick Start", "快速開始"),
        ("Writing a Good Request", "需求怎麼寫"),
        ("Strong Model Plans, Cheap Model Implements", "強模型規劃、便宜模型實作"),
        ("Dual Spec Mode", "雙 spec 模式"),
        ("Importing an External Spec or Plan", "匯入外部 Spec 或 Plan"),
        ("Phased ATDD Mode", "分階段 ATDD 模式(Phased ATDD)"),
        ("Custom Agent Commands", "自訂 Agent 指令"),
        ("Configuration", "環境變數"),
        ("Resuming an Interrupted Run", "中斷後續跑"),
        ("Artifacts", "產物與目錄結構"),
        ("Agent CLI Session Behavior", "Agent CLI 差異與限制"),
        ("Protected Acceptance Tests", "受保護測試的逃生口"),
        ("Safety Notes", "安全性注意事項"),
        ("Custom Stages", "自訂 stage"),
        ("Testing This Repository", "測試"),
        ("Troubleshooting", "疑難排解"),
        ("Future Directions", "延伸方向"),
        ("Related Reading", "參考資料"),
    )

    for english_heading, chinese_heading in section_pairs:
        assert f"## {english_heading}\n" in english
        assert f"## {chinese_heading}\n" in chinese

    assert "### Manual E2E" in english
    assert "### 手動 E2E" in chinese


def test_custom_agent_commands_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        for detail in (
            "AGENT_A=gemini",
            "$AGENT_A $AGENT_A_ARGS",
            "$AGENT_B $AGENT_B_ARGS",
            "$IMPL_AGENT $IMPL_ARGS",
            "aac/.run/review.md",
            "aac/.run/verdict.json",
            "exec my-agent --session aac-worker",
            "exec my-agent --session aac-reviewer",
        ):
            assert detail in readme


def test_cross_platform_launchers_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "adversarial-ai-coding/scripts" in readme
        assert "scripts/aac" in readme
        assert "scripts/aac.cmd" in readme
        assert "aac request.md" in readme
        assert "Windows PowerShell" in readme
        assert "--locked" in readme
        assert "PYTHONHOME" in readme
        assert "PYTHONPATH" in readme
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
        assert "pt-BR" in row
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
