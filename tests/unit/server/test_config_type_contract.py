# -*- coding: utf-8 -*-
"""系统配置的类型契约守护（ADR-072，observability 评估出口收口）。

背景：`convert_type` 对**不在 defaults 中的键**原样返回字符串（弱类型）；若
`SysConfig` 的 property 未做显式类型转换，坏值会以 str 形态静默流入消费点
（第十轮演练登记的「弱类型消费点」评估出口）。

本测试以「默认值类型」为契约逐项断言：每个系统级 property 的运行时返回值类型
必须与默认值一致（新增 property 自动纳入清单，无需登记）；并用注入式负向用例
证明守护本身能抓到漂移。默认值单源 = `CONFIG`（config.yml / 环境变量 / 代码默认）。
"""

import pytest

from common.core.config import SysConfig
from server.const import CONFIG

pytestmark = pytest.mark.django_db


def _system_properties() -> list:
    cls = type(SysConfig)
    return sorted(name for name in dir(cls) if isinstance(getattr(cls, name, None), property))


def _detect_drift() -> list:
    drift = []
    for name in _system_properties():
        default = CONFIG.get(name)
        if default is None:
            continue
        value = getattr(SysConfig, name)
        if value is None:
            continue
        if type(value) is not type(default):
            drift.append(f"{name}: default {type(default).__name__} -> value {type(value).__name__}")
    return drift


def test_property_pool_is_discovered():
    """防回归：property 枚举不能为空（避免断言空跑）。"""
    assert len(_system_properties()) >= 50


def test_property_type_matches_default():
    drift = _detect_drift()
    assert not drift, "系统配置类型漂移（弱类型消费点）：\n" + "\n".join(drift)


def test_contract_check_detects_drift(monkeypatch):
    """注入式负向验证：property 返回值类型与默认值不一致时必须被检出。"""
    target = "FILE_UPLOAD_SIZE"
    monkeypatch.setattr(type(SysConfig), target, property(lambda self: "drifted"), raising=False)
    drift = _detect_drift()
    assert any(item.startswith(target) for item in drift), "守护未检出注入的类型漂移"
