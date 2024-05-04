#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : swagger
# author : ly_13
# date : 5/4/2024
from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_yasg.generators import OpenAPISchemaGenerator
from drf_yasg.inspectors import SwaggerAutoSchema
from drf_yasg.utils import swagger_auto_schema
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from common.core.response import ApiResponse


class ApiLogin(TokenObtainPairView):
    """接口文档的登录接口"""

    @swagger_auto_schema(auto_schema=None)
    @xframe_options_exempt
    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            login(request, serializer.user)
        except Exception as e:
            return ApiResponse(detail="账号/密码错误")
        response = redirect(request.query_params.get("next", "/api-docs/swagger/"))
        return response

    @swagger_auto_schema(auto_schema=None)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        return ApiResponse(detail="请输入账号信息进行登录")


class ApiLogout(APIView):
    permission_classes = []

    @swagger_auto_schema(auto_schema=None)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("/api-docs/login/")


def get_summary(string):
    if string is not None:
        result = string.strip().replace(" ", "").split("\n")
        return result[0]


class CustomSwaggerAutoSchema(SwaggerAutoSchema):
    def get_tags(self, operation_keys=None):
        tags = super().get_tags(operation_keys)
        if "api" in tags and operation_keys:
            tags[0] = operation_keys[settings.SWAGGER_SETTINGS.get('AUTO_SCHEMA_TYPE', 2)]
        return tags

    def get_summary_and_description(self):
        summary_and_description = super().get_summary_and_description()
        summary = get_summary(self.__dict__.get('view').__doc__)
        description = summary_and_description[1]
        return summary, description

    def add_manual_parameters(self, parameters):
        """Add/replace parameters from the given list of automatically generated request parameters.

        :param list[openapi.Parameter] parameters: generated parameters
        :return: modified parameters
        :rtype: list[openapi.Parameter]
        """

        if self.overrides.get('ignore_params'):
            return
        if self.overrides.get('ignore_body_params'):
            parameters = []
        return super().add_manual_parameters(parameters)


class CustomOpenAPISchemaGenerator(OpenAPISchemaGenerator):
    def get_schema(self, request=None, public=False):
        """Generate a :class:`.Swagger` object with custom tags"""
        # request.all_fields = True  # 忽略字段权限校验
        swagger = super().get_schema(request, public)
        return swagger
