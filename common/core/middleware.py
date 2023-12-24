#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : middleware
# author : ly_13
# date : 6/27/2023

import json

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.utils.deprecation import MiddlewareMixin

from common.utils.request import get_request_user, get_request_ip, get_request_data, get_request_path, get_os, \
    get_browser, get_verbose_name
from system.models import OperationLog


class ApiLoggingMiddleware(MiddlewareMixin):

    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.enable = getattr(settings, 'API_LOG_ENABLE', None) or False
        self.methods = getattr(settings, 'API_LOG_METHODS', None) or set()
        self.operation_log_id = None

    @classmethod
    def __handle_request(cls, request):
        request.request_ip = get_request_ip(request)
        request.request_data = get_request_data(request)
        request.request_path = get_request_path(request)

    def __handle_response(self, request, response):
        body = getattr(request, 'request_data', {})
        # 请求含有password则用*替换掉(暂时先用于所有接口的password请求参数)
        if isinstance(body, dict) and body.get('password', ''):
            body['password'] = '*' * len(body['password'])
        if not hasattr(response, 'data') or not isinstance(response.data, dict):
            response.data = {}
        try:
            if not response.data and response.content:
                content = json.loads(response.content.decode())
                response.data = content if isinstance(content, dict) else {}
        except Exception:
            return
        user = get_request_user(request)
        info = {
            'creator': user if not isinstance(user, AnonymousUser) else None,
            'dept_belong_id': getattr(request.user, 'dept_id', None),
            'ipaddress': getattr(request, 'request_ip'),
            'method': request.method,
            'path': request.request_path,
            'body': body,
            'response_code': response.status_code,
            'system': get_os(request),
            'browser': get_browser(request),
            'status_code': response.data.get('code'),
            'response_result': {"code": response.data.get('code'), "data": response.data.get('data'),
                                "detail": response.data.get('detail')},
        }
        operation_log, creat = OperationLog.objects.update_or_create(defaults=info, id=self.operation_log_id)
        module_name = settings.API_MODEL_MAP.get(request.request_path, None)
        if module_name:
            operation_log.module = module_name
            operation_log.save(update_fields=['module'])

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, 'cls') and hasattr(view_func.cls, 'queryset'):
            if self.enable:
                if self.methods == 'ALL' or request.method in self.methods:
                    log = OperationLog(module=get_verbose_name(view_func.cls.queryset))
                    log.save()
                    self.operation_log_id = log.id

        return

    def process_request(self, request):
        self.__handle_request(request)

    def process_response(self, request, response):
        """
        :param request:
        :param response:
        :return:
        """
        if self.enable:
            if self.methods == 'ALL' or request.method in self.methods:
                self.__handle_response(request, response)
        return response
