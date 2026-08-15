"""Presentation-layer locale for human-facing CLI surface strings.

The package itself only honours AAC_LANG (unset or unknown → English).
Shipped catalogs: zh-TW, zh-CN, ja-JP, ko-KR. scripts/aac and aac.cmd
may set AAC_LANG from the OS locale when it is unset. Run logs,
exceptions, and artifacts stay on the English template.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping

from .config import render_template
from .style import Styler

LOCALES_DIR = Path(__file__).resolve().parent / "locales"


def _fold_tag(tag: str) -> str:
    base = tag.strip().split(".", 1)[0].split("@", 1)[0]
    return base.lower().replace("_", "-")


def is_zh_tw_tag(tag: str) -> bool:
    """Traditional Chinese: zh-TW / zh_TW / zh-Hant* / zh-HK / zh_HK."""

    folded = _fold_tag(tag)
    return folded in {"zh-tw", "zh-hk"} or folded.startswith("zh-hant")


def is_zh_cn_tag(tag: str) -> bool:
    """Simplified Chinese: zh-CN / zh_CN / zh-Hans* / zh-SG."""

    folded = _fold_tag(tag)
    return folded in {"zh-cn", "zh-sg"} or folded.startswith("zh-hans")


def is_ja_tag(tag: str) -> bool:
    folded = _fold_tag(tag)
    return folded == "ja" or folded.startswith("ja-")


def is_ko_tag(tag: str) -> bool:
    folded = _fold_tag(tag)
    return folded == "ko" or folded.startswith("ko-")


def resolve_lang(env: Mapping[str, str]) -> str:
    raw = (env.get("AAC_LANG") or "").strip()
    if not raw:
        return "en"
    if is_zh_tw_tag(raw):
        return "zh-TW"
    if is_zh_cn_tag(raw):
        return "zh-CN"
    if is_ja_tag(raw):
        return "ja-JP"
    if is_ko_tag(raw):
        return "ko-KR"
    return "en"


def _catalog_path(lang: str) -> Path:
    return LOCALES_DIR / f"{lang.replace('-', '_')}.json"


@lru_cache(maxsize=8)
def _load_catalog(lang: str) -> dict[str, str]:
    if lang == "en":
        return {}
    path = _catalog_path(lang)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    }


def translate(template: str, lang: str, fields: Mapping[str, object] | None = None) -> str:
    english = render_template(template, fields)
    if lang == "en":
        return english
    translated = _load_catalog(lang).get(template)
    if translated is None:
        return english
    try:
        return render_template(translated, fields)
    except (KeyError, IndexError, ValueError):
        return english


def emit(
    sink: Callable[..., object], template: str, **fields: object
) -> object:
    """Call a 1-arg sink with English, or a template sink with fields."""

    try:
        return sink(template, **fields)
    except TypeError:
        return sink(render_template(template, fields))


def emit_exception(sink: Callable[..., object], exc: BaseException) -> object:
    template = getattr(exc, "template", None)
    fields = getattr(exc, "fields", None)
    if isinstance(template, str):
        return emit(sink, template, **(fields or {}))
    return emit(sink, str(exc))


def bind_ask(
    raw_ask: Callable[[str], str], lang: str
) -> Callable[..., str]:
    def ask(template: str, **fields: object) -> str:
        return raw_ask(translate(template, lang, fields))

    return ask


@dataclass(frozen=True)
class Presenter:
    """Translate at the console boundary; classify colour on English."""

    styler: Styler
    lang: str

    def out(self, template: str, **fields: object) -> None:
        self._write(template, fields, err=False)

    def err(self, template: str, **fields: object) -> None:
        self._write(template, fields, err=True)

    def _write(
        self, template: str, fields: Mapping[str, object], *, err: bool
    ) -> None:
        english = render_template(template, fields)
        display = translate(template, self.lang, fields)
        enabled = self.styler.err_enabled if err else self.styler.out_enabled
        text = self.styler.paint_mapped(english, display) if enabled else display
        if err:
            print(text, file=sys.stderr)
        else:
            print(text)
