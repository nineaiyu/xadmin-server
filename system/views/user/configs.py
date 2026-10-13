#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : configs
# author : ly_13
# date : 3/14/2024
from typing import Any

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


def config_response_schema() -> Any:
    return get_default_response_schema({"config": build_object_type(), "auth": build_basic_type(OpenApiTypes.STR)})


# 登录前唯一可匿名读取的键（站点默认配置，供登录页/壳层渲染），其余键匿名不暴露
ANONYMOUS_READABLE_CONFIG_KEY = "WEB_SITE_CONFIG"
# 自服务可写白名单：个人偏好类键允许用户通过本接口写入个人值；
# 配额/限流等键只允许管理员在「用户配置」管理页调整，防止用户自提配额。
# 新增可自服务键时在此登记
SELF_WRITABLE_CONFIG_KEYS = frozenset({"WEB_SITE_CONFIG", "PUSH_MESSAGE_NOTICE", "PUSH_CHAT_MESSAGE"})
# 站点级外观键（全局生效）：站点标题 / Logo 品牌位 / 登录页布局写入系统配置，
# 对所有账号与登录页（匿名读取）一致；仅超管可改，其余账号提交的原值不做变更判定
# （前端整包提交时这些键带的是当前生效值），不写入个人配置行。
# 与站点级键相对，其余键保持个人偏好语义（个人行覆盖 + 回落系统值）
SITE_WIDE_CONFIG_KEYS = frozenset(
    {
        "Title",
        "ShowLogo",
        "LogoSource",
        "LogoSourceDark",
        "LogoShowText",
        "LogoFit",
        "LogoMode",
        "FullLogoHeight",
        "AuthPageLayout",
    }
)


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

    def _site_wide_values(self) -> dict[str, Any]:
        """系统级站点配置（WEB_SITE_CONFIG）当前值：站点级外观键的权威来源。"""
        value = SysConfig.get_value(ANONYMOUS_READABLE_CONFIG_KEY)
        return dict(value) if isinstance(value, dict) else {}

    def _split_site_wide_updates(
        self, request: Any, config: dict[str, Any], site_values: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        """按站点级 / 个人级分流整包提交。

        站点级键以硬编码白名单为准（不依赖系统行既有键集合——历史种子/老库
        可能缺键，如 Title），变更判定以系统当前值为基准：
        - 与系统值相同（前端整包提交的当前值）→ 未变更，跳过；
        - 系统行缺该键 → 不构成越权判定：超管写入（自愈补齐），其余账号忽略；
        - 空值视为「前端未持有该字段」（服务端未下发的回落态），不判越权；
        - 其余变化仅超管可写，普通账号整单拒绝（1001）。
        个人级键保持「只更新既有键」语义（防未知键入库）。
        """
        site_updates: dict[str, Any] = {}
        personal: dict[str, Any] = {}
        denied = False
        for key in sorted(SITE_WIDE_CONFIG_KEYS):
            if key not in request.data:
                continue
            new_value = request.data[key]
            current = site_values.get(key)
            if current is None:
                if request.user.is_superuser:
                    site_updates[key] = new_value
                continue
            if new_value == current:
                continue
            if not request.user.is_superuser:
                if new_value in ("", None):
                    continue
                denied = True
                continue
            site_updates[key] = new_value
        for key, value in config.items():
            if key in SITE_WIDE_CONFIG_KEYS:
                # 站点级键不沉淀个人行：读取回落系统值，系统级变更对全员即时生效
                continue
            personal[key] = request.data.get(key, value)
        return site_updates, personal, denied

    @extend_schema(responses=config_response_schema())
    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
                elif value_key == ANONYMOUS_READABLE_CONFIG_KEY:
                    # 站点级键以系统配置为唯一权威：个人行里的历史值（站点级字段
                    # 曾是个人偏好）不覆盖；个人行缺席（保存时已剔除）也补齐，
                    # 保证前端始终拿到完整的站点级字段
                    config.update(
                        {key: value for key, value in self._site_wide_values().items() if key in SITE_WIDE_CONFIG_KEYS}
                    )
                return ApiResponse(config=config, auth=f"{request.user}")
        return ApiResponse(config={}, auth=f"{request.user}")

    @extend_schema(responses=config_response_schema(), request=OpenApiRequest(build_object_type()))
    @auth_required
    def partial_update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
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
                # 站点配置对象按站点级 / 个人级分流：站点级键写系统配置（全局生效），
                # 其余键写个人配置（个人偏好）
                if value_key == ANONYMOUS_READABLE_CONFIG_KEY:
                    site_values = self._site_wide_values()
                    site_updates, config, denied = self._split_site_wide_updates(request, config, site_values)
                    if denied:
                        return ApiResponse(
                            code=1001,
                            detail=_("Only administrators can change site-wide appearance settings"),
                        )
                    if site_updates:
                        # access=True 保持「登录前可读」：系统行不存在时新建，
                        # 模型默认 access=False 会让匿名读取被拒（登录页拿不到配置）
                        SysConfig.set_value(
                            ANONYMOUS_READABLE_CONFIG_KEY,
                            {**site_values, **site_updates},
                            is_active=True,
                            access=True,
                        )
                else:
                    config.update({key: request.data.get(key, value) for key, value in config.items()})
            else:
                config = request.data
            UserConfig(request.user).set_value(value_key, config, is_active=True, access=True)
        return self.retrieve(request, *args, **kwargs)

    @extend_schema(responses=config_response_schema())
    @auth_required
    def destroy(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """删除{cls}"""
        value_key = self.kwargs[self.lookup_field]
        if value_key:
            UserConfig(request.user).del_value(value_key)
        return self.retrieve(request, *args, **kwargs)
