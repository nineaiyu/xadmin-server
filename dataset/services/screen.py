#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""大屏模板版本领域服务：快照、保留数裁剪。"""

from typing import Any

from django.conf import settings

from dataset.models.screen import ScreenTemplateVersion
from dataset.utils.screen_layout import SCREEN_TEMPLATE_VERSION_KEEP


def version_keep() -> int:
    """版本保留数：允许 settings 覆盖（便于按环境/测试调小），缺省取领域常量。"""
    return int(getattr(settings, "SCREEN_TEMPLATE_VERSION_KEEP", SCREEN_TEMPLATE_VERSION_KEEP))


def snapshot_template_version(template: Any, actor: Any = None) -> Any:
    """给模板记一版快照，并把超出保留数的旧版本裁掉。

    只在有窗格时记（空载荷不是有效模板）；版本号沿用「当前最大 + 1」，删除最旧的若干条。
    """
    if not (template.free_layout or {}).get("panes"):
        return None
    latest = template.versions.first()
    version = (latest.version if latest else 0) + 1
    snapshot = ScreenTemplateVersion.objects.create(
        template=template,
        version=version,
        name=template.name,
        group=template.group,
        free_layout=template.free_layout,
        creator=actor,
        modifier=actor,
    )
    stale = list(template.versions.values_list("pk", flat=True)[version_keep() :])
    if stale:
        ScreenTemplateVersion.objects.filter(pk__in=stale).delete()
    return snapshot


# 视图兼容别名（历史私有名保留，避免存量引用断裂）
_version_keep = version_keep
