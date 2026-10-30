#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : capabilities
from typing import Any

from drf_spectacular.plumbing import build_array_type, build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from common.core.response import ApiResponse
from common.core.throttle import LoginThrottle
from common.swagger.utils import get_default_response_schema
from identity.services.auth_capabilities import build_login_capabilities


def _way_schema(*extra: tuple[str, Any]) -> dict[str, Any]:
    """方式级能力对象:enabled + 各通道开关(或附加字段)。"""
    properties: dict[str, Any] = {
        "enabled": build_basic_type(OpenApiTypes.BOOL),
    }
    for name, field_type in extra:
        properties[name] = field_type
    return build_object_type(properties=properties)


class LoginCapabilitiesAPIView(GenericAPIView):
    """登录页能力清单

    登录页外壳一次性获取:各登录方式入口的可用性(账密/验证码/注册/重置/
    第三方)。登录前置须匿名可达,显式放开认证与权限链(与 OAuthProviders
    同口径);只读开关投影,无敏感信息。
    """

    permission_classes = [AllowAny]
    throttle_classes = [LoginThrottle]

    @extend_schema(
        responses=get_default_response_schema(
            {
                "data": build_object_type(
                    properties={
                        "access": build_basic_type(OpenApiTypes.BOOL),
                        "lifetime": build_basic_type(OpenApiTypes.NUMBER),
                        "basic": _way_schema(
                            ("captcha", build_basic_type(OpenApiTypes.BOOL)),
                            ("captcha_types", build_array_type(build_basic_type(OpenApiTypes.STR) or {})),
                            ("captcha_image_fallback", build_basic_type(OpenApiTypes.BOOL)),
                            ("captcha_flow", build_basic_type(OpenApiTypes.STR)),
                            ("token", build_basic_type(OpenApiTypes.BOOL)),
                            ("encrypted", build_basic_type(OpenApiTypes.BOOL)),
                        ),
                        "code": _way_schema(
                            ("email", build_basic_type(OpenApiTypes.BOOL)),
                            ("sms", build_basic_type(OpenApiTypes.BOOL)),
                        ),
                        "register": _way_schema(
                            ("email", build_basic_type(OpenApiTypes.BOOL)),
                            ("sms", build_basic_type(OpenApiTypes.BOOL)),
                        ),
                        "reset": _way_schema(
                            ("email", build_basic_type(OpenApiTypes.BOOL)),
                            ("sms", build_basic_type(OpenApiTypes.BOOL)),
                        ),
                        "oauth": build_array_type(
                            build_object_type(
                                properties={
                                    "key": build_basic_type(OpenApiTypes.STR),
                                    "name": build_basic_type(OpenApiTypes.STR),
                                    "flavor": build_basic_type(OpenApiTypes.STR),
                                }
                            )
                        ),
                    }
                )
            }
        ),
    )
    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """获取登录页能力清单"""
        return ApiResponse(data=build_login_capabilities())
