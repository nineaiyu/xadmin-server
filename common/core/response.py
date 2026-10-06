#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : response
# author : ly_13
# date : 6/2/2023
import datetime

from django.utils.translation import gettext_lazy as _
from rest_framework.response import Response

from common.local import get_current_request

# 全平台 API 响应统一成功码（业务层 code，非 HTTP status；业务数据写入
# OperationLog.status_code 等场景按同值判断成功）
API_SUCCESS_CODE = 1000


class ApiResponse(Response):
    def __init__(
        self, code=API_SUCCESS_CODE, detail=None, data=None, status=None, headers=None, content_type=None, **kwargs
    ):
        dic = {
            "code": code,
            "detail": detail
            if detail
            else (_("Operation successful") if code == API_SUCCESS_CODE else _("Operation failed")),
            "requestId": str(getattr(get_current_request(), "request_uuid", "")),
            "timestamp": str(datetime.datetime.now()),
        }
        if data is not None:
            dic["data"] = data
        dic.update(kwargs)
        self._data = data
        # 对象来调用对象的绑定方法，会自动传值
        super().__init__(data=dic, status=status, headers=headers, content_type=content_type)

        # 类来调用对象的绑定方法，这个方法就是一个普通函数，有几个参数就要传几个参数
        # Response.__init__(data=dic,status=status,headers=headers,content_type=content_type)
