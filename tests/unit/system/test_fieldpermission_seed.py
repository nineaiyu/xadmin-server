# -*- coding: utf-8 -*-
"""字段权限种子（loadjson/fieldpermission.json）完整性守护。

字段权限种子是 load_init_json 的导入输入，条目引用的角色 / 权限点 / 字段必须
同时存在于对应种子文件——否则新环境 loaddata 会撞外键（或静默丢配置）。
运行时模块自建的权限点（演示示例 6eed 段、插件等）不进 loadjson，其字段白名单
由模块自身的种子命令维护（如 seed_demo_admin 的补齐入口）。
"""

import json
from pathlib import Path
from typing import Any

from django.conf import settings

SEED_DIR = Path(settings.PROJECT_DIR) / "loadjson"

#: 运行时模块自建权限点的 pk 前缀（demo_seed 家族固定段，不进 loadjson）
RUNTIME_MODULE_PREFIXES = ("6eed000c",)


def _load(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


def test_field_permission_seed_references_resolve() -> None:
    """角色 / 权限点 / 字段三侧引用都落在各自种子文件内。"""
    entries = _load("fieldpermission.json")
    assert entries, "字段权限种子不应为空"

    role_pks = {row["pk"] for row in _load("userrole.json")}
    menu_pks = {row["pk"] for row in _load("menu.json")}
    field_pks = {row["pk"] for row in _load("modellabelfield.json")}

    for entry in entries:
        payload = entry["fields"]
        assert payload["role"] in role_pks, f"角色不在种子内: {entry['pk']}"
        assert payload["menu"] in menu_pks, f"权限点不在种子内: {entry['pk']}"
        assert set(payload.get("field") or []) <= field_pks, f"字段不在种子内: {entry['pk']}"
        assert payload.get("field"), f"空白名单条目没有导入意义: {entry['pk']}"


def test_field_permission_seed_excludes_runtime_modules() -> None:
    """运行时模块自建的权限点不进种子（由模块自身的种子命令维护）。"""
    entries = _load("fieldpermission.json")
    leaked = [entry for entry in entries if str(entry["fields"]["menu"]).startswith(RUNTIME_MODULE_PREFIXES)]
    assert leaked == [], f"运行时模块权限点混入字段权限种子: {[e['pk'] for e in leaked]}"


def test_seeded_roles_have_field_permissions() -> None:
    """种子内角色都应至少有一条字段白名单（新角色漏配会静默空输出）。"""
    entries = _load("fieldpermission.json")
    covered = {entry["fields"]["role"] for entry in entries}
    seeded_roles = {row["pk"]: row["fields"].get("name") for row in _load("userrole.json")}
    missing = {name for pk, name in seeded_roles.items() if pk not in covered}
    assert missing == set(), f"种子角色缺字段白名单: {missing}"
