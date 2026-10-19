#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：种子导出与合并。"""

import json
import re
from pathlib import Path
from typing import Any


def dump_entries(model: Any, objs: Any, exclude_fields: Any = ()) -> Any:
    """把模型实例序列化成 loaddata 兼容的 JSON 结构（与 dump_init_json 同口径）。"""
    from django.core import serializers

    fields = [item.name for item in model._meta.get_fields() if item.name not in exclude_fields]
    return json.loads(serializers.serialize("json", list(objs), fields=fields))


def write_field_seed_layers(manifests: Any = None, apps: Any = None) -> Any:
    """把字段树与种子角色的字段白名单按应用归属回写各层种子。

    - ``system.modellabelfield``：库内同名字段节点按 `<app>/seeds/` 归属落盘；
    - ``system.fieldpermission``：只回写「种子角色 × 种子权限点」的组合（运行时角色与
      插件自建权限点由各自命令维护）。

    ``apps`` 限定只写这些应用（默认全部）；平台层行需显式包含 ``"platform"``。
    返回 ``[(层, 相对路径, 行数), ...]``，供命令输出。写入使用 ``merge=True``，
    保留同层其它行。
    """
    from django.conf import settings

    from identity.services import UserRole
    from system.models import FieldPermission, ModelLabelField
    from system.services.seeding import PLATFORM, discover, read_model_rows, row_owners, write_layer_model

    from .constants import RUNTIME_PERMISSION_PREFIXES

    manifests = manifests or discover().manifests
    selected = set(apps) if apps else None
    base = Path(settings.PROJECT_DIR)
    written: list[Any] = []

    def flush(
        model: str, entries: list[dict[str, Any]], context: dict[str, list[dict[str, Any]]] | None = None
    ) -> None:
        merged = {str(row["pk"]): row for row in read_model_rows(model)}
        for entry in entries:
            merged[str(entry["pk"])] = entry
        # 归属计算需要同级引用（字段白名单跟随 menu 归属），只传本模型会让全部行落到平台层
        rows_by_model = {**(context or {}), model: list(merged.values())}
        owners = row_owners(rows_by_model, manifests)[model]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            grouped.setdefault(owners.get(str(entry["pk"]), PLATFORM), []).append(entry)
        for app, rows in sorted(grouped.items()):
            if selected is not None and app not in selected:
                continue
            path = write_layer_model(base, app, model, rows, manifests=manifests, merge=True)
            written.append((app, str(path.relative_to(base)), len(rows)))

    field_entries = dump_entries(ModelLabelField, ModelLabelField.objects.all(), exclude_fields=("updated_time",))
    flush("system.modellabelfield", field_entries)

    seed_role_pks = {row["pk"] for row in read_model_rows("identity.userrole")}
    menu_rows = read_model_rows("system.menu")
    seed_menu_pks = {str(row["pk"]) for row in menu_rows if not str(row["pk"]).startswith(RUNTIME_PERMISSION_PREFIXES)}
    seeded_roles = UserRole.objects.filter(pk__in=seed_role_pks)
    permission_entries = dump_entries(
        FieldPermission,
        FieldPermission.objects.filter(role__in=seeded_roles, menu_id__in=seed_menu_pks),
        exclude_fields=("created_time", "updated_time"),
    )
    flush("system.fieldpermission", permission_entries, context={"system.menu": menu_rows})
    return written


def detect_indent(text: Any, default: Any = 1) -> Any:
    match = re.search(r"\n( +)\S", text)
    return len(match.group(1)) if match else default


def seed_entry_pks(file_path: Any) -> Any:
    """读取种子文件已有条目的 pk 集合（不存在时返回空集）。"""
    path = Path(file_path)
    if not path.exists():
        return set()
    return {entry["pk"] for entry in json.loads(path.read_text(encoding="utf8"))}


def merge_seed_file(file_path: Any, entries: Any, normalize_creator: Any = True) -> Any:
    """把条目合并进 loadjson 种子文件（按 pk 原地替换、新条目追加，保持既有缩进）。"""
    path = Path(file_path)
    text = path.read_text(encoding="utf8") if path.exists() else "[]"
    indent = detect_indent(text)
    data = json.loads(text)
    index = {entry["pk"]: position for position, entry in enumerate(data)}

    added = updated = 0
    for entry in entries:
        if normalize_creator:
            entry["fields"]["creator"] = 1
            entry["fields"]["modifier"] = 1
        if entry["pk"] in index:
            data[index[entry["pk"]]] = entry
            updated += 1
        else:
            data.append(entry)
            added += 1

    with path.open("w", encoding="utf8") as fh:
        json.dump(data, fh, indent=indent, ensure_ascii=False)
        fh.write("\n")
    return {"file": str(path), "added": added, "updated": updated}
