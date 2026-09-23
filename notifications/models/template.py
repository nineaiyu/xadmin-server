#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""通知消息模板覆盖。

代码内模板是**默认值**，本表是**可选的 DB 覆盖层**：

- ``subject_template`` / ``body_template`` 为空 = 该字段用代码默认（零行为变化）；
- 渲染在渠道渲染收口（``Message.get_backend_msg_mapper``）统一套用，
  邮件 / 站内信 / 短信 / IM 全部渠道一致；
- 模板语法为 Django 模板的变量插值（``{{ subject }}`` / ``{{ message }}`` + 消息类型
  自定义变量），保存时校验语法与变量白名单（不开放任意模板逻辑）。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class MessageTemplate(DbAuditModel):
    """消息模板覆盖行（message_type 唯一；删除 = 重置回代码默认）。"""

    id = models.BigAutoField(primary_key=True)
    # 消息类型 = 消息类名（Message.get_message_type()，与订阅表同口径）
    message_type = models.CharField(_("Message type"), max_length=128, unique=True)
    subject_template = models.TextField(_("Subject template"), blank=True, default="")
    body_template = models.TextField(_("Body template"), blank=True, default="")
    is_active = models.BooleanField(_("Is active"), default=True)
    remark = models.CharField(_("Remark"), max_length=255, blank=True, default="")

    class Meta:
        ordering = ["message_type"]
        verbose_name = _("Message template")
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.message_type}"
