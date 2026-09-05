"""Tests that the docs keep pace with the code.

What is worth asserting here, and what only rigidifies the prose:

1. A behavior that already has a behavioral test elsewhere needs no
   sentence pinned here - assert that the doc names the knob, and let
   the other test own the behavior.
2. A fact the code owns (a variable, flag, path, filename, JSON key, or
   a string the CLI prints) is asserted as that name, never as the
   sentence wrapped around it. Read it from the code where you can.
3. A claim with no code counterpart - a guarantee, a negation, a timing
   rule - is worth locking, but as the shortest keyword pair that still
   fails when the claim disappears, one per language.
4. Multi-word English phrases carrying function words ("Claude and Codex
   report a") are the brittle kind: a comma or an "and" rewritten breaks
   the build while locking nothing rule 2 or 3 has not already locked.

Structure beats prose throughout: the helpers below read one settings
row, one adapter cell, or one section, so an assertion cannot be
satisfied - or broken - by unrelated text elsewhere in the file.
"""

import ast
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


def _ask_prompts() -> list[str]:
    """Every prompt the workflow puts to a human, read from the source.

    A prompt quoted in the README is quoted so a reader recognizes it on
    their terminal, which only holds while the two say the same thing.
    Reading the literal out of the call that asks it is what makes a
    reworded prompt fail here instead of going unnoticed.
    """
    prompts: list[str] = []
    for name in ("workflow.py", "dual_spec.py"):
        source = (ROOT / "src" / "adversarial_ai_coding" / name).read_text(
            encoding="utf-8"
        )
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call) or len(node.args) < 2:
                continue
            if getattr(node.func, "id", None) != "emit":
                continue
            sink, prompt = node.args[0], node.args[1]
            if not (isinstance(sink, ast.Attribute) and sink.attr == "ask"):
                continue
            if isinstance(prompt, ast.Constant) and isinstance(prompt.value, str):
                prompts.append(prompt.value)
    assert prompts, "no emit(ctx.ask, ...) prompts found in the workflow source"
    return prompts


def _section(text: str, heading: str) -> str:
    """One `## ` section, so a claim is asserted where it has to appear.

    A knob named anywhere in the file cannot stand in for a safety note
    that has to be in the safety section.
    """
    start = text.index(f"## {heading}\n")
    rest = text[start + len(heading) + 4 :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


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

    # That TOOLS replaces the default rather than extending it is already
    # covered above: both guides have to print DEFAULT_TOOLS in full, which
    # is only readable as a replacement value, and the worked example's own
    # rule is asserted with it.


def test_permission_prompt_adapter_table_is_documented_bilingually():
    headers = {
        "docs/troubleshooting.md": (
            "| Adapter | Arguments and settings added by AAC | Effective behavior |"
        ),
        "docs/troubleshooting.zh-TW.md": (
            "| Adapter | AAC 加入的參數與設定 | 實際行為 |"
        ),
    }

    for guide_name, header in headers.items():
        guide = _read(guide_name)
        assert header in guide
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
    # What detection actually does is proved in test_ratelimit_parsing.py
    # (envelope status, a 429 with no wording, the reported reset epoch).
    # Left here: the wait table both guides publish, and the fact that a
    # status is what makes a 429 detectable, because OpenCode relays each
    # provider's own wording.
    assert "| Reported reset epoch | Claude |" in english
    assert "| 回報的 reset epoch | Claude |" in chinese
    assert "HTTP status" in english
    assert "HTTP status" in chinese


def test_opencode_permission_and_reserved_args_are_documented_bilingually():
    """`--auto` is a safety note, not only a table cell.

    It approves every permission the user has not explicitly denied, which
    is the same trust level the Agy note already warns about.
    """
    english = _read("README.md")
    chinese = _read("README.zh-TW.md")
    assert "--auto" in _section(english, "Safety Notes")
    assert "--auto" in _section(chinese, "安全性注意事項")
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


def test_impl_args_dual_spec_timing_is_documented_bilingually():
    """Both halves of the rule, or a reader learns the wrong one.

    Knowing only that some checks wait suggests IMPL_ARGS is unchecked
    until stage five; knowing only that some fail at startup suggests a
    candidate's rules apply to a run that will never use it.
    """
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "DUAL_SPEC=1" in readme
        assert "IMPL_ARGS" in readme
    # Read the section the rule lives in: a bare "waits" anywhere in the
    # file would let half the rule disappear unnoticed.
    english = _section(_read("README.md"), "Agent CLI Session Behavior")
    chinese = _section(_read("README.zh-TW.md"), "Agent CLI 差異與限制")
    assert "waits" in english and "refused at startup" in english
    assert "等到選定" in chinese and "啟動時就拒絕" in chinese


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
    assert "never" in _clause_about(english, "run log")
    assert "永遠不" in _clause_about(chinese, "run log")
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
    # When each adapter prints is locked in the cells above, and what it
    # prints is behavior owned by test_agent_call.py.
    for readme in (english, chinese):
        assert "powershell -Command" in readme
        assert "bash -c" in readme


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
    # Session reuse and discard is behavior owned by test_session_resume.py.
    # Left here: the guides still have to tie reviewer calls to a fresh
    # session, which "fresh" alone cannot show - the word appears dozens of
    # times about other slots.
    for guide_name, word in (
        ("docs/agent-session-lifecycle.md", "fresh"),
        ("docs/agent-session-lifecycle.zh-TW.md", "全新"),
    ):
        lines = _read(guide_name).splitlines()
        assert any("reviewer" in line and word in line for line in lines)


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


def test_detected_gate_commands_are_documented_bilingually():
    """Every command detection can pick has to appear in the settings row.

    A gate the workflow runs on its own, that the docs never name, reads
    as the workflow running something unaccounted for.
    """
    for name in ("README.md", "README.zh-TW.md"):
        row = _settings_row(_read(name), "GATE_CMD")
        for command in (
            "go test ./...",
            "npm test",
            "cargo test",
            "uv run pytest",
            "poetry run pytest",
            "python -m pytest",
        ):
            assert command in row, f"{name}: GATE_CMD row must name {command}"
        assert ".venv" in row


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
    # The offer is quoted in both READMEs, so take the wording from the
    # call that asks it rather than repeating the literal here.
    offers = [prompt for prompt in _ask_prompts() if "Phased ATDD" in prompt]
    assert len(offers) == 1, offers
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "phased-suggestion.json" in readme
        assert offers[0] in readme
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
    # Which combinations arm the suggestion is proved by
    # test_phased_suggestion.py::test_suggestion_armed_matrix. Left here:
    # both READMEs have to name the combination and say it offers nothing.
    required_details = {
        "README.md": ("IMPORT_SPEC` + `IMPORT_REVIEW=0", "no phased suggestion"),
        "README.zh-TW.md": (
            "IMPORT_SPEC` + `IMPORT_REVIEW=0",
            "不會產生或提供",
        ),
    }
    for name, details in required_details.items():
        readme = _read(name)
        for detail in details:
            assert detail in readme


def test_the_run_manifest_is_documented_as_workflow_owned_bilingually():
    """The contract is what a reviewer is told to check.

    An artifact that appears in aac/docs/ without being described there
    reads as spec content nobody accounted for, which is exactly how a
    reviewer ends up reporting it as a defect and burning a round.
    """
    for name in ("docs/artifact-contract.md", "docs/artifact-contract.zh-TW.md"):
        contract = _read(name)
        assert "aac/docs/<RUN_ID>/run.json" in contract
        assert "runindex.py" in contract
    english = _read("docs/artifact-contract.md")
    assert "No agent writes it." in english
    assert "Write-once." in english
    assert "Never control flow." in english
    chinese = _read("docs/artifact-contract.zh-TW.md")
    assert "沒有任何 agent 會寫它" in chinese
    assert "只寫一次" in chinese
    assert "絕不是控制流" in chinese


def test_agents_template_keeps_the_manifest_off_limits():
    """The contract doc is not always in an agent's context; this is.

    The manifest sits in the same directory an agent is told to write the
    spec and plan into, so without a rule the honest failure is tidy-up:
    an agent deletes the file it did not create.
    """
    template = _read("resources/AGENTS.template.md")
    assert "`run.json` in the spec directory is written by the workflow" in template
    assert "Never create, edit, or delete it" in template


def test_list_runs_is_documented_in_both_readmes():
    for name in ("README.md", "README.zh-TW.md"):
        readme = _read(name)
        assert "aac list-runs" in readme
        # The layout tree decides what a reader expects to be committed.
        assert "run.json" in readme
        # The default view truncates, so the way to see everything has to be
        # documented next to it, not left to be discovered from --help.
        assert "aac list-runs --full" in readme
        assert "aac list-runs --spec-title" in readme


def test_spec_title_flag_is_documented_as_opt_in_bilingually():
    """The heading it reads is a convention, and the docs must not imply more.

    C4 lists what spec.md has to contain and a title is not on the list, so
    a reader who takes --spec-title for a guarantee has been misled by us.
    """
    # The claim is about the prompts, so check the prompts. A spec-writing
    # prompt that starts asking for a title is exactly when the README
    # sentence turns into a false promise.
    for name in (
        "write-spec.md",
        "dual-spec-write-candidate.md",
        "dual-spec-merge-final.md",
    ):
        prompt = _read(f"resources/prompts/{name}").lower()
        assert "title" not in prompt, f"{name} now asks for a title"
        assert "heading" not in prompt, f"{name} now asks for a heading"
    # Both files wrap at a fixed width, so match on flowed text.
    assert "no prompt" in " ".join(_read("README.md").split())
    assert "沒有任何 prompt" in " ".join(_read("README.zh-TW.md").split())
    for name in ("docs/artifact-contract.md", "docs/artifact-contract.zh-TW.md"):
        contract = _read(name)
        heading = [line for line in contract.splitlines() if line.startswith("### C4")]
        assert len(heading) == 1
        assert "spec.md" in heading[0]
