#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""文件访问审计模型（identity 域拆分后暂留 system，随 file 域切分迁移）。

- ``FileAccessLog``：文件访问审计（上传 / 下载 / 预览 / 删除留痕）。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class FileAccessLog(models.Model):
    """文件访问审计：上传 / 下载 / 预览 / 删除四类动作的元数据留痕。

    高频写表：只记元数据（不含文件内容），保留期随审计口径统一清理；
    文件与用户删除后靠名称快照保留可读性（不级联删除日志）。
    """

    class Action(models.TextChoices):
        UPLOAD = "upload", _("Upload")
        DOWNLOAD = "download", _("Download")
        PREVIEW = "preview", _("Preview")
        DELETE = "delete", _("Delete")

    id = models.BigAutoField(primary_key=True)
    file = models.ForeignKey(
        "file.UploadFile",
        related_name="access_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("File"),
    )
    filename = models.CharField(_("Filename"), max_length=255, blank=True, default="")
    user = models.ForeignKey(
        "identity.UserInfo",
        related_name="file_access_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_("User"),
    )
    user_display = models.CharField(_("User display"), max_length=128, blank=True, default="")
    action = models.CharField(_("Action"), max_length=16, choices=Action.choices, db_index=True)
    ipaddress = models.CharField(_("IP address"), max_length=64, blank=True, default="")
    result = models.BooleanField(_("Result"), default=True)
    detail = models.CharField(_("Detail"), max_length=255, blank=True, default="")
    created_time = models.DateTimeField(_("Created time"), auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_time"]
        verbose_name = _("File access log")
        verbose_name_plural = verbose_name
        indexes = [
            models.Index(fields=["file", "created_time"], name="idx_file_log_file_created"),
            models.Index(fields=["user", "action"], name="idx_file_log_user_action"),
        ]

    def __str__(self):
        return f"{self.filename} {self.action} by {self.user_display}"
