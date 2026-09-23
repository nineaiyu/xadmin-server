#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""列表「我的视图」（F-4）：命名保存的筛选条件。

- 个人级（owner）为主，``is_shared`` 打开后同页其他用户可见（只读应用）；
- ``page_key`` = 前端路由 path（与列偏好 / 分栏配置同一套页面键口径）；
- 视图只存**条件**，执行时仍按当前用户权限裁剪（不越权）；
- 同一用户同一页面同名唯一；``is_default`` 每人每页至多一个（保存时互斥）。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class SavedListView(DbAuditModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        "system.UserInfo",
        related_name="saved_list_views",
        on_delete=models.CASCADE,
        verbose_name=_("Owner"),
    )
    page_key = models.CharField(_("Page key"), max_length=128, db_index=True)
    name = models.CharField(_("View name"), max_length=64)
    # 搜索区条件快照（前端 searchFields，剔除 page/size 与空值）
    conditions = models.JSONField(_("Conditions"), default=dict, blank=True)
    ordering = models.CharField(_("Ordering"), max_length=64, blank=True, default="")
    is_default = models.BooleanField(_("Is default"), default=False)
    is_shared = models.BooleanField(_("Is shared"), default=False)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        ordering = ["page_key", "-is_default", "created_time"]
        verbose_name = _("Saved list view")
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=["owner", "page_key", "name"], name="uniq_saved_view_owner_page_name"),
        ]

    def __str__(self):
        return f"{self.owner_id} {self.page_key} {self.name}"
