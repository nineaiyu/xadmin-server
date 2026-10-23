#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第三方登录配置页：Provider 列表读取（密钥掩码）/保存（密钥留空沿用）/discovery 测试。"""

from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action

from common.core.config import SysConfig
from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from identity.utils.oauth import (
    REQUIRED_KEYS,
    OAuthError,
    get_providers,
    mask_providers,
    validate_providers,
)
from identity.utils.oauth_flavors import FLAVOR_PRESETS, FLAVOR_REQUIRED_KEYS
from identity.utils.oidc import discovery_metadata
from settings.serializers.oauth import OAuthProviderSettingSerializer
from settings.views.system_config import SystemConfigSettingViewSet


class OAuthProviderSettingViewSet(SystemConfigSettingViewSet):
    """第三方登录配置"""

    serializer_class = OAuthProviderSettingSerializer
    config_keys = ("OAUTH_PROVIDERS",)

    def retrieve(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取第三方登录配置"""
        return ApiResponse(
            data={
                "providers": mask_providers(get_providers()),
                "flavors": [
                    {
                        "key": "oauth2",
                        "required_keys": list(REQUIRED_KEYS),
                        "preset": {},
                    },
                    *[
                        {
                            "key": key,
                            "required_keys": list(FLAVOR_REQUIRED_KEYS.get(key, ())),
                            "preset": preset,
                        }
                        for key, preset in FLAVOR_PRESETS.items()
                    ],
                ],
            }
        )

    def partial_update(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """更新第三方登录配置

        Provider 的 client_secret 留空 = 沿用已存密钥；显式填写才覆盖。
        """
        payload = request.data.get("providers") if isinstance(request.data, dict) else request.data
        if not isinstance(payload, list):
            return ApiResponse(code=1001, detail=_("Providers must be a list"))
        existing = {
            str(item.get("key")): item
            for item in (SysConfig.OAUTH_PROVIDERS or [])
            if isinstance(item, dict) and item.get("key")
        }
        merged = []
        for item in payload:
            if not isinstance(item, dict):
                return ApiResponse(code=1001, detail=_("Each provider must be an object"))
            provider = dict(item)
            provider.pop("client_secret_configured", None)
            if not provider.get("client_secret"):
                prior = existing.get(str(provider.get("key") or "")) or {}
                if prior.get("client_secret"):
                    provider["client_secret"] = prior["client_secret"]
            merged.append(provider)
        try:
            normalized = validate_providers(merged)
        except DjangoValidationError as exc:
            detail = exc.messages or [str(exc)]
            return ApiResponse(code=1001, detail="; ".join(str(item) for item in detail))
        self.save_key("OAUTH_PROVIDERS", normalized, request)
        return self.retrieve(request)

    @extend_schema(request=None, responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="test")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def test(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """测试 OIDC discovery

        仅标准 OIDC provider 支持；返回授权 / 换码 / JWKS 端点，便于保存前确认。
        """
        payload = request.data.get("provider") if isinstance(request.data, dict) else request.data
        if not isinstance(payload, dict):
            return ApiResponse(code=1001, detail=_("Provider must be an object"))
        provider = dict(payload)
        provider.pop("client_secret_configured", None)
        if not provider.get("client_secret"):
            prior = next(
                (
                    item
                    for item in (SysConfig.OAUTH_PROVIDERS or [])
                    if isinstance(item, dict) and str(item.get("key")) == str(provider.get("key"))
                ),
                {},
            )
            provider["client_secret"] = prior.get("client_secret") or ""
        if provider.get("flavor") != "oidc":
            return ApiResponse(code=1001, detail=_("Discovery test is only available for OIDC providers"))
        if not (provider.get("issuer") or provider.get("discovery_url")):
            return ApiResponse(code=1001, detail=_("OIDC provider requires issuer or discovery_url"))
        try:
            meta = discovery_metadata(provider)
        except OAuthError as exc:
            return ApiResponse(code=1001, detail=str(exc.detail))
        return ApiResponse(
            data={
                "issuer": meta.get("issuer") or provider.get("issuer") or "",
                "authorization_endpoint": meta.get("authorization_endpoint") or "",
                "token_endpoint": meta.get("token_endpoint") or "",
                "jwks_uri": meta.get("jwks_uri") or "",
                "userinfo_endpoint": meta.get("userinfo_endpoint") or "",
                "scopes_supported": meta.get("scopes_supported") or [],
                "claims_supported": meta.get("claims_supported") or [],
            }
        )
