#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : rule
# author : ly_13
# date : 8/10/2024
from rest_framework.views import APIView

from common.core.response import ApiResponse
from settings.utils.password import get_password_check_rules


class PasswordRulesView(APIView):
    permission_classes = []

    def get(self, request):
        return ApiResponse(data={"password_rules": get_password_check_rules(request.user)})
