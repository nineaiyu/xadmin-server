# -*- coding: utf-8 -*-
"""配置键字符串守护：个人级配置读取 helper 的 key 字面量必须登记为 SysConfig property。

helper（get_personal_config_data / get_personal_int_config / batch_user_config）的
key 是裸字符串，与 DB 行 key、个人行 key、SysConfig property 名三方一致纯靠约定；
拼错的失败模式是静默降级（个人行永远匹配不上 → 回退系统级值，无任何报错）。
本测试用 AST 扫描业务代码中的 helper 调用点，字符串不在 property 名集合内即红。
"""

import ast
from pathlib import Path

from tests.unit.common.test_config_defaults_single_source import ALL_SYSCONFIG_KEYS

HELPER_NAMES = {"get_personal_config_data", "get_personal_int_config", "batch_user_config"}
SCAN_ROOTS = ["common", "system", "message", "notifications"]
SKIP_DIRS = {"__pycache__", "migrations"}


def _call_name(func_node):
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return None


def _helper_key_literals():
    server_root = Path(__file__).resolve().parents[3]
    literals = {}
    for root in SCAN_ROOTS:
        for path in sorted((server_root / root).rglob("*.py")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node.func) not in HELPER_NAMES:
                    continue
                if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
                    continue
                if isinstance(node.args[1].value, str):
                    literals.setdefault(node.args[1].value, []).append(path.relative_to(server_root).as_posix())
    return literals


def test_helper_key_literals_are_registered_properties():
    literals = _helper_key_literals()
    assert literals, "AST 扫描应至少找到 FILE_UPLOAD_SIZE 等调用点，扫描范围失效时本测试失去意义"
    unknown = {key: files for key, files in literals.items() if key not in ALL_SYSCONFIG_KEYS}
    assert unknown == {}, f"以下 helper 调用的配置键未登记为 SysConfig property（拼错会静默失效）: {unknown}"
