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

from system.services.permission_sync import RUNTIME_PERMISSION_PREFIXES
from system.services.seeding import read_model_rows

SEED_DIR = Path(settings.PROJECT_DIR) / "loadjson"

#: 运行时模块自建权限点的 pk 前缀（demo_seed 家族固定段，不进 loadjson）；
#: 与扫描/回写内核同源（permission_sync.constants.RUNTIME_PERMISSION_PREFIXES）
RUNTIME_MODULE_PREFIXES = RUNTIME_PERMISSION_PREFIXES


def _load(name: str) -> Any:
    return json.loads((SEED_DIR / name).read_text(encoding="utf-8"))


def test_field_permission_seed_references_resolve() -> None:
    """角色 / 权限点 / 字段三侧引用都落在各自种子文件内。"""
    entries = read_model_rows("system.fieldpermission")
    assert entries, "字段权限种子不应为空"

    role_pks = {row["pk"] for row in _load("userrole.json")}
    menu_pks = {row["pk"] for row in read_model_rows("system.menu")}
    field_pks = {row["pk"] for row in read_model_rows("system.modellabelfield")}

    for entry in entries:
        payload = entry["fields"]
        assert payload["role"] in role_pks, f"角色不在种子内: {entry['pk']}"
        assert payload["menu"] in menu_pks, f"权限点不在种子内: {entry['pk']}"
        assert set(payload.get("field") or []) <= field_pks, f"字段不在种子内: {entry['pk']}"
        assert payload.get("field"), f"空白名单条目没有导入意义: {entry['pk']}"


def test_field_permission_seed_excludes_runtime_modules() -> None:
    """运行时模块自建的权限点不进种子（由模块自身的种子命令维护）。"""
    entries = read_model_rows("system.fieldpermission")
    leaked = [entry for entry in entries if str(entry["fields"]["menu"]).startswith(RUNTIME_MODULE_PREFIXES)]
    assert leaked == [], f"运行时模块权限点混入字段权限种子: {[e['pk'] for e in leaked]}"


def test_seeded_roles_have_field_permissions() -> None:
    """种子内角色都应至少有一条字段白名单（新角色漏配会静默空输出）。"""
    entries = read_model_rows("system.fieldpermission")
    covered = {entry["fields"]["role"] for entry in entries}
    seeded_roles = {row["pk"]: row["fields"].get("name") for row in _load("userrole.json")}
    missing = {name for pk, name in seeded_roles.items() if pk not in covered}
    assert missing == set(), f"种子角色缺字段白名单: {missing}"


#: 输出模型与视图主模型不同的动作（序列化器在 action 内手工构造）：白名单取值面
#: 是「接口实际输出的模型」，此处显式登记该模型标签；未登记的按路由反查视图主模型。
#: - access-logs：视图主模型为 file.uploadfile，但响应体是文件访问日志行
OUTPUT_MODEL_OVERRIDES = {
    "accessLogs:SystemUploadFile": "file.fileaccesslog",
}


def test_seed_whitelist_covers_menu_model() -> None:
    """种子白名单必须覆盖对应权限点的视图主模型（模型迁移后残留旧模型字段即失效）。

    运行时字段权限按「模型标签 + 字段名」匹配（get_user_field_queryset）：白名单与
    权限点模型脱节时等价于空白名单，该角色在列表页整块空白（新部署环境用种子导入
    会直接复现）。此处以 loadjson 静态校验（路由反查视图模型 + 字段树父子关系），
    与库内状态无关；权限点 path 匹配不到视图（自定义/运行时端点）时跳过。
    """
    from system.services.permission_sync import build_route_index

    entries = read_model_rows("system.fieldpermission")
    menus = {row["pk"]: row["fields"] for row in read_model_rows("system.menu")}
    fields = {row["pk"]: row["fields"] for row in read_model_rows("system.modellabelfield")}
    roles = {row["pk"]: row["fields"].get("name") for row in _load("userrole.json")}

    by_path: dict[str, str] = {}
    for route in build_route_index():
        model = getattr(getattr(getattr(route, "view_cls", None), "queryset", None), "model", None)
        if model is None:
            continue
        by_path[route.url] = model._meta.label_lower
        by_path[route.url.rstrip("$")] = model._meta.label_lower

    mismatched = []
    for entry in entries:
        payload = entry["fields"]
        menu = menus.get(payload["menu"])
        if not menu:
            continue
        path = menu.get("path") or ""
        model_label = (
            OUTPUT_MODEL_OVERRIDES.get(menu.get("name") or "") or by_path.get(path) or by_path.get(path.rstrip("$"))
        )
        if not model_label:
            continue
        roots = {
            pk
            for pk, row in fields.items()
            if row["name"] == model_label and row.get("field_type") == 0 and not row.get("parent")
        }
        if not roots:
            continue
        covered = any(fields.get(fid, {}).get("parent") in roots for fid in payload.get("field") or [])
        if not covered:
            mismatched.append(
                f"{roles.get(payload['role'], payload['role'])} × {menu['name']}（{path}，模型 {model_label}）"
            )
    assert mismatched == [], (
        "字段权限种子的白名单与权限点模型脱节（模型迁移残留，该角色页面将输出空对象）：\n" + "\n".join(mismatched)
    )
