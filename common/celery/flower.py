#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : flower
# author : ly_13
# date : 6/29/2023
import base64

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_spectacular.utils import extend_schema
from proxy.views import proxy_view
from rest_framework.generics import GenericAPIView

from common.utils import get_logger

logger = get_logger(__name__)

flower_url = f'{settings.CELERY_FLOWER_HOST}:{settings.CELERY_FLOWER_PORT}'


class CeleryFlowerAPIView(GenericAPIView):
    """celery 定时任务"""

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def get(self, request, path):
        """获取{cls}"""
        remote_url = 'http://{}/api/flower/{}'.format(flower_url, path)
        try:
            basic_auth = base64.b64encode(settings.CELERY_FLOWER_AUTH.encode('utf-8')).decode('utf-8')
            response = proxy_view(request, remote_url, {
                'headers': {
                    'Authorization': f"Basic {basic_auth}"
                }
            })
        except Exception as e:
            logger.warning(f"celery flower service unavailable. {e}")
            msg = _("<h3>Celery flower service unavailable. Please contact the administrator</h3>")
            response = HttpResponse(msg)
        return response

    @extend_schema(exclude=True)
    @xframe_options_exempt
    def post(self, request, path):
        """操作{cls}"""
        return self.get(request, path)
