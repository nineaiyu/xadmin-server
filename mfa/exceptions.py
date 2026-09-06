#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : exceptions
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import APIException

from mfa.const import ConfirmType


class MFAConfirmRequired(APIException):
    """敏感操作需要二次身份验证（HTTP 412），前端统一拦截后弹出验证弹窗

    响应体: {"code": 412, "type": "user_confirm_required", "confirm_type": "mfa", "detail": "..."}
    """
    status_code = 412
    default_detail = _('This action requires identity verification')

    def __init__(self, confirm_type=ConfirmType.MFA, detail=None):
        data = {
            'detail': str(detail or self.default_detail),
            'type': 'user_confirm_required',
            'confirm_type': confirm_type,
        }
        super().__init__(data)
