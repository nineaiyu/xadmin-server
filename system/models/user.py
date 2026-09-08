#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : user
# author : ly_13
# date : 8/10/2024

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils.translation import gettext_lazy as _
from pilkit.processors import ResizeToFill

from common.core.models import (
    SoftDeleteManager,
    SoftDeleteModel,
    SoftDeleteQuerySet,
    upload_directory_path,
    DbAuditModel,
    AutoCleanFileMixin,
)
from common.fields.image import ProcessedImageField
from system.models import ModeTypeAbstract


class SoftDeleteUserManager(SoftDeleteManager, UserManager):
    """用户软删除管理器——默认查询过滤已删除用户，
    同时保留 UserManager 的 create_user / create_superuser 等能力。"""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(deleted_at__isnull=True)


class UserInfo(SoftDeleteModel, AutoCleanFileMixin, DbAuditModel, AbstractUser, ModeTypeAbstract):
    """用户软删除——删除进入回收站可恢复；
    登录/鉴权走默认管理器（过滤 deleted_at），软删除用户的存量 JWT 立即失效。"""

    objects = SoftDeleteUserManager()

    class GenderChoices(models.IntegerChoices):
        UNKNOWN = 0, _("Unknown")
        MALE = 1, _("Male")
        FEMALE = 2, _("Female")

    class MFALevelChoices(models.IntegerChoices):
        DISABLED = 0, _("Disabled")
        ENABLED = 1, _("Enabled")

    avatar = ProcessedImageField(
        verbose_name=_("Avatar"),
        null=True,
        blank=True,
        upload_to=upload_directory_path,
        processors=[ResizeToFill(512, 512)],  # 默认存储像素大小
        scales=[1, 2, 3, 4],  # 缩略图可缩小倍数，
        format="png",
    )

    nickname = models.CharField(verbose_name=_("Nickname"), max_length=150, blank=True)
    gender = models.IntegerField(choices=GenderChoices, default=GenderChoices.UNKNOWN, verbose_name=_("Gender"))
    phone = models.CharField(verbose_name=_("Phone"), max_length=16, default="", blank=True, db_index=True)
    email = models.EmailField(verbose_name=_("Email"), default="", blank=True, db_index=True)

    # MFA 二次验证（登录 MFA 开关 + OTP 密钥，密钥泄露即可重置密码，无需加密存储）
    mfa_level = models.IntegerField(
        verbose_name=_("MFA level"), choices=MFALevelChoices.choices, default=MFALevelChoices.DISABLED
    )
    otp_secret_key = models.CharField(verbose_name=_("OTP secret key"), max_length=64, default="", blank=True)

    roles = models.ManyToManyField(to="system.UserRole", verbose_name=_("Role permission"), blank=True)
    rules = models.ManyToManyField(to="system.DataPermission", verbose_name=_("Data permission"), blank=True)
    dept = models.ForeignKey(
        to="system.DeptInfo",
        verbose_name=_("Department"),
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        related_query_name="dept_query",
    )

    class Meta:
        verbose_name = _("Userinfo")
        verbose_name_plural = verbose_name
        ordering = ("-date_joined",)
        # 注意：username 不做"未删除数据"条件唯一（Django auth.E003 要求
        # USERNAME_FIELD 全局唯一，部分唯一约束不满足检查），
        # 已删除用户的用户名在 DB 层仍被占用，序列化器按 all_objects 拦截并给出可读提示

    def __str__(self):
        return f"{self.nickname}({self.username})"

    @property
    def mfa_enabled(self):
        """是否已启用登录 MFA 二次验证（OTP 绑定成功后自动开启）"""
        return self.mfa_level == self.MFALevelChoices.ENABLED and bool(self.otp_secret_key)
