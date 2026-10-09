#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""插件自有模型（独立迁移，随插件包分发）。

二开数据面约定：

- 继承框架内核的抽象基类（``DbAuditModel`` 带 creator/created_time/updated_time），
  审计字段、软删除、回收站等能力与业务 app 同源；
- 迁移文件放在插件包内（``xadmin_demo_plugin/migrations/``），由插件维护者自行演进，
  与宿主迁移链解耦（宿主只执行 ``manage.py migrate xadmin_demo_plugin``）；
- 跨 app 外键请引用内核提供的抽象（``common.core.models``）或经 ``<app>.services``
  契约层，避免插件与宿主业务 app 的模块级耦合。
"""

from django.db import models

from common.core.models import DbAuditModel


class PluginNote(DbAuditModel):
    """示例便签：演示「插件表」的完整链路（模型 → 迁移 → 序列化器 → ViewSet → 影响面守卫）。"""

    title = models.CharField(verbose_name="标题", max_length=100)
    content = models.TextField(verbose_name="内容", blank=True, default="")
    is_active = models.BooleanField(verbose_name="是否启用", default=True)

    class Meta:
        verbose_name = "插件示例便签"
        verbose_name_plural = verbose_name
        ordering = ("pk",)

    def __str__(self) -> str:
        return str(self.title)
