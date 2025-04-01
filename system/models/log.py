#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : log
# author : ly_13
# date : 8/10/2024

import datetime

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.core.models import DbAuditModel


class UserLoginLog(DbAuditModel):
    class LoginTypeChoices(models.IntegerChoices):
        USERNAME = 0, _("Username and password")
        SMS = 1, _("SMS verification code")
        EMAIL = 2, _("Email verification code")
        WECHAT = 4, _("Wechat scan code")
        WEBSOCKET = 8, _("Websocket")
        UNKNOWN = 9, _("Unknown")

    status = models.BooleanField(default=True, verbose_name=_("Login status"))
    ipaddress = models.GenericIPAddressField(verbose_name=_("IpAddress"), null=True, blank=True)
    city = models.CharField(max_length=254, verbose_name=_("Login city"), null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name=_("Browser"), null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name=_("System"), null=True, blank=True)
    agent = models.CharField(max_length=128, verbose_name=_("Agent"), null=True, blank=True)
    channel_name = models.CharField(max_length=128, verbose_name=_("Channel name"), null=True, blank=True)
    login_type = models.SmallIntegerField(default=LoginTypeChoices.USERNAME, choices=LoginTypeChoices,
                                          verbose_name=_("Login type"))

    class Meta:
        verbose_name = _("User login log")
        verbose_name_plural = verbose_name
        ordering = ('-created_time',)

    @staticmethod
    def get_login_type(query_key):
        if query_key == "email":
            login_type = UserLoginLog.LoginTypeChoices.EMAIL
        elif query_key == "phone":
            login_type = UserLoginLog.LoginTypeChoices.SMS
        elif query_key == "username":
            login_type = UserLoginLog.LoginTypeChoices.USERNAME
        else:
            login_type = UserLoginLog.LoginTypeChoices.UNKNOWN
        return login_type


class OperationLog(DbAuditModel):
    module = models.CharField(max_length=64, verbose_name=_("Module"), null=True, blank=True)
    path = models.CharField(max_length=400, verbose_name=_("URL path"), null=True, blank=True)
    body = models.TextField(verbose_name=_("Request body"), null=True, blank=True)
    method = models.CharField(max_length=8, verbose_name=_("Request method"), null=True, blank=True)
    ipaddress = models.GenericIPAddressField(verbose_name=_("IpAddress"), null=True, blank=True)
    browser = models.CharField(max_length=64, verbose_name=_("Browser"), null=True, blank=True)
    system = models.CharField(max_length=64, verbose_name=_("System"), null=True, blank=True)
    response_code = models.IntegerField(verbose_name=_("Response code"), null=True, blank=True)
    response_result = models.TextField(verbose_name=_("Response result"), null=True, blank=True)
    status_code = models.IntegerField(verbose_name=_("Status code"), null=True, blank=True)
    request_uuid = models.UUIDField(verbose_name=_("Request ID"), null=True, blank=True)
    exec_time = models.FloatField(verbose_name=_("Execution time"), null=True, blank=True)

    class Meta:
        verbose_name = _("Operation log")
        verbose_name_plural = verbose_name
        ordering = ("-created_time",)

    def remove_expired(cls, clean_day=30 * 6):
        clean_time = timezone.now() - datetime.timedelta(days=clean_day)
        cls.objects.filter(created_time__lt=clean_time).delete()

    remove_expired = classmethod(remove_expired)
