"""Mechanism tests for presentation-layer locale. No completeness gate."""

from pathlib import Path

from adversarial_ai_coding.config import SettingsError, render_template
from adversarial_ai_coding.i18n import (
    Presenter,
    emit,
    emit_exception,
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
    assert resolve_lang({"AAC_LANG": "zh-CN"}) == "en"
    assert resolve_lang({"AAC_LANG": "zh-Hans"}) == "en"
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


def test_is_zh_tw_tag_strips_codeset_and_rejects_simplified():
    assert is_zh_tw_tag("zh_TW.UTF-8")
    assert is_zh_tw_tag("zh-Hant-TW@cns")
    assert not is_zh_tw_tag("zh_CN.UTF-8")
    assert not is_zh_tw_tag("zh-Hans-CN")
    assert not is_zh_tw_tag("en_US")


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
    path = (
        Path(__file__).parents[1]
        / "src"
        / "adversarial_ai_coding"
        / "locales"
        / "zh_TW.json"
    )
    assert path.is_file()
