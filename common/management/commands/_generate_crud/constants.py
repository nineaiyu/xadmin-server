#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成器：常量与命名空间。"""

import uuid

from django.db import models

# 第一方顶层包（与 ruff.toml [lint.isort].known-first-party 对齐）：
# 产物必须一次通过 ruff check（I001），import 分组/排序口径与 isort 保持一致
FIRST_PARTY_TOP_LEVEL = frozenset(
    {
        "captcha",
        "common",
        "demo",
        "loadtest",
        "message",
        "mfa",
        "notifications",
        "server",
        "settings",
        "system",
        "utils",
    }
)


def _import_name_sort_key(name: str) -> tuple:
    """isort order-by-type 口径的 name 排序键：常量 → 类 → 函数/模块（同类内字母序）。"""
    base = name.split(" as ")[0].strip()
    if base.isupper():
        rank = 0
    elif base[:1].isupper():
        rank = 1
    else:
        rank = 2
    return (rank, base.lower())


# 生成块标记：views.py / serializers.py 的幂等合并锚点
BLOCK_START = "# --- xadmin:generated:{key}:start ---"
BLOCK_END = "# --- xadmin:generated:{key}:end ---"
# 菜单种子 pk 的 uuid5 命名空间（固定常量，保证确定性）
SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/nineaiyu/xadmin-server/generated-seed")
# 权限码：动作 → (HTTP 方法, 路径正则)；路径口径与既有种子一致（无前导 ^，$ 收尾）
PERMISSION_ACTIONS = (
    ("list", "GET", "{prefix}$"),
    ("retrieve", "GET", "{prefix}/(?P<pk>[^/.]+)$"),
    ("create", "POST", "{prefix}$"),
    ("partialUpdate", "PATCH", "{prefix}/(?P<pk>[^/.]+)$"),
    ("destroy", "DELETE", "{prefix}/(?P<pk>[^/.]+)$"),
)
IMPORT_EXPORT_PERMISSIONS = (
    ("exportData", "GET", "{prefix}/export-data$"),
    ("importData", "POST", "{prefix}/import-data$"),
)
# 不进序列化器/搜索的字段类型与审计字段名
SEARCH_EXCLUDE_TYPES = (models.JSONField, models.FileField, models.ImageField, models.BinaryField)
AUDIT_FIELDS = ("creator", "modifier", "dept_belong")
FILE_RELATED_MODEL = "system.uploadfile"
