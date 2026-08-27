"""Mechanism tests for presentation-layer locale. No completeness gate."""

from pathlib import Path

from adversarial_ai_coding.config import SettingsError, render_template
from adversarial_ai_coding.i18n import (
    Presenter,
    emit,
    emit_exception,
    is_ja_tag,
    is_ko_tag,
    is_pt_tag,
    is_zh_cn_tag,
    is_zh_tw_tag,
    resolve_lang,
    translate,
)
from adversarial_ai_coding.style import Styler, classify


def test_resolve_lang_defaults_to_english():
    assert resolve_lang({}) == "en"
    assert resolve_lang({"AAC_LANG": ""}) == "en"
    assert resolve_lang({"AAC_LANG": "en"}) == "en"
    assert resolve_lang({"AAC_LANG": "fr"}) == "en"
    assert resolve_lang({"LANG": "zh_TW.UTF-8"}) == "en"


def test_resolve_lang_accepts_traditional_chinese_aliases():
    for raw in (
        "zh-TW",
        "zh_TW",
        "zh-tw",
        "zh-Hant",
        "zh-Hant-TW",
        "zh-HK",
        "zh_HK",
    ):
        assert resolve_lang({"AAC_LANG": raw}) == "zh-TW", raw


def test_resolve_lang_accepts_simplified_chinese_aliases():
    for raw in ("zh-CN", "zh_CN", "zh-cn", "zh-Hans", "zh-Hans-CN", "zh-SG"):
        assert resolve_lang({"AAC_LANG": raw}) == "zh-CN", raw


def test_resolve_lang_accepts_japanese_and_korean_aliases():
    for raw in ("ja", "ja-JP", "ja_JP", "ja-jp"):
        assert resolve_lang({"AAC_LANG": raw}) == "ja-JP", raw
    for raw in ("ko", "ko-KR", "ko_KR", "ko-kr"):
        assert resolve_lang({"AAC_LANG": raw}) == "ko-KR", raw
    for raw in ("pt", "pt-BR", "pt_BR", "pt-br", "pt-PT"):
        assert resolve_lang({"AAC_LANG": raw}) == "pt-BR", raw


def test_is_zh_tw_tag_strips_codeset_and_rejects_simplified():
    assert is_zh_tw_tag("zh_TW.UTF-8")
    assert is_zh_tw_tag("zh-Hant-TW@cns")
    assert not is_zh_tw_tag("zh_CN.UTF-8")
    assert not is_zh_tw_tag("zh-Hans-CN")
    assert not is_zh_tw_tag("en_US")
    assert is_zh_cn_tag("zh_CN.UTF-8")
    assert is_zh_cn_tag("zh-Hans-CN")
    assert not is_zh_cn_tag("zh_TW.UTF-8")
    assert is_ja_tag("ja_JP.UTF-8")
    assert is_ko_tag("ko_KR.UTF-8")
    assert is_pt_tag("pt_BR.UTF-8")
    assert is_pt_tag("pt-PT")
    assert not is_ja_tag("en_US")


def test_translate_falls_back_when_key_is_missing():
    assert (
        translate("no such surface string {x}", "zh-TW", {"x": "1"})
        == "no such surface string 1"
    )


def test_translate_uses_catalog_and_keeps_fields():
    text = translate(
        "!! Workflow interrupted (exit={rc}).", "zh-TW", {"rc": 130}
    )
    assert "130" in text
    assert "中斷" in text
    assert "!! " in text


def test_progress_lines_keep_industry_terms():
    worker = translate(
        ">>> Worker({name}) is running...", "zh-TW", {"name": "claude"}
    )
    assert "Worker" in worker
    assert "claude" in worker
    assert "執行中" in worker
    reviewer = translate(
        ">>> Reviewer({name}) is reviewing...", "zh-TW", {"name": "codex"}
    )
    assert "Reviewer" in reviewer
    assert "審查中" in reviewer
    tree = translate(
        "Created worktree:{workspace} "
        "(branch {branch}; "
        "remove later with git worktree remove)",
        "zh-TW",
        {"workspace": "repo-aac-1", "branch": "aac/1"},
    )
    assert "worktree" in tree
    assert "git worktree remove" in tree
    assert "已建立" in tree


def test_english_is_identity():
    assert (
        translate("!! Workflow interrupted (exit={rc}).", "en", {"rc": 1})
        == "!! Workflow interrupted (exit=1)."
    )


def test_bad_translation_placeholders_fall_back(monkeypatch):
    monkeypatch.setattr(
        "adversarial_ai_coding.i18n._load_catalog",
        lambda lang: {"Hello {name}": "你好 {missing}"},
    )
    assert translate("Hello {name}", "zh-TW", {"name": "Ada"}) == "Hello Ada"


def test_emit_accepts_one_arg_sinks():
    seen = []
    emit(seen.append, "hello {name}", name="Ada")
    assert seen == ["hello Ada"]


REMOVED_ADAPTER_ARGS_TEMPLATE = (
    "Removed adapter-wide argument variable(s): {names}. "
    "Use AGENT_A_ARGS, AGENT_B_ARGS, or IMPL_ARGS instead."
)


def test_removed_adapter_args_error_is_translated_in_every_locale():
    fields = {"names": "CLAUDE_ARGS, CODEX_ARGS"}
    english = render_template(REMOVED_ADAPTER_ARGS_TEMPLATE, fields)
    for lang in ("zh-TW", "zh-CN", "ja-JP", "ko-KR", "pt-BR"):
        text = translate(REMOVED_ADAPTER_ARGS_TEMPLATE, lang, fields)
        assert "CLAUDE_ARGS" in text
        assert "CODEX_ARGS" in text
        assert "AGENT_A_ARGS" in text
        assert "AGENT_B_ARGS" in text
        assert "IMPL_ARGS" in text
        assert text != english


def test_emit_exception_uses_template_fields():
    seen = []
    emit_exception(
        seen.append,
        SettingsError("{name} must be an integer, got: {raw}", name="N", raw="x"),
    )
    assert seen == ["N must be an integer, got: x"]


def test_presenter_translates_stderr_and_classifies_on_english(capsys):
    styler = Styler.from_env(
        {"COLOR": "always"},
        stdout_isatty=False,
        stderr_isatty=False,
        enable_vt=lambda _stream: True,
    )
    presenter = Presenter(styler, "zh-TW")
    presenter.err("!! Workflow interrupted (exit={rc}).", rc=130)
    err = capsys.readouterr().err
    assert "\x1b[1;91m" in err
    assert "中斷" in err
    assert classify("!! Workflow interrupted (exit=130).") == "error"


def test_render_template_without_fields_is_identity():
    assert render_template("keep {braces}") == "keep {braces}"


def test_catalog_file_is_json_object():
    root = (
        Path(__file__).parents[1] / "src" / "adversarial_ai_coding" / "locales"
    )
    for name in (
        "zh_TW.json",
        "zh_CN.json",
        "ja_JP.json",
        "ko_KR.json",
        "pt_BR.json",
    ):
        assert (root / name).is_file()


def test_new_locales_translate_progress_and_keep_jargon():
    cases = {
        "zh-CN": ("执行中", "已创建"),
        "ja-JP": ("実行中", "作成しました"),
        "ko-KR": ("실행 중", "만들었습니다"),
        "pt-BR": ("em execução", "worktree criado"),
    }
    for lang, (running, created) in cases.items():
        worker = translate(
            ">>> Worker({name}) is running...", lang, {"name": "claude"}
        )
        assert "Worker" in worker
        assert running in worker
        tree = translate(
            "Created worktree:{workspace} "
            "(branch {branch}; "
            "remove later with git worktree remove)",
            lang,
            {"workspace": "repo-aac-1", "branch": "aac/1"},
        )
        assert "worktree" in tree
        assert "git worktree remove" in tree
        assert created in tree
        interrupted = translate(
            "!! Workflow interrupted (exit={rc}).", lang, {"rc": 130}
        )
        assert "130" in interrupted
        assert interrupted != "!! Workflow interrupted (exit=130)."
