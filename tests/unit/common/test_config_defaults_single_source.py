#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SysConfig 默认值单源守护（A1）。

单一来源 = ``server/conf.py``：

1. 每个 SysConfig property 的键都必须登记在 conf.py 默认值表——否则
   ``CONFIG.<KEY>`` 静默返回 None，默认值失效且无任何报错；
2. ``loadjson/systemconfig.json`` 的种子初值须与 conf.py 默认值一致
   （防「改代码默认忘了同步种子」的漂移）；
3. property 名与配置键名保持一一对应（本目录约定，供上面两条校验成立）。
"""

import json
from pathlib import Path

from common.core.config import BaseConfCache, ConfigCache, MessagePushConfCache
from server.conf import Config

# 前端站点配置整体 JSON 走 SystemConfig 但非后端 SysConfig 键，种子豁免
SEED_EXEMPT_KEYS = {"WEB_SITE_CONFIG"}


def _property_keys(cls):
    return {name for name, value in vars(cls).items() if isinstance(value, property)}


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
