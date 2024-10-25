#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin-server
# filename : middleware
# author : ly_13
# date : 10/18/2024
import json
import re
import time
import uuid

from django.conf import settings
from django.core.exceptions import MiddlewareNotUsed
from django.http import HttpResponseForbidden

from .utils import set_current_request


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
        if not settings.DEBUG_DEV:
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
        if not settings.DEBUG_DEV:
            raise MiddlewareNotUsed

    def __call__(self, request):
        request._e_time_start = time.time()
        response = self.get_response(request)
        request._e_time_end = time.time()
        return response


class RequestMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_uuid = uuid.uuid4()
        set_current_request(request)
        response = self.get_response(request)
        return response


class RefererCheckMiddleware:
    def __init__(self, get_response):
        if not settings.REFERER_CHECK_ENABLED:
            raise MiddlewareNotUsed
        self.get_response = get_response
        self.http_pattern = re.compile('https?://')

    def check_referer(self, request):
        referer = request.META.get('HTTP_REFERER', '')
        referer = self.http_pattern.sub('', referer)
        if not referer:
            return True
        remote_host = request.get_host()
        return referer.startswith(remote_host)

    def __call__(self, request):
        match = self.check_referer(request)
        if not match:
            return HttpResponseForbidden('CSRF CHECK ERROR')
        response = self.get_response(request)
        return response
