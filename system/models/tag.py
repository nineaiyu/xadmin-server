#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通用标签中心：轻量分类运营 + 批量筛选。

设计边界：

- **白名单准入**：仅登记在 ``TAGGABLE_MODELS`` 的对象可打标（不做全模型铺开）；
- **关联形态**：``TaggedItem`` 用 ``content_type + object_id`` 关联目标对象，
  目标模型侧声明 ``GenericRelation("system.TaggedItem")``（可预取，列表零 N+1）；
- **权限**：标签管理 4 个权限点；打标权限回落业务对象的 update 权限点
  （不新增对象级权限点，避免权限点膨胀）；
- **治理**：使用计数 + 删除保护（被引用时提示先解绑）。

首批试点 3 个对象：系统用户 / 文件 / 审批实例。
"""

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel

#: 可打标对象白名单："app_label.model"（小写）→ 展示名 + 打标权限的回落访问路径（PATCH 模板）
#: 打标权限回落业务对象的 update 权限点（不新增对象级权限点）
TAGGABLE_MODELS = {
    "system.userinfo": {"label": _("User"), "visit": "/api/system/user/<pk>"},
    "system.uploadfile": {"label": _("File"), "visit": "/api/system/file/<pk>"},
    "system.approvalinstance": {"label": _("Approval instance"), "visit": "/api/system/approval-instances/<pk>"},
}


class Tag(DbAuditModel, DbUuidModel):
    """标签定义：名称唯一 + 颜色（前端渲染）+ 内置标记（内置标签不允许删除）。"""

    name = models.CharField(_("Tag name"), max_length=64, unique=True)
    color = models.CharField(_("Color"), max_length=16, blank=True, default="")
    builtin = models.BooleanField(_("Builtin"), default=False, db_index=True)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        verbose_name = _("Tag")
        verbose_name_plural = _("Tags")
        ordering = ("name",)

    def __str__(self):
        return self.name


class TaggedItem(DbAuditModel, DbUuidModel):
    """对象打标关联：``unique(content_type, object_id, tag)`` 保证同一对象同一标签一次。"""

    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="tagged_items", verbose_name=_("Tag"))
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, verbose_name=_("Content type"), db_index=True
    )
    object_id = models.CharField(_("Object id"), max_length=64, db_index=True)

    class Meta:
        verbose_name = _("Tagged item")
        verbose_name_plural = _("Tagged items")
        ordering = ("-created_time",)
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "tag"], name="uniq_tagged_item_content_object_tag"
            )
        ]
        indexes = [models.Index(fields=["content_type", "object_id"], name="tagged_item_target_idx")]

    def __str__(self):
        return f"{self.content_type_id}:{self.object_id}#{self.tag_id}"
