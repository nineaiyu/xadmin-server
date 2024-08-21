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
from rest_framework.generics import GenericAPIView
from rest_framework_simplejwt.serializers import TokenObtainSerializer

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
