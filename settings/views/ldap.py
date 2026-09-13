#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP/AD 设置视图（ADR-017）：retrieve 回显 / partialUpdate 保存 / create 连接测试。

与邮件服务器设置同构：``POST`` 即「测试」——按表单当前值（未带字段回退到已存
配置）实际 bind + 搜索并返回用户/部门计数；失败转可读 ApiResponse，不影响登录。
"""

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.utils import get_logger
from settings.serializers.ldap import LdapSettingSerializer
from settings.views.settings import BaseSettingViewSet
from system.ldap.client import LDAPException, LdapConfigError

logger = get_logger(__name__)

# 测试时允许被表单值临时覆盖（并测试后恢复）的 settings 键
_TEST_OVERRIDE_KEYS = [
    "LDAP_SERVER_URI",
    "LDAP_START_TLS",
    "LDAP_BIND_DN",
    "LDAP_CONNECT_TIMEOUT",
    "LDAP_USER_SEARCH_BASE",
    "LDAP_USER_FILTER",
    "LDAP_ATTR_USERNAME",
    "LDAP_ATTR_NICKNAME",
    "LDAP_ATTR_EMAIL",
    "LDAP_ATTR_PHONE",
    "LDAP_DEPT_ENABLED",
    "LDAP_DEPT_SEARCH_BASE",
]


class LdapServerSettingViewSet(BaseSettingViewSet):
    """LDAP 服务设置与连接测试"""

    serializer_class = LdapSettingSerializer
    category = "ldap"

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        saved = {key: getattr(settings, key) for key in _TEST_OVERRIDE_KEYS}
        try:
            for key in _TEST_OVERRIDE_KEYS:
                if key in request.data:
                    setattr(settings, key, data.get(key))
            # write_only：表单未重新输入密码时回退到已存配置（密文已随 refresh 解密在 settings）
            password = data.get("LDAP_BIND_PASSWORD") or settings.LDAP_BIND_PASSWORD
            if not settings.LDAP_SERVER_URI:
                return ApiResponse(code=1001, detail=_("Server URI is required"))
            if not settings.LDAP_USER_SEARCH_BASE:
                return ApiResponse(code=1001, detail=_("User search base is required"))

            from system.ldap.sync import test_ldap_connection

            original_password = settings.LDAP_BIND_PASSWORD
            settings.LDAP_BIND_PASSWORD = password
            try:
                result = test_ldap_connection()
            finally:
                settings.LDAP_BIND_PASSWORD = original_password
        except LdapConfigError as e:
            return ApiResponse(code=1001, detail=str(e))
        except LDAPException as e:
            logger.warning("LDAP connection test failed: %s", e)
            return ApiResponse(code=1002, detail=str(e))
        except Exception as e:  # noqa: BLE001 测试入口兜底，不给前端裸异常
            logger.warning("LDAP connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(e))
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)
        return ApiResponse(
            detail=_("Connection OK: {user_count} users, {dept_count} departments found").format(**result),
            data=result,
        )
