#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""identity 域跨 app 共享常量。

独立于 models 包（不触发模型注册），供 audit 等域以模块级 import 复用——
枚举语义属登录域，取值即库内整型值，改动须保持向后兼容。
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class LoginTypeChoices(models.IntegerChoices):
    USERNAME = 0, _("Username and password")
    SMS = 1, _("SMS verification code")
    EMAIL = 2, _("Email verification code")
    WECHAT = 4, _("Wechat scan code")
    # 第三方 OAuth/OIDC 登录：位标记风格下的独立槽位（0/1/2/4/8/9 已占用）
    OAUTH = 5, _("Third-party OAuth")
    # LDAP/AD 目录账号 bind 登录：经 LdapBindBackend 认证，
    # login_type 由 SessionTokenObtainPairSerializer 依 _ldap_authenticated 透传
    LDAP = 3, _("LDAP directory account")
    WEBSOCKET = 8, _("Websocket")
    UNKNOWN = 9, _("Unknown")
    # 用户模拟（管理员以该用户身份使用后台）：非真实登录，登录日志与
    # 在线会话以此类型区分；模拟发起人记录在同请求的操作日志里
    IMPERSONATE = 6, _("Impersonation")
