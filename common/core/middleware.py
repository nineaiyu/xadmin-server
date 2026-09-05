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
from django.db import transaction
from django.utils.deprecation import MiddlewareMixin
from rest_framework.utils import encoders

from common.utils import get_logger
from common.utils.request import get_request_user, get_request_ip, get_request_data, get_os, \
    get_browser, get_verbose_name
from system.models import OperationLog

logger = get_logger(__name__)

# PERF-05：日志大字段截断上限，避免大请求体/大响应整包入库
MAX_LOG_FIELD = 4096
# PERF-18：操作日志脱敏字段清单
SENSITIVE_FIELDS = {'password', 'old_password', 'access', 'refresh'}


def desensitize_body(body):
    """对请求体中的敏感字段做掩码处理，返回脱敏后的 dict。"""
    if not isinstance(body, dict):
        return body
    masked = dict(body)
    for field in SENSITIVE_FIELDS:
        value = masked.get(field)
        if value:
            masked[field] = '*' * len(str(value))
    return masked


def write_operation_log(operation_log_id, info):
    """PERF-05 Step1：主键已知，用 UPDATE 替代 update_or_create（省 1 条 SELECT）。

    PERF-05 Step3：该函数通过 transaction.on_commit 在请求事务提交后执行，
    请求事务回滚时占位行一并消失，UPDATE 影响 0 行，不再产生孤儿日志写；
    事务中断也不会再连带整个请求失败。
    """
    try:
        OperationLog.objects.filter(id=operation_log_id).update(**info)
    except Exception as e:  # sqlite3 数据库因为锁表可能会导致日志记录失败
        logger.warning(f"write operation log failed. id:{operation_log_id} error:{e}")


def build_operation_log_info(request, response, request_start_time):
    """组装操作日志字段。

    所有字段在此一次性求值（包括 UA 解析与用户主键），返回值不再持有
    request / ORM 实例引用，因此可以安全地延迟到 on_commit 回调中执行。
    """
    body = desensitize_body(getattr(request, 'request_data', {}))
    # PERF-05：非 dict 响应的整包解析丢弃逻辑已删除——DRF 渲染后的 content
    # 无法可靠还原 data，解析了也不用，只会白白序列化一遍大响应
    response_data = getattr(response, 'data', None)
    if not isinstance(response_data, dict):
        response_data = {}
    user = get_request_user(request)
    request_module = getattr(request, 'request_module', '')
    if hasattr(response, 'renderer_context'):
        action_doc = getattr(response.renderer_context['view'], request.method.lower()).__doc__
        if action_doc:
            try:
                action_doc = action_doc.format(cls=request_module)
            except Exception:
                action_doc = request_module
        else:
            action_doc = request_module
    else:
        action_doc = request_module
    return {
        'module': action_doc,
        # 预取主键而非持有实例：on_commit 回调中不再延迟访问 request/ORM
        'creator_id': getattr(user, 'pk', None) if not isinstance(user, AnonymousUser) else None,
        'dept_belong_id': getattr(request.user, 'dept_id', None),
        'ipaddress': getattr(request, 'request_ip'),
        'method': request.method,
        'path': request.path,
        'body': json.dumps(body, default=str)[:MAX_LOG_FIELD] if isinstance(body, dict)
                else str(body)[:MAX_LOG_FIELD],
        'response_code': response.status_code,
        # PERF-05 Step2：UA 只解析一次（旧实现 get_os/get_browser 各跑一次重型正则）
        'system': get_os(request),
        'browser': get_browser(request),
        'status_code': response_data.get('code'),
        'request_uuid': getattr(request, 'request_uuid', None),
        'exec_time': time.time() - request_start_time,
        'response_result': json.dumps(
            {"code": response_data.get('code'), "data": response_data.get('data'),
             "detail": response_data.get('detail')}, cls=encoders.JSONEncoder, default=str,
        )[:MAX_LOG_FIELD],
    }


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
        request.request_start_time = time.time()
        logger.debug(f"request start. {request.method} {request.path} {getattr(request, 'request_data', {})}")

    def __handle_response(self, request, response):
        request_start_time = getattr(request, 'request_start_time', None)
        exec_time = time.time() - request_start_time
        if exec_time > 1:
            logger.warning(
                f"exec time {exec_time} over 1s. {request.method} {request.path} {getattr(request, 'request_data', {})}")
        # 判断有无log_id属性，使用All记录时，会出现此情况
        operation_log_id = getattr(request, self.operation_log_id, None)
        if operation_log_id is None:
            return

        info = build_operation_log_info(request, response, request_start_time)
        # PERF-05 Step3：移出请求事务，提交后再写日志
        transaction.on_commit(lambda: write_operation_log(operation_log_id, info))
        logger.debug(f"request end. {request.method} {request.path} {getattr(request, 'request_data', {})} log:{info}")
        return True

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, 'cls') and hasattr(view_func.cls, 'queryset'):
            if self.enable:
                if self.methods == 'ALL' or request.method in self.methods:
                    model, v = get_verbose_name(view_func.cls.queryset, view_func.cls)
                    if (model and request.method in self.ignores.get(model._meta.label, [])) or (
                            request.method in self.ignores.get(request.path, [])):
                        return
                    if not v:
                        v = settings.API_MODEL_MAP.get(request.path, v)
                        if not v and model:
                            v = model._meta.label
                    log = OperationLog(module=v)
                    log.save()
                    setattr(request, self.operation_log_id, log.id)
                    setattr(request, 'request_module', v)

        return

    def process_request(self, request):
        if request.path == '/api/common/api/health':
            return
        self.__handle_request(request)

    def process_response(self, request, response):
        """
        :param request:
        :param response:
        :return:
        """
        if request.path == '/api/common/api/health':
            return response
        show = False
        if self.enable:
            if self.methods == 'ALL' or request.method in self.methods:
                show = self.__handle_response(request, response)
        if not show:
            logger.debug(f" request end. {request.method} {request.path} {getattr(response, 'data', {})}")
        return response
