#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : flower
# author : ly_13
# date : 6/29/2023
import base64
import logging

from django.conf import settings
from django.http import HttpResponse
from django.utils.translation import gettext as _
from django.views.decorators.clickjacking import xframe_options_exempt
from drf_yasg.utils import swagger_auto_schema
from proxy.views import proxy_view
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

flower_url = f'{settings.CELERY_FLOWER_HOST}:{settings.CELERY_FLOWER_PORT}'


class CeleryFlowerView(APIView):
    """celery 定时任务"""

    @swagger_auto_schema(auto_schema=None)
    @xframe_options_exempt
    def get(self, request, path):
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
            msg = _("<h3>服务不在线，请联系管理员</h3>")
            response = HttpResponse(msg)
        return response

    @swagger_auto_schema(auto_schema=None)
    @xframe_options_exempt
    def post(self, request, path):
        return self.get(request, path)
