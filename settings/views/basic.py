#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : basic
# author : ly_13
# date : 7/31/2024

from common.utils import get_logger
from settings.serializers.basic import BasicSettingSerializer
from settings.views.settings import BaseSettingView

logger = get_logger(__file__)


class BasicSettingView(BaseSettingView):
    serializer_class = BasicSettingSerializer
    category = "basic"
