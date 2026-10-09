#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : flower
# author : ly_13
# date : 6/29/2023
import base64
from typing import Any

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.utils import extend_schema
from proxy.views import proxy_view
from rest_framework.generics import GenericAPIView

from common.settings_contract import kernel_setting
from common.utils import get_logger

logger = get_logger(__name__)

FLOWER_HOST = kernel_setting("CELERY_FLOWER_HOST")
FLOWER_PORT = kernel_setting("CELERY_FLOWER_PORT")
flower_url = f"{FLOWER_HOST}:{FLOWER_PORT}"


class CeleryFlowerAPIView(GenericAPIView):
    """celery 定时任务"""

    @extend_schema(exclude=True)
    @xframe_options_exempt  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def get(self, request: Any, path: str) -> Any:
        """获取{cls}"""
        remote_url = f"http://{flower_url}/api/flower/{path}"
        try:
            basic_auth = base64.b64encode(kernel_setting("CELERY_FLOWER_AUTH").encode("utf-8")).decode("utf-8")
            response = proxy_view(request, remote_url, {"headers": {"Authorization": f"Basic {basic_auth}"}})
        except Exception as e:
            logger.warning(f"celery flower service unavailable. {e}")
            msg = _("<h3>Celery flower service unavailable. Please contact the administrator</h3>")
            response = HttpResponse(msg)
        return response

    @extend_schema(exclude=True)
    @xframe_options_exempt  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def post(self, request: Any, path: str) -> Any:
        """操作{cls}"""
        return self.get(request, path)
