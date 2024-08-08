#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : verify
# author : ly_13
# date : 8/7/2024

from common.utils import get_logger
from settings.serializers.verify import VerifyCodeSettingSerializer, CaptchaSettingSerializer
from settings.views.settings import BaseSettingView

logger = get_logger(__file__)


class VerifyCodeSettingView(BaseSettingView):
    serializer_class = VerifyCodeSettingSerializer
    category = "verify"


class CaptchaSettingView(BaseSettingView):
    serializer_class = CaptchaSettingSerializer
    category = "captcha"
