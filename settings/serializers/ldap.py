#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP/AD 配置序列化器。

字段名即配置名（BaseSettingViewSet 约定）；``LDAP_BIND_PASSWORD`` write_only
⇒ Setting.encrypted=True 值级加密落库，且 retrieve 回显时自动剔除。
"""

from django.db.models import TextChoices
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from common.core.fields import LabeledChoiceField
from settings.serializers.contract import SettingSaveContractMixin


class LdapSettingSerializer(SettingSaveContractMixin, serializers.Serializer):
    # 认证接入
    class AuthPriorityChoices(TextChoices):
        """认证优先级取值：value 为存量存储值，label 供 API 元数据与前端下拉展示。"""

        LOCAL_FIRST = "local_first", _("Local accounts first")
        LDAP_FIRST = "ldap_first", _("Directory first")

    LDAP_AUTH_ENABLED = serializers.BooleanField(
        default=False, label=_("LDAP authentication"), help_text=_("Enable LDAP/AD account login")
    )
    LDAP_AUTH_PRIORITY = LabeledChoiceField(
        choices=AuthPriorityChoices.choices,
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
        help_text=_("Encrypted at rest; never returned by the API; submit empty to keep the current one"),
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
        help_text=_("LDAP search filter; empty falls back to (objectClass=person)"),
    )

    # 字段映射（固定四键）
    LDAP_ATTR_USERNAME = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Username attribute"),
        help_text=_("e.g. sAMAccountName / uid; empty falls back to sAMAccountName"),
    )
    LDAP_ATTR_NICKNAME = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Nickname attribute"),
        help_text=_("e.g. cn / displayName; empty falls back to cn"),
    )
    LDAP_ATTR_EMAIL = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Email attribute"),
        help_text=_("e.g. mail; empty falls back to mail"),
    )
    LDAP_ATTR_PHONE = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Phone attribute"),
        help_text=_("e.g. telephoneNumber / mobile; empty falls back to telephoneNumber"),
    )

    # 部门
    LDAP_DEPT_ENABLED = serializers.BooleanField(
        default=True,
        label=_("Sync departments"),
        help_text=_("Sync organizational units under the department search base as the department tree"),
    )
    LDAP_DEPT_SEARCH_BASE = serializers.CharField(
        max_length=512,
        required=False,
        allow_blank=True,
        label=_("Department search base"),
        help_text=_("Base DN for department (OU) search; empty disables department sync"),
    )

    # 定时同步
    LDAP_SYNC_ENABLED = serializers.BooleanField(
        default=False,
        label=_("Scheduled sync"),
        help_text=_("Hourly sync of users/departments/status from the directory"),
    )
    LDAP_SYNC_AUTO_CREATE = serializers.BooleanField(default=True, label=_("Auto create user on sync"))

    class SyncMissingPolicyChoices(TextChoices):
        """缺失用户处置策略取值：value 为存量存储值，label 供 API 元数据与前端下拉展示。"""

        DEACTIVATE = "deactivate", _("Deactivate account")
        SOFT_DELETE = "soft_delete", _("Move to recycle bin")
        IGNORE = "ignore", _("Keep untouched")

    LDAP_SYNC_MISSING_POLICY = LabeledChoiceField(
        choices=SyncMissingPolicyChoices.choices,
        default="deactivate",
        label=_("Missing user policy"),
        help_text=_(
            "deactivate: disable accounts removed from the directory (reversible); "
            "soft_delete: move them to the recycle bin; ignore: keep untouched"
        ),
    )
    # 组 → 角色映射（组同步）：读取用户所属组的属性名 + 组 DN/CN → 平台角色 code 映射；
    # 映射为空 = 不启用（只管理映射中出现的角色，手工授权不受影响）
    LDAP_ATTR_GROUPS = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        label=_("Group attribute"),
        help_text=_(
            "Directory attribute holding group membership (AD default: memberOf); empty disables group to role mapping"
        ),
    )
    LDAP_GROUP_ROLE_MAP = serializers.DictField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        label=_("Group to role mapping"),
        help_text=_("Map directory group DN/CN (case-insensitive) to platform role code; empty disables the mapping"),
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
