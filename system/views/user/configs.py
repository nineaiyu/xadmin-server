#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : configs
# author : ly_13
# date : 3/14/2024
from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.viewsets import GenericViewSet

from common.core.auth import auth_required
from common.core.config import SysConfig, UserConfig
from common.core.filter import OwnerUserFilter
from common.core.permission import PatScopePermission
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from system.models import UserPersonalConfig
from system.serializers.config import UserPersonalConfigSerializer


def config_response_schema():
    return get_default_response_schema({"config": build_object_type(), "auth": build_basic_type(OpenApiTypes.STR)})


# 登录前唯一可匿名读取的键（站点默认配置，供登录页/壳层渲染），其余键匿名不暴露
ANONYMOUS_READABLE_CONFIG_KEY = "WEB_SITE_CONFIG"
# 自服务可写白名单：个人偏好类键允许用户通过本接口写入个人值；
# 配额/限流等键只允许管理员在「用户配置」管理页调整，防止用户自提配额。
# 新增可自服务键时在此登记
SELF_WRITABLE_CONFIG_KEYS = frozenset({"WEB_SITE_CONFIG", "PUSH_MESSAGE_NOTICE", "PUSH_CHAT_MESSAGE"})


class ConfigsViewSet(GenericViewSet):
    """配置信息"""

    queryset = UserPersonalConfig.objects.none()
    serializer_class = UserPersonalConfigSerializer
    ordering_fields = ["created_time"]
    lookup_field = "key"
    # 匿名可读系统默认配置，故不挂 IsAuthenticated；但 PAT 凭证带 scope 时仍需校验
    # （permission_classes 被覆写会绕过默认链，此处显式挂载 scope 校验）
    permission_classes = [PatScopePermission]
    filter_backends = [OwnerUserFilter]

    @extend_schema(responses=config_response_schema())
    def retrieve(self, request, *args, **kwargs):
        """获取{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            if request.user and request.user.is_authenticated:
                config = UserConfig(request.user).get_value(value_key, ignore_access=False)
            elif value_key == ANONYMOUS_READABLE_CONFIG_KEY:
                config = SysConfig.get_value(value_key, ignore_access=False)
            else:
                config = None
            if config is not None:
                if not isinstance(config, dict):
                    config = {"value": config, "key": self.kwargs[self.lookup_field]}
                return ApiResponse(config=config, auth=f"{request.user}")
        return ApiResponse(config={}, auth=f"{request.user}")

    @extend_schema(responses=config_response_schema(), request=OpenApiRequest(build_object_type()))
    @auth_required
    def partial_update(self, request, *args, **kwargs):
        """更新{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            config = UserConfig(request.user).get_value(value_key, ignore_access=False)
            # 标量 False/0 是合法的个人值，只有「未知键/未启用个人继承」才视为空
            if config is None or (isinstance(config, dict) and not config):
                return ApiResponse(code=1001, detail=_("Unknown config key"))
            if value_key not in SELF_WRITABLE_CONFIG_KEYS:
                return ApiResponse(code=1001, detail=_("This config is managed by administrators"))
            if isinstance(config, dict):
                # 防止存储未知配置，下面代码禁止修改，如果添加字段，可以在 loadjson systemconfig.josn里面添加对应的配置值
                config.update({key: request.data.get(key, value) for key, value in config.items()})
            else:
                config = request.data
            UserConfig(request.user).set_value(value_key, config, is_active=True, access=True)
        return self.retrieve(request, *args, **kwargs)

    @extend_schema(responses=config_response_schema())
    @auth_required
    def destroy(self, request, *args, **kwargs):
        """删除{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            UserConfig(request.user).del_value(value_key)
        return self.retrieve(request, *args, **kwargs)
