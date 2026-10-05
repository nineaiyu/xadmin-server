#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LDAP/AD 设置视图：retrieve 回显 / partialUpdate 保存 / create 连接测试。

与邮件/IM 测试同口径：``POST`` 即「测试」——按表单当前值（未带字段
回退到已存配置）构造 ``LdapConfig`` 快照显式传参，实际 bind + 搜索并返回
用户/部门计数；失败转可读 ApiResponse，不影响登录。全程不 ``setattr(settings,
...)``，并发期间真实请求不可能读到测试值。
"""

from django.utils.translation import gettext_lazy as _

from common.core.response import ApiResponse
from common.utils import get_logger
from identity.ldap.client import LdapConfig, LdapConfigError, LDAPException
from settings.serializers.ldap import LdapSettingSerializer
from settings.utils.test_connection import build_test_values
from settings.views.settings import BaseSettingViewSet

logger = get_logger(__name__)

# 测试时允许被表单值覆盖（未提交键回退已存配置）的 settings 键
_TEST_VALUE_KEYS = [
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

# write_only 密文：表单重新输入（非空）才覆盖，留空沿用已存值
_SECRET_KEYS = {"LDAP_BIND_PASSWORD"}


class LdapServerSettingViewSet(BaseSettingViewSet):
    """LDAP 服务设置与连接测试"""

    serializer_class = LdapSettingSerializer
    category = "ldap"

    def create(self, request, *args, **kwargs):
        """测试{cls}"""
        serializer = self.get_serializer_class()(data=request.data)
        serializer.is_valid(raise_exception=True)

        values = build_test_values(
            serializer.validated_data,
            request.data,
            keys=_TEST_VALUE_KEYS,
            secret_keys=_SECRET_KEYS,
        )
        if not values.get("LDAP_SERVER_URI"):
            return ApiResponse(code=1001, detail=_("Server URI is required"))
        if not values.get("LDAP_USER_SEARCH_BASE"):
            return ApiResponse(code=1001, detail=_("User search base is required"))

        config = LdapConfig.from_values(values)
        try:
            from identity.ldap.sync import test_ldap_connection

            result = test_ldap_connection(config)
        except LdapConfigError as e:
            return ApiResponse(code=1001, detail=str(e))
        except LDAPException as e:
            logger.warning("LDAP connection test failed: %s", e)
            return ApiResponse(code=1002, detail=str(e))
        except Exception as e:  # noqa: BLE001 测试入口兜底，不给前端裸异常
            logger.warning("LDAP connection test unexpected error", exc_info=True)
            return ApiResponse(code=1002, detail=str(e))
        return ApiResponse(
            detail=_("Connection OK: {user_count} users, {dept_count} departments found").format(**result),
            data=result,
        )
