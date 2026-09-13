#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP/AD 配置序列化器（ADR-017）。

字段名即配置名（BaseSettingViewSet 约定）；``LDAP_BIND_PASSWORD`` write_only
⇒ Setting.encrypted=True 值级加密落库，且 retrieve 回显时自动剔除。
"""

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class LdapSettingSerializer(serializers.Serializer):
    # 认证接入
    LDAP_AUTH_ENABLED = serializers.BooleanField(
        default=False, label=_("LDAP authentication"), help_text=_("Enable LDAP/AD account login")
    )
    LDAP_AUTH_PRIORITY = serializers.ChoiceField(
        choices=["local_first", "ldap_first"],
        default="local_first",
        label=_("Authentication priority"),
        help_text=_(
            "local_first: local accounts with a usable password are authenticated locally first "
            "(directory password never shadows local admin); ldap_first: directory bind is tried first"
        ),
    )
    LDAP_AUTH_AUTO_CREATE = serializers.BooleanField(
        default=True,
        label=_("Auto create user on first login"),
        help_text=_("Create a local user (no local password) on first successful directory bind"),
    )

    # 连接（SERVER_URI / SEARCH_BASE 留空由视图层给出可读错误，连接测试可先于保存使用）
    LDAP_SERVER_URI = serializers.CharField(
        max_length=256,
        required=False,
        allow_blank=True,
        label=_("Server URI"),
        help_text=_("ldap://host:389 or ldaps://host:636"),
    )
    LDAP_START_TLS = serializers.BooleanField(
        default=False,
        label=_("StartTLS"),
        help_text=_("Upgrade the connection to TLS after connecting (ldap:// URIs)"),
    )
    LDAP_BIND_DN = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        label=_("Bind DN"),
        help_text=_("Service account DN used to search the directory; empty for anonymous bind"),
    )
    LDAP_BIND_PASSWORD = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        write_only=True,
        label=_("Bind password"),
        help_text=_("Encrypted at rest; never returned by the API"),
    )
    LDAP_CONNECT_TIMEOUT = serializers.IntegerField(
        default=10, min_value=1, max_value=120, label=_("Connect timeout (seconds)")
    )

    # 用户
    LDAP_USER_SEARCH_BASE = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        label=_("User search base"),
        help_text=_("Base DN for user search"),
    )
    LDAP_USER_FILTER = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        label=_("User filter"),
        help_text=_("LDAP search filter, e.g. (objectClass=person)"),
    )

    # 字段映射（固定四键）
    LDAP_ATTR_USERNAME = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Username attribute"),
        help_text=_("e.g. sAMAccountName / uid"),
    )
    LDAP_ATTR_NICKNAME = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Nickname attribute"),
        help_text=_("e.g. cn / displayName"),
    )
    LDAP_ATTR_EMAIL = serializers.CharField(
        max_length=64, required=False, allow_blank=True, label=_("Email attribute"), help_text=_("e.g. mail")
    )
    LDAP_ATTR_PHONE = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Phone attribute"),
        help_text=_("e.g. telephoneNumber / mobile"),
    )

    # 部门
    LDAP_DEPT_ENABLED = serializers.BooleanField(
        default=True,
        label=_("Sync departments"),
        help_text=_("Sync organizational units under the department search base as the department tree"),
    )
    LDAP_DEPT_SEARCH_BASE = serializers.CharField(
        max_length=512, required=False, allow_blank=True, label=_("Department search base")
    )

    # 定时同步
    LDAP_SYNC_ENABLED = serializers.BooleanField(
        default=False,
        label=_("Scheduled sync"),
        help_text=_("Hourly sync of users/departments/status from the directory"),
    )
    LDAP_SYNC_AUTO_CREATE = serializers.BooleanField(default=True, label=_("Auto create user on sync"))
    LDAP_SYNC_MISSING_POLICY = serializers.ChoiceField(
        choices=["deactivate", "soft_delete", "ignore"],
        default="deactivate",
        label=_("Missing user policy"),
        help_text=_(
            "deactivate: disable accounts removed from the directory (reversible); "
            "soft_delete: move them to the recycle bin; ignore: keep untouched"
        ),
    )

    # 留白的可选字段收敛到默认值：空 filter/空属性名会让搜索静默失效
    _BLANK_DEFAULTS = {
        "LDAP_USER_FILTER": "(objectClass=person)",
        "LDAP_ATTR_USERNAME": "sAMAccountName",
        "LDAP_ATTR_NICKNAME": "cn",
        "LDAP_ATTR_EMAIL": "mail",
        "LDAP_ATTR_PHONE": "telephoneNumber",
    }

    def validate(self, attrs):
        for key, default in self._BLANK_DEFAULTS.items():
            if attrs.get(key) in (None, ""):
                attrs[key] = default
        return attrs
