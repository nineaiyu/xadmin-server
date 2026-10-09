#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""岗位（Post）：组织上的人员维度，与部门（组织维度）、角色（权限维度）互补。

设计边界：

- **不参与权限判定**：权限只经角色授予；岗位用于人员标识、审批人解析的补充筛选
  与人员名录展示，避免「岗位即权限」导致授权模型分叉；
- **一人可兼多岗**：关联形态为 ``UserInfo.posts`` 多对多（人力场景常见：兼岗/代岗）；
- **部门归属可空**：空 = 全组织通用岗（如「安全员」），非空 = 该部门下的岗位；
- 名称与编码在**未删除数据**内唯一（软删除模型，删除进入回收站并释放名称/编码）。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel, DbUuidModel, SoftDeleteModel


class Post(SoftDeleteModel, DbAuditModel, DbUuidModel):
    """岗位定义（软删除：删除进回收站，可恢复）。"""

    name = models.CharField(verbose_name=_("Post name"), max_length=64)
    code = models.CharField(verbose_name=_("Post code"), max_length=64)
    dept = models.ForeignKey(
        "identity.DeptInfo",
        verbose_name=_("Dept"),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="posts",
        help_text=_("Empty for organization-wide posts"),
    )
    rank = models.IntegerField(verbose_name=_("Rank"), default=99)
    is_active = models.BooleanField(verbose_name=_("Is active"), default=True, db_index=True)

    class Meta:
        verbose_name = _("Post")
        verbose_name_plural = verbose_name
        ordering = ("rank", "name")
        constraints = [
            # 唯一性仅作用于未删除数据（与 UserRole/DeptInfo 同口径）：
            # 已删除岗位释放其名称与编码，回收站恢复时由校验器兜底
            models.UniqueConstraint(
                fields=["name"], condition=models.Q(deleted_at__isnull=True), name="uniq_post_name_active"
            ),
            models.UniqueConstraint(
                fields=["code"], condition=models.Q(deleted_at__isnull=True), name="uniq_post_code_active"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name}({self.code})"
