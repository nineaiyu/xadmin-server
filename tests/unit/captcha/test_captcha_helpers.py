# -*- coding: utf-8 -*-
"""captcha 辅助函数单元测试（挑战生成 / 噪点函数 / 图片构造 / URL 反解）。"""

import random

import pytest
from django.test import override_settings
from PIL import Image, ImageDraw

from captcha.helpers import (
    _callable_from_string,
    captcha_audio_url,
    captcha_image_url,
    filter_functions,
    get_challenge,
    get_format_color,
    makeimg,
    math_challenge,
    noise_dots,
    noise_functions,
    noise_null,
    post_smooth,
    random_char_challenge,
    unicode_challenge,
)

# challenge 生成类测试需要读写 settings 中的验证码配置，但触不到数据库，
# 不过 math_challenge 的一致性校验循环涉及随机种子，保持纯函数即可
pytestmark = pytest.mark.django_db


class TestCallableFromString:
    def test_callable_passthrough(self):
        """传入可调用对象时应原样返回（不走字符串导入分支）。"""
        assert _callable_from_string(math_challenge) is math_challenge

    def test_dotted_path_import(self):
        func = _callable_from_string("captcha.helpers.math_challenge")
        assert callable(func)
        assert func.__name__ == "math_challenge"

    def test_get_challenge_default(self):
        """不带参数时按 settings.CAPTCHA_CHALLENGE_FUNCT 解析。"""
        assert callable(get_challenge())


class TestNoiseFilterFunctions:
    @override_settings(CAPTCHA_NOISE_FUNCTIONS=["captcha.helpers.noise_dots"])
    def test_noise_functions_enabled(self):
        funcs = list(noise_functions())
        assert funcs == [noise_dots]

    @override_settings(CAPTCHA_NOISE_FUNCTIONS=[])
    def test_noise_functions_empty(self):
        assert list(noise_functions()) == []

    @override_settings(CAPTCHA_FILTER_FUNCTIONS=["captcha.helpers.post_smooth"])
    def test_filter_functions_enabled(self):
        funcs = list(filter_functions())
        assert funcs == [post_smooth]

    @override_settings(CAPTCHA_FILTER_FUNCTIONS=[])
    def test_filter_functions_empty(self):
        assert list(filter_functions()) == []


class TestMathChallenge:
    @override_settings(CAPTCHA_MATH_CHALLENGE_OPERATOR="×")
    def test_result_is_consistent(self):
        for _ in range(20):
            challenge, answer = math_challenge()
            assert challenge.endswith("=")
            expression = challenge.rstrip("=").replace("×", "*")
            assert str(eval(expression)) == answer

    def test_subtraction_operand_swap(self):
        """被减数小于减数时应交换操作数，保证结果非负。"""
        original_randint, original_choice = random.randint, random.choice
        try:
            operands = iter([1, 5])
            random.randint = lambda a, b: next(operands)
            random.choice = lambda seq: "-"
            challenge, answer = math_challenge()
        finally:
            random.randint, random.choice = original_randint, original_choice
        assert challenge == "5-1="
        assert answer == "4"

    @override_settings(CAPTCHA_MATH_CHALLENGE_OPERATOR="×")
    def test_operator_replaced_by_setting(self):
        """乘号应按 CAPTCHA_MATH_CHALLENGE_OPERATOR 配置替换显示。"""
        original_randint, original_choice = random.randint, random.choice
        try:
            random.randint = lambda a, b: 3
            random.choice = lambda seq: "*"
            challenge, answer = math_challenge()
        finally:
            random.randint, random.choice = original_randint, original_choice
        assert challenge == "3×3="
        assert answer == "9"


class TestCharChallenges:
    @override_settings(CAPTCHA_LENGTH=5)
    def test_random_char_challenge(self):
        upper, lower = random_char_challenge()
        assert len(upper) == len(lower) == 5
        assert upper == lower.upper()
        assert lower.islower()

    @override_settings(CAPTCHA_LENGTH=3)
    def test_unicode_challenge(self):
        upper, lower = unicode_challenge()
        assert len(upper) == len(lower) == 3
        assert upper == lower.upper()
        assert all(char in "äàáëéèïíîöóòüúù" for char in lower)


class TestFormatColor:
    def test_rgba_with_fraction_alpha(self):
        """alpha 处于 0~1 时应放大到 0~255 区间。"""
        assert get_format_color("rgba(1,2,3,0.5)") == (1, 2, 3, 127)

    def test_rgba_with_byte_alpha(self):
        """alpha 已经是字节值（>1）时保持不变。"""
        assert get_format_color("rgba(1,2,3,128)") == (1, 2, 3, 128)

    def test_non_rgba_passthrough(self):
        assert get_format_color("#ffffff") == "#ffffff"


class TestMakeimg:
    def test_transparent_rgba(self):
        image = makeimg((10, 5), "transparent")
        assert image.mode == "RGBA"
        assert image.size == (10, 5)

    def test_rgba_color(self):
        image = makeimg((10, 5), "rgba(255,0,0,0.5)")
        assert image.mode == "RGBA"

    def test_plain_rgb(self):
        image = makeimg((10, 5), "#ffffff")
        assert image.mode == "RGB"


class TestNoiseHelpers:
    def test_noise_null(self):
        image = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(image)
        assert noise_null(draw, image) is draw

    def test_noise_dots_returns_draw(self):
        image = Image.new("RGB", (10, 10))
        draw = ImageDraw.Draw(image)
        assert noise_dots(draw, image) is draw

    def test_post_smooth_returns_image(self):
        image = Image.new("RGB", (10, 10))
        assert post_smooth(image).size == (10, 10)


class TestCaptchaUrls:
    def test_image_url(self):
        assert captcha_image_url("abc") == "/api/system/captcha/image/abc/"

    def test_audio_url(self):
        assert captcha_audio_url("abc") == "/api/system/captcha/audio/abc.wav"
