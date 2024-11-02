#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : rule
# author : ly_13
# date : 8/10/2024
from drf_spectacular.plumbing import build_object_type, build_basic_type, build_array_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.generics import GenericAPIView

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from settings.utils.password import get_password_check_rules


class PasswordRulesAPIView(GenericAPIView):
    """密码规则配置信息"""
    permission_classes = []

    @extend_schema(
        responses=get_default_response_schema(
            {
                'data': build_object_type(
                    properties={
                        'password_rules': build_array_type(
                            build_object_type(
                                properties={
                                    'key': build_basic_type(OpenApiTypes.STR),
                                    'value': build_basic_type(OpenApiTypes.NUMBER),
                                }
                            )
                        )
                    }
                )
            }
        )
    )
    def get(self, request):
        """获取密码规则配置"""
        return ApiResponse(data={"password_rules": get_password_check_rules(request.user)})
