#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : server
# filename : response
# author : ly_13
# date : 6/2/2023
import datetime
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework.response import Response

from common.local import get_current_request

# 全平台 API 响应统一成功码（业务层 code，非 HTTP status；业务数据写入
# OperationLog.status_code 等场景按同值判断成功）
API_SUCCESS_CODE = 1000


class ApiResponse(Response):
    """统一响应壳（``code=API_SUCCESS_CODE`` 表示业务成功）。

    契约：``response.data`` 是**信封 dict**（``code`` / ``detail`` / ``requestId`` /
    ``timestamp``，业务数据放在可选的 ``data`` 键下）。操作日志等按
    ``response.data["code"]`` 判定结果，因此不要把它替换成原始业务数据。
    """

    def __init__(
        self,
        code: Any = API_SUCCESS_CODE,
        detail: Any = None,
        data: Any = None,
        status: Any = None,
        headers: Any = None,
        content_type: Any = None,
        **kwargs: Any,
    ) -> None:
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
        # 对象来调用对象的绑定方法，会自动传值
        super().__init__(data=dic, status=status, headers=headers, content_type=content_type)

        # 类来调用对象的绑定方法，这个方法就是一个普通函数，有几个参数就要传几个参数
        # Response.__init__(data=dic,status=status,headers=headers,content_type=content_type)
