#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""代码生成方案：命名保存的代码生成表单状态。

- ``payload`` 存放代码生成页表单状态全文（前端序列化结果），服务端存储
  替代浏览器本地缓存（不受单浏览器数据清理影响，可跨设备取用）；
- 个人级资源：``creator`` 为保存者，非本人不可改 / 删；
- ``is_shared`` 打开后其他用户可见（只读应用），取值域 = 本人 ∪ 共享；
- ``description`` 由审计基类提供，作为方案备注。
"""

import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class CodegenPlan(DbAuditModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(_("Plan name"), max_length=64)
    payload = models.JSONField(_("Payload"), default=dict, blank=True)
    is_shared = models.BooleanField(_("Is shared"), default=False)

    class Meta:
        ordering = ["-updated_time"]
        verbose_name = _("Codegen plan")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.creator_id} {self.name}"
