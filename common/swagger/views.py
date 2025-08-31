#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : views
# author : ly_13
# date : 8/12/2024

from django.contrib.auth import login, logout
from django.shortcuts import redirect
from django.utils.translation import gettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.utils import extend_schema
from drf_spectacular.views import (
    SpectacularSwaggerView, SpectacularRedocView,
    SpectacularYAMLAPIView, SpectacularJSONAPIView
)
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.serializers import TokenObtainSerializer

from common.base.magic import cache_response
from common.core.response import ApiResponse


class ApiLogin(GenericAPIView):
    """接口文档的登录接口"""
    permission_classes = ()
    serializer_class = TokenObtainSerializer

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def post(self, request, *args, **kwargs):

        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
            login(request, serializer.user)
        except Exception as e:
            return ApiResponse(detail=_("Incorrect username/password"))
        response = redirect(request.query_params.get("next", "/api-docs/swagger/"))
        return response

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect(to="/api-docs/swagger/")
        return ApiResponse(detail=_("Please enter your account information to log in"))


class ApiLogout(GenericAPIView):
    permission_classes = []

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("/api-docs/login/")


class SchemaMixin:
    @xframe_options_exempt
    @cache_response(timeout=60 * 5, key_func='get_cache_key')
    def get(self, *args, **kwargs):
        return super().get(*args, **kwargs)

    def get_cache_key(self, view_instance, view_method, request, args, kwargs):
        func_name = f'{view_instance.__class__.__name__}_{view_method.__name__}'
        return f"{func_name}_{request.user.pk}"


@extend_schema(exclude=True)
class JsonApi(SchemaMixin, SpectacularJSONAPIView):
    pass


@extend_schema(exclude=True)
class YamlApi(SchemaMixin, SpectacularYAMLAPIView):
    pass


@extend_schema(exclude=True)
class SwaggerUI(SchemaMixin, SpectacularSwaggerView):
    pass


@extend_schema(exclude=True)
class Redoc(SchemaMixin, SpectacularRedocView):
    pass
