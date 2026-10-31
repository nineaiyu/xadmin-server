# -*- coding: utf-8 -*-
"""配置值渲染：只解包「整值恰好是数字括号占位」的形态，不能篡改业务字符串。"""

import json

import pytest

from common.core.config.base import render_config_value

pytestmark = pytest.mark.django_db


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


def test_mutual_reference_does_not_recurse():
    """A→B→A 互引配置只渲染一层，不因重建渲染上下文而无限递归。"""
    from common.core.config import SysConfig
    from system.models import SystemConfig

    SystemConfig.objects.create(key="MUTUAL_A", value="{{ MUTUAL_B }}", is_active=True)
    SystemConfig.objects.create(key="MUTUAL_B", value="{{ MUTUAL_A }}", is_active=True)
    value = SysConfig.get_value("MUTUAL_A")
    assert "MUTUAL_A" in str(value)


def test_unknown_config_attribute_logs_warning(caplog):
    from common.core.config import SysConfig

    with caplog.at_level("WARNING"):
        assert getattr(SysConfig, "TYPO_SECURITY_SWITCH", None) == {}
    assert "not registered as SysConfig property" in caplog.text
