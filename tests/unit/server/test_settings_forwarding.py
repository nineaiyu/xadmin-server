#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""server/settings 包「CONFIG 转发」一致性守护（表驱动化扩展）。

`server/settings/*.py` 把 CONFIG（config.yml / 环境变量 / 代码默认值的装配结果）
暴露给 django settings。手写转发有两类静默风险：

1. 左右键名不一致（如 `A = CONFIG.B`）——配置可用但语义错位，且无任何报错；
2. 转发到 CONFIG 不存在的键——`CONFIG.get` 静默返回 None，django settings 拿到空值；
3. 新增 CONFIG 键漏转发——读取方 `getattr(settings, X, 默认值)` 永远落默认值
   （SECURITY_AES_V1_DECRYPT_ENABLED 漏转发曾导致开关无法关闭，METRICS_ENABLED
   漏转发端点永远 404）。

现在 setting.py 已改为声明式转发（FORWARD_KEYS 清单 + 循环写 globals），
本测试对 AST 做静态校验实现全量覆盖：CONFIG.defaults 的每个键必须落在——

- FORWARD_KEYS（setting.py 声明清单，同名转发）；
- server/ 装配模块的 `CONFIG.<key>` 直接引用（base.py / libs.py / monitoring 等）；
- NON_FORWARDED_KEYS 豁免登记（common 注入面等有意不转发，注明消费方）。

两头都不沾 = 新键忘了接线，CI 失败。
"""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SETTINGS_DIR = REPO_ROOT / "server" / "settings"
SERVER_DIR = REPO_ROOT / "server"
SETTING_MODULE = SETTINGS_DIR / "setting.py"

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


def _declared_forward_keys() -> list[str]:
    """抽取 setting.py 声明式转发清单 FORWARD_KEYS（表驱动）。"""
    tree = ast.parse(SETTING_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "FORWARD_KEYS"
            and isinstance(node.value, ast.List)
        ):
            return [elt.value for elt in node.value.elts]
    return []


def _non_forwarded_registry() -> dict[str, str]:
    """抽取 setting.py 的 NON_FORWARDED_KEYS 豁免登记（key -> 原因）。"""
    tree = ast.parse(SETTING_MODULE.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "NON_FORWARDED_KEYS"
            and isinstance(node.value, ast.Dict)
        ):
            return {
                ast.literal_eval(k): ast.literal_eval(v)
                for k, v in zip(node.value.keys, node.value.values, strict=True)
            }
    return {}


def _config_attr_references() -> set[str]:
    """server/ 全部模块中 `CONFIG.<key>` 属性引用（装配模块直读 = 合法消费面）。"""
    refs = set()
    for path in sorted(SERVER_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "CONFIG":
                refs.add(node.attr)
    return refs


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


def test_declared_forward_keys_parse():
    keys = _declared_forward_keys()
    assert len(keys) >= 100, "FORWARD_KEYS 清单解析失败（setting.py 结构变化，请更新解析）"


def test_declared_forward_keys_unique():
    keys = _declared_forward_keys()
    duplicated = sorted({key for key in keys if keys.count(key) > 1})
    assert duplicated == [], f"FORWARD_KEYS 存在重复登记: {duplicated}"


def test_declared_forward_keys_exist_in_config_defaults():
    from server.conf import Config

    unknown = sorted(set(_declared_forward_keys()) - set(Config.defaults))
    assert unknown == [], f"FORWARD_KEYS 登记了 CONFIG 不存在的键（转发将静默取 None）: {unknown}"


def test_declared_and_non_forwarded_are_disjoint():
    forward = set(_declared_forward_keys())
    registered = set(_non_forwarded_registry())
    overlap = sorted(forward & registered)
    assert overlap == [], f"键同时登记在 FORWARD_KEYS 与 NON_FORWARDED_KEYS: {overlap}"


def test_non_forwarded_registry_reasons_meaningful():
    registry = _non_forwarded_registry()
    assert registry, "NON_FORWARDED_KEYS 豁免登记解析失败（setting.py 结构变化，请更新解析）"
    empty = sorted(key for key, reason in registry.items() if not str(reason).strip())
    assert empty == [], f"豁免键缺少消费方说明: {empty}"
    from server.conf import Config

    unknown = sorted(set(registry) - set(Config.defaults))
    assert unknown == [], f"豁免登记了 CONFIG 不存在的键（已失效，请清理）: {unknown}"


def test_all_config_keys_forwarded_or_registered():
    """全量覆盖守护（核心）：新增 CONFIG 键未接线即失败。

    CONFIG.defaults 的每个键必须落在三处之一：FORWARD_KEYS 声明清单、
    server/ 装配模块的 `CONFIG.<key>` 直接引用、NON_FORWARDED_KEYS 豁免登记。
    """
    from server.conf import Config

    forward = set(_declared_forward_keys())
    registered = set(_non_forwarded_registry())
    referenced = _config_attr_references()
    orphaned = sorted(set(Config.defaults) - forward - registered - referenced)
    assert orphaned == [], (
        f"以下 CONFIG 键未转发到 django settings、未被 server/ 装配模块消费、也未登记豁免"
        f"（新键请登记 FORWARD_KEYS；有意不转发的登记 NON_FORWARDED_KEYS 并注明消费方）: {orphaned}"
    )


def test_forward_keys_bound_at_module_import():
    """运行时校验：FORWARD_KEYS 的每个键真的经循环写入了模块 globals 并与 CONFIG 同值。

    防御「清单与循环脱钩」类回归（如循环被误删/改名后清单沦为死数据）。
    """
    import server.settings.setting as setting_module
    from server.const import CONFIG

    missing = [key for key in _declared_forward_keys() if not hasattr(setting_module, key)]
    assert missing == [], f"FORWARD_KEYS 键未绑定到模块 globals（转发循环失效）: {missing}"
    diverged = [key for key in _declared_forward_keys() if getattr(setting_module, key) != CONFIG.get(key)]
    assert diverged == [], f"模块值与 CONFIG 装配值不一致: {diverged}"


def test_metrics_keys_exported():
    """METRICS_ENABLED / METRICS_TOKEN 必须无条件导出到 settings。

    读取方 common/api/metrics.py 走 `getattr(settings, "METRICS_ENABLED", False)`：
    漏导出时端点永远 404（2026-09-16 实测踩中，与 SECURITY_AES_V1_DECRYPT_ENABLED 同类缺陷）。
    """
    from django.conf import settings

    assert hasattr(settings, "METRICS_ENABLED")
    assert hasattr(settings, "METRICS_TOKEN")


def test_security_keys_all_forwarded_to_settings():
    """conf.py 中所有 SECURITY_* 键都必须被 server/settings 转发（同名或已登记别名）。

    SECURITY_* 的读取方普遍走 `getattr(settings, "X", 默认值)` 形态：漏转发时
    django settings 没有该属性，getattr 永远落默认值，配置开关形同虚设且无任何报错
    （实测：SECURITY_AES_V1_DECRYPT_ENABLED 曾因漏转发无法关闭）。
    """
    from server.conf import Config

    forward = set(_declared_forward_keys())
    pairs_attr = {attr for _, attr, _, _ in _forwarding_pairs()}
    referenced = _config_attr_references()
    missing = sorted(
        key for key in Config.defaults if key.startswith("SECURITY_") and key not in forward | pairs_attr | referenced
    )
    assert missing == [], (
        f"以下 SECURITY_* 键未转发到 server/settings（开关将静默失效，读取方 getattr 永远落默认值）: {missing}"
    )
