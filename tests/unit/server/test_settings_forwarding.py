#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""server/settings 包「CONFIG 转发」一致性守护（A1/A6）。

`server/settings/*.py` 用 `X = CONFIG.X` 把 conf.py 的键暴露给 django settings。
手写转发有两类静默风险：

1. 左右键名不一致（如 `A = CONFIG.B`）——配置可用但语义错位，且无任何报错；
2. 转发到 conf.py 不存在的键——`CONFIG.get` 静默返回 None，django settings 拿到空值。

本测试对 AST 做静态校验：同名自转发为常态，跨名映射必须在豁免清单中显式登记
（Django/Celery 等第三方框架的固定键名，如 SECURE_SSL_REDIRECT）。
"""

import ast
from pathlib import Path

SETTINGS_DIR = Path(__file__).resolve().parents[3] / "server" / "settings"

# 有意为之的跨名映射：(django settings 键, conf.py 键)；新增映射必须在此登记
ALLOWED_ALIASES = {
    ("SECURE_SSL_REDIRECT", "SECURITY_HTTPS_REDIRECT_ENABLED"),  # Django 固定键名
    ("CELERY_TIMEZONE", "TIME_ZONE"),  # Celery 固定键名
}


def _forwarding_pairs():
    """抽取所有 `NAME = CONFIG.NAME` 形态的赋值：(左值, 右值, 文件, 行号)。"""
    pairs = []
    for path in sorted(SETTINGS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            target, value = node.targets[0], node.value
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Attribute):
                continue
            if isinstance(value.value, ast.Name) and value.value.id == "CONFIG":
                pairs.append((target.id, value.attr, path.name, node.lineno))
    return pairs


def test_settings_forwarding_is_same_name_or_registered_alias():
    pairs = _forwarding_pairs()
    assert pairs, "server/settings 未解析到任何 CONFIG 转发（解析逻辑或文件结构已变化，请更新本测试）"
    mismatched = [
        f"{file}:{lineno}: {name} = CONFIG.{attr}"
        for name, attr, file, lineno in pairs
        if name != attr and (name, attr) not in ALLOWED_ALIASES
    ]
    assert mismatched == [], f"未登记的跨名转发（右值必须同名，或加入 ALLOWED_ALIASES）: {mismatched}"


def test_settings_forwarding_reads_existing_conf_keys():
    from server.conf import Config

    missing = [
        f"{file}:{lineno}: CONFIG.{attr}"
        for _, attr, file, lineno in _forwarding_pairs()
        if attr not in Config.defaults
    ]
    assert missing == [], f"以下转发读取的键在 conf.py 默认值表中不存在（CONFIG 会静默返回 None）: {missing}"
