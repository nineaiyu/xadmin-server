# -*- coding: utf-8 -*-
"""点选题字符集：简体 / 繁体 / mixed 整题脚本选择与字体存在性。"""

from pathlib import Path

from captcha.backends.wordclick import (
    TRADITIONAL_CHARS,
    WordClickBackend,
    _font_path,
    click_charset,
    selected_charset,
)


def test_simplified_default(settings):
    settings.CAPTCHA_CLICK_CHARSET = "simplified"
    assert selected_charset() == "simplified"
    assert len(click_charset("simplified")) > 3000


def test_traditional_charset_and_font(settings):
    settings.CAPTCHA_CLICK_CHARSET = "traditional"
    assert selected_charset() == "traditional"
    charset = click_charset("traditional")
    assert len(charset) > 3000
    assert any(char in charset for char in "臺灣體國學門東車馬長風雲龍鳳")
    assert Path(_font_path("traditional")).is_file()
    assert len(TRADITIONAL_CHARS) > 3000


def test_mixed_is_whole_challenge_script(settings):
    settings.CAPTCHA_CLICK_CHARSET = "mixed"
    scripts = {selected_charset() for _ in range(40)}
    assert scripts <= {"simplified", "traditional"}


def test_generate_traditional_challenge(settings):
    settings.CAPTCHA_CLICK_CHARSET = "traditional"
    settings.CAPTCHA_SLIDE_SIZE = (320, 180)
    challenge = WordClickBackend().generate()
    assert challenge.type == "click"
    assert challenge.payload["background"].startswith("data:image/png;base64,")
    assert len(challenge.secret["points"]) == 3
