#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : message
# author : ly_13
# date : 9/15/2024

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class MessageContent(DbAuditModel):
    class NoticeChoices(models.IntegerChoices):
        SYSTEM = 0, _("System notification")
        NOTICE = 1, _("System announcement")
        USER = 2, _("User notification")
        DEPT = 3, _("Department notification")
        ROLE = 4, _("Role notification")

    class LevelChoices(models.TextChoices):
        DEFAULT = 'info', _("Ordinary notices")
        PRIMARY = 'primary', _("General notices")
        SUCCESS = 'success', _("Success notices")
        DANGER = 'danger', _("Important notices")

    notice_user = models.ManyToManyField("system.UserInfo", through="MessageUserRead", null=True, blank=True,
                                         through_fields=('notice', 'owner'), verbose_name=_("The notified user"))
    notice_dept = models.ManyToManyField("system.DeptInfo", null=True, blank=True,
                                         verbose_name=_("The notified department"))
    notice_role = models.ManyToManyField("system.UserRole", null=True, blank=True,
                                         verbose_name=_("The notified role"))
    level = models.CharField(verbose_name=_("Notice level"), choices=LevelChoices, default=LevelChoices.DEFAULT,
                             max_length=20)
    notice_type = models.SmallIntegerField(verbose_name=_("Notice type"), choices=NoticeChoices,
                                           default=NoticeChoices.USER)
    title = models.CharField(verbose_name=_("Notice title"), max_length=255)
    message = models.TextField(verbose_name=_("Notice message"), blank=True, null=True)
    extra_json = models.JSONField(verbose_name=_("Additional json data"), blank=True, null=True)
    file = models.ManyToManyField("system.UploadFile", verbose_name=_("Uploaded attachments"))
    publish = models.BooleanField(verbose_name=_("Publish"), default=True)

    @classmethod
    @property
    def user_choices(cls):
        return [cls.NoticeChoices.USER, cls.NoticeChoices.SYSTEM]

    @classmethod
    @property
    def notice_choices(cls):
        return [cls.NoticeChoices.NOTICE, cls.NoticeChoices.DEPT, cls.NoticeChoices.ROLE]

    class Meta:
        verbose_name = _("Message content")
        verbose_name_plural = verbose_name
        ordering = ('-created_time',)

    def delete(self, using=None, keep_parents=False):
        if self.file:
            for file in self.file.all():
                file.delete()
        return super().delete(using, keep_parents)

    def __str__(self):
        return f"{self.title}-{self.get_notice_type_display()}"


class MessageUserRead(DbAuditModel):
    owner = models.ForeignKey("system.UserInfo", on_delete=models.CASCADE, verbose_name=_("User"))
    notice = models.ForeignKey(MessageContent, on_delete=models.CASCADE, verbose_name=_("Notice"))
    unread = models.BooleanField(verbose_name=_("Unread"), default=True, blank=False, db_index=True)

    class Meta:
        ordering = ('-created_time',)
        verbose_name = _("User read message")
        verbose_name_plural = verbose_name
        indexes = [models.Index(fields=['owner', 'unread'])]
        unique_together = ('owner', 'notice')
