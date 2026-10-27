#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""已下线权限点的升级清理（原迁移改由升级流程统一执行）。

``ops/legacy`` 的结构对齐路径不执行本仓库的数据迁移（旧库别名连接下 ORM
会命中默认库），因此历史上的清理迁移在旧库升级路径会落空。这里提供等价、
幂等的清理入口，由 ``post_upgrade`` 在 ``migrate`` 之后调用：此时默认连接
已指向目标库，删除只影响残留的菜单行与其元数据。
"""

from common.utils import get_logger

logger = get_logger(__name__)

#: 已下线权限点名称（视图集已无对应路由，存量库可能仍保留种子行）。
DEAD_PERMISSION_NAMES: tuple[str, ...] = ("retrieve:SystemUserLoginLog",)


def remove_dead_permissions() -> int:
    """删除已下线权限点及其菜单元数据；返回删除的菜单行数（幂等）。"""
    from system.models import Menu, MenuMeta

    if not DEAD_PERMISSION_NAMES:
        return 0
    rows = Menu.objects.filter(name__in=DEAD_PERMISSION_NAMES)
    count = rows.count()
    if not count:
        return 0
    meta_ids = [pk for pk in rows.values_list("meta_id", flat=True) if pk]
    rows.delete()
    if meta_ids:
        MenuMeta.objects.filter(pk__in=meta_ids).delete()
    logger.info("removed dead permissions: %s", ", ".join(DEAD_PERMISSION_NAMES))
    return int(count)


__all__ = ["DEAD_PERMISSION_NAMES", "remove_dead_permissions"]
