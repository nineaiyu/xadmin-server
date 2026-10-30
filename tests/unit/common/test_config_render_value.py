# -*- coding: utf-8 -*-
"""配置值渲染：只解包「整值恰好是数字括号占位」的形态，不能篡改业务字符串。"""

import json

from common.core.config.base import render_config_value


def test_business_parentheses_preserved():
    assert render_config_value(json.dumps("(默认)"), {}, None) == "(默认)"
    assert render_config_value(json.dumps("(内网)"), {}, None) == "(内网)"
    assert render_config_value(json.dumps("a(b)c"), {}, None) == "a(b)c"
    assert render_config_value(json.dumps("前缀(示例)后缀"), {}, None) == "前缀(示例)后缀"


def test_numeric_parentheses_unwrapped():
    assert render_config_value('"(123)"', {}, None) == 123
    assert render_config_value('"(-5)"', {}, None) == -5
    assert render_config_value('"(1.5)"', {}, None) == 1.5


def test_non_numeric_parentheses_not_unwrapped_as_number():
    assert render_config_value('"(abc)"', {}, None) == "(abc)"
