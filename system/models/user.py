#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user
# author : ly_13
# date : 8/10/2024

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.translation import gettext_lazy as _
from pilkit.processors import ResizeToFill
from django.conf import settings
from common.core.models import upload_directory_path, DbAuditModel, AutoCleanFileMixin
from common.fields.image import ProcessedImageField
from system.models import ModeTypeAbstract


class UserInfo(AutoCleanFileMixin, DbAuditModel, AbstractUser, ModeTypeAbstract):
    class GenderChoices(models.IntegerChoices):
        UNKNOWN = 0, _("Unknown")
        MALE = 1, _("Male")
        FEMALE = 2, _("Female")

    avatar = ProcessedImageField(verbose_name=_("Avatar"), null=True, blank=True,
                                 upload_to=upload_directory_path,
                                 processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
                                 scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
                                 format='png')

    nickname = models.CharField(verbose_name=_("Nickname"), max_length=150, blank=True)
    gender = models.IntegerField(choices=GenderChoices, default=GenderChoices.UNKNOWN, verbose_name=_("Gender"))
    phone = models.CharField(verbose_name=_("Phone"), max_length=16, default='', blank=True, db_index=True)
    email = models.EmailField(verbose_name=_("Email"), default='', blank=True, db_index=True)

    roles = models.ManyToManyField(to="system.UserRole", verbose_name=_("Role permission"), blank=True)
    rules = models.ManyToManyField(to="system.DataPermission", verbose_name=_("Data permission"), blank=True)
    dept = models.ForeignKey(to="system.DeptInfo", verbose_name=_("Department"), on_delete=models.PROTECT, blank=True,
                             null=True, related_query_name="dept_query")

    @property
    def preference(self):
        from system.models.preference import PreferenceManager
        return PreferenceManager(self)

    @property
    def secret_key(self):
        instance = self.preferences.filter(name="secret_key").first()
        if not instance:
            return
        return instance.decrypt_value

    # 从站点配置单独提取出来
    @property
    def lang(self):
        return self.preference.get_value("lang", default=settings.LANGUAGE_CODE)

    @lang.setter
    def lang(self, value):
        self.preference.set_value('lang', value)


    class Meta:
        verbose_name = _("Userinfo")
        verbose_name_plural = verbose_name
        ordering = ("-date_joined",)

    def __str__(self):
        return f"{self.nickname}({self.username})"
