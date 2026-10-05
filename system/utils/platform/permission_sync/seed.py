#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""菜单权限点同步内核：种子导出与合并。"""

import json
import re
from pathlib import Path


def dump_entries(model, objs, exclude_fields=()):
    """把模型实例序列化成 loaddata 兼容的 JSON 结构（与 dump_init_json 同口径）。"""
    from django.core import serializers

    fields = [item.name for item in model._meta.get_fields() if item.name not in exclude_fields]
    return json.loads(serializers.serialize("json", list(objs), fields=fields))


def detect_indent(text, default=1):
    match = re.search(r"\n( +)\S", text)
    return len(match.group(1)) if match else default


def seed_entry_pks(file_path):
    """读取种子文件已有条目的 pk 集合（不存在时返回空集）。"""
    path = Path(file_path)
    if not path.exists():
        return set()
    return {entry["pk"] for entry in json.loads(path.read_text(encoding="utf8"))}


def merge_seed_file(file_path, entries, normalize_creator=True):
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
