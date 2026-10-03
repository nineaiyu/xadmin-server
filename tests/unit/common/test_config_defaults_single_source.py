#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SysConfig 默认值单源守护（A1）。

单一来源 = ``server/conf.py``：

1. 每个 SysConfig property 的键都必须登记在 conf.py 默认值表——否则
   ``CONFIG.<KEY>`` 静默返回 None，默认值失效且无任何报错；
2. ``loadjson/systemconfig.json`` 的种子初值须与 conf.py 默认值一致
   （防「改代码默认忘了同步种子」的漂移）；
3. property 名与配置键名保持一一对应（本目录约定，供上面两条校验成立）；
4. 三个默认值字典（base / libs / settings）键不重叠、config_example.yml
   的顶层键全部登记（防静默覆盖与示例漂移）。
"""

import json
import re
from pathlib import Path

from common.core.config import BaseConfCache, ConfigCache, MessagePushConfCache
from server.conf import Config
from server.conf.defaults import BASE_CONFIG, LIBS_CONFIG
from server.conf.settings_defaults import SETTINGS_CONFIG

# 前端站点配置整体 JSON 走 SystemConfig 但非后端 SysConfig 键，种子豁免
SEED_EXEMPT_KEYS = {"WEB_SITE_CONFIG"}


def _property_keys(cls):
    # BaseConfCache 的属性按域拆分在 conf_upload/conf_security/conf_ops 三个 mixin 上，
    # 守护口径不变：沿 MRO 收集组合全体的 property（仍覆盖 SysConfig 全部键）
    keys: set[str] = set()
    for klass in cls.__mro__:
        keys |= {name for name, value in vars(klass).items() if isinstance(value, property)}
    return keys


ALL_SYSCONFIG_KEYS = _property_keys(BaseConfCache) | _property_keys(MessagePushConfCache) | _property_keys(ConfigCache)


def test_every_property_has_conf_default():
    """property 的键必须单源登记在 conf.py 默认值表。"""
    missing = sorted(key for key in ALL_SYSCONFIG_KEYS if key not in Config.defaults)
    assert missing == [], f"以下 SysConfig 键缺少 conf.py 默认值，CONFIG.<KEY> 会返回 None: {missing}"


def test_seed_values_match_conf_defaults():
    """systemconfig.json 种子初值必须与 conf.py 默认值一致（防两处漂移）。"""
    seed_path = Path(__file__).resolve().parents[3] / "loadjson" / "systemconfig.json"
    seeds = json.loads(seed_path.read_text(encoding="utf-8"))
    drifted = []
    for row in seeds:
        fields = row["fields"]
        key, value = fields["key"], fields["value"]
        if key in SEED_EXEMPT_KEYS:
            continue
        if key not in Config.defaults:
            drifted.append(f"{key}: 种子键不在 conf.py 默认值表")
        elif value != Config.defaults[key]:
            drifted.append(f"{key}: 种子={value!r} conf={Config.defaults[key]!r}")
    assert drifted == [], f"systemconfig.json 与 conf.py 默认值漂移: {drifted}"


def test_conf_defaults_are_not_none_for_sysconfig_keys():
    """SysConfig 键的 conf.py 默认值不得为 None（None 会被误判为「未配置」）。"""
    nulled = sorted(key for key in ALL_SYSCONFIG_KEYS if Config.defaults.get(key) is None)
    assert nulled == [], f"以下 SysConfig 键的 conf.py 默认值为 None: {nulled}"


def test_graduated_switch_defaults_adr082():
    """/ 转正默认值守护：逐键断言，防无意回退。

    AI 四开关（消费面另有 is_configured/能力探测门控）、METRICS_ENABLED（端点仍有
    token 门控）、保留期卫生默认值（种子同步转值，存量部署种子行优先）。
    """
    for key in (
        "AI_ASSISTANT_ENABLED",
        "AI_NL_QUERY_ENABLED",
        "AI_ACTION_ENABLED",
        "AI_NATIVE_TOOLS_ENABLED",
        "METRICS_ENABLED",
    ):
        assert Config.defaults[key] is True, f"{key} 应保持转正默认 True"
    assert Config.defaults["CHAT_HISTORY_DAYS"] == 365
    assert Config.defaults["FILE_KEEP_DAYS"] == 180


def test_config_groups_do_not_overlap():
    """三个默认值字典（base / libs / settings）键不得重叠。

    ``Config.defaults`` 按 base → libs → settings 顺序 update，同键双写会
    静默覆盖（后写胜），排查成本高；新增开关只应登记在一处。
    """
    base = set(BASE_CONFIG) | set(LIBS_CONFIG)
    settings = set(SETTINGS_CONFIG)
    overlap = sorted(base & settings)
    assert overlap == [], f"默认值字典存在重复键（会静默覆盖）: {overlap}"


def test_example_yml_keys_are_registered():
    """config_example.yml 的顶层键必须登记在默认值表。

    未登记的键没有默认值：convert_type 按 default_value is None 原样返回，
    类型转换与内置默认值均失效（也可能是拼写错误），静默不生效。
    """
    example = Path(__file__).resolve().parents[3] / "config_example.yml"
    keys = {
        match.group(1)
        for line in example.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^([A-Z][A-Z0-9_]*):", line))
    }
    assert keys, "config_example.yml 未解析到顶层键（格式变化？）"
    registered = set(BASE_CONFIG) | set(LIBS_CONFIG) | set(SETTINGS_CONFIG)
    missing = sorted(keys - registered)
    assert missing == [], f"示例配置键未登记默认值: {missing}"
