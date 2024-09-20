#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : middleware
# author : ly_13
# date : 6/27/2023

import json
import time

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import MiddlewareNotUsed
from django.utils.deprecation import MiddlewareMixin
from rest_framework.utils import encoders

from common.utils.request import get_request_user, get_request_ip, get_request_data, get_request_path, get_os, \
    get_browser, get_verbose_name
from system.models import OperationLog


class ApiLoggingMiddleware(MiddlewareMixin):

    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.enable = getattr(settings, 'API_LOG_ENABLE', None) or False
        self.methods = getattr(settings, 'API_LOG_METHODS', None) or set()
        self.ignores = getattr(settings, 'API_LOG_IGNORE', None) or {}
        self.operation_log_id = '__operation_log_id'

    @classmethod
    def __handle_request(cls, request):
        request.request_ip = get_request_ip(request)
        request.request_data = get_request_data(request)
        request.request_path = get_request_path(request)

    def __handle_response(self, request, response):
        # 判断有无log_id属性，使用All记录时，会出现此情况
        operation_log_id = getattr(request, self.operation_log_id, None)
        if operation_log_id is None:
            return

        body = getattr(request, 'request_data', {})
        # 请求含有password则用*替换掉(暂时先用于所有接口的password请求参数)
        if isinstance(body, dict) and body.get('password', ''):
            body['password'] = '*' * len(body['password'])
        if not hasattr(response, 'data') or not isinstance(response.data, dict):
            response.data = {}
        try:
            if not response.data and response.content:
                content = json.loads(response.content.decode().replace('\\', ''))
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
            'body': json.dumps(body) if isinstance(body, dict) else body,
            'response_code': response.status_code,
            'system': get_os(request),
            'browser': get_browser(request),
            'status_code': response.data.get('code'),
            'response_result': json.dumps({"code": response.data.get('code'), "data": response.data.get('data'),
                                           "detail": response.data.get('detail')}, cls=encoders.JSONEncoder),
        }
        try:
            OperationLog.objects.update_or_create(defaults=info, id=operation_log_id)
        except Exception:
            pass

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, 'cls') and hasattr(view_func.cls, 'queryset'):
            if self.enable:
                if self.methods == 'ALL' or request.method in self.methods:
                    model, v = get_verbose_name(view_func.cls.queryset)
                    if model and request.method in self.ignores.get(model._meta.label, []):
                        return
                    if not v:
                        v = settings.API_MODEL_MAP.get(request.request_path, v)
                    log = OperationLog(module=v)
                    log.save()
                    setattr(request, self.operation_log_id, log.id)

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


class SQLCountMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG:
            raise MiddlewareNotUsed

    def __call__(self, request):
        from django.db import connection
        response = self.get_response(request)
        response['X-SQL-COUNT'] = len(connection.queries) - 2
        return response


class StartMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG:
            raise MiddlewareNotUsed

    def __call__(self, request):
        request._s_time_start = time.time()
        response = self.get_response(request)
        request._s_time_end = time.time()
        if request.path == '/api/common/api/health':
            data = response.data
            data['pre_middleware_time'] = request._e_time_start - request._s_time_start
            data['api_time'] = request._e_time_end - request._e_time_start
            data['post_middleware_time'] = request._s_time_end - request._e_time_end
            response.content = json.dumps(data)
            response.headers['Content-Length'] = str(len(response.content))
            response.headers['Content-Type'] = "application/json"
        return response


class EndMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        if not settings.DEBUG:
            raise MiddlewareNotUsed

    def __call__(self, request):
        request._e_time_start = time.time()
        response = self.get_response(request)
        request._e_time_end = time.time()
        return response
