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

from common.core.config import SysConfig
from common.core.utils import get_doc_first_line
from common.utils import get_logger
from common.utils.request import (
    get_request_user,
    get_request_ip,
    get_request_data,
    get_os,
    get_browser,
    get_verbose_name,
)
from system.services import OperationLog

logger = get_logger(__name__)

# 日志大字段截断上限，避免大请求体/大响应整包入库
MAX_LOG_FIELD = 4096
# 操作日志脱敏字段清单
# code：二次验证提交体里的登录密码/动态验证码（POST /api/mfa/confirm 等），
# 严禁明文落日志
SENSITIVE_FIELDS = {"password", "old_password", "access", "refresh", "code"}
# module 列的防御性截断：视图 docstring/模型标签超长时按字段上限截断，
# 避免写日志失败放大成整个请求 500（mfa confirm 曾因此全挂）
OPERATION_LOG_MODULE_MAX = OperationLog._meta.get_field("module").max_length
# 健康检查端点：探针高频请求，中间件请求/响应阶段直接跳过（不落操作日志）
HEALTH_CHECK_PATH = "/api/common/api/health"


def desensitize_body(body):
    """对请求体中的敏感字段做掩码处理，返回脱敏后的 dict。"""
    if not isinstance(body, dict):
        return body
    masked = dict(body)
    for field in SENSITIVE_FIELDS:
        value = masked.get(field)
        if value:
            masked[field] = "*" * len(str(value))
    return masked


def write_operation_log(operation_log_id, info):
    """主键已知，用 UPDATE 替代 update_or_create（省 1 条 SELECT）。

    该函数通过 transaction.on_commit 在请求事务提交后执行，
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
    body = desensitize_body(getattr(request, "request_data", {}))
    # 非 dict 响应的整包解析丢弃逻辑已删除——DRF 渲染后的 content
    # 无法可靠还原 data，解析了也不用，只会白白序列化一遍大响应
    response_data = getattr(response, "data", None)
    if response_data is None:
        # 响应缓存命中时返回的是普通 HttpResponse（无 .data）：此时才退化为解析已渲染的
        # JSON 响应体，否则被缓存接口的操作日志会丢失 status_code / response_result。
        # 无 content 的响应对象（含测试替身）不解析，保持"非 dict 不解析"的既有语义。
        content = getattr(response, "content", None)
        content_type = ""
        if hasattr(response, "get"):
            content_type = str(response.get("Content-Type", ""))
        if content and content_type.startswith("application/json"):
            try:
                response_data = json.loads(content)
            except Exception:
                response_data = None
    if not isinstance(response_data, dict):
        response_data = {}
    user = get_request_user(request)
    request_module = getattr(request, "request_module", "")
    if hasattr(response, "renderer_context"):
        # 视图实例可能没有与 HTTP 动词同名的方法（如 ViewSet 的 405/detail 误配路径），
        # getattr 必须带兜底，否则操作日志会把业务响应改写成 500
        view = response.renderer_context.get("view")
        handler = getattr(view, request.method.lower(), None) if view else None
        # 视图方法 docstring 只取首行：多行长说明（如 412 交互流程）会撑爆 module 列且不可读
        action_doc = get_doc_first_line(getattr(handler, "__doc__", None)) if handler else ""
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
        "module": action_doc[:OPERATION_LOG_MODULE_MAX] if action_doc else action_doc,
        # 预取主键而非持有实例：on_commit 回调中不再延迟访问 request/ORM
        "creator_id": getattr(user, "pk", None) if not isinstance(user, AnonymousUser) else None,
        "dept_belong_id": getattr(request.user, "dept_id", None),
        "ipaddress": getattr(request, "request_ip"),
        "method": request.method,
        "path": request.path,
        "body": json.dumps(body, default=str)[:MAX_LOG_FIELD] if isinstance(body, dict) else str(body)[:MAX_LOG_FIELD],
        "response_code": response.status_code,
        # Step2：UA 只解析一次（旧实现 get_os/get_browser 各跑一次重型正则）
        "system": get_os(request),
        "browser": get_browser(request),
        "status_code": response_data.get("code"),
        "request_uuid": getattr(request, "request_uuid", None),
        "exec_time": time.time() - request_start_time,
        # 字段级变更 diff（AUDIT_DIFF_MODELS 白名单模型的 update 路径由视图集挂载）
        "changes": json.dumps(changes, cls=encoders.JSONEncoder, default=str)[:MAX_LOG_FIELD]
        if (changes := getattr(request, "operation_log_changes", None))
        else None,
        "response_result": json.dumps(
            {
                "code": response_data.get("code"),
                "data": response_data.get("data"),
                "detail": response_data.get("detail"),
            },
            cls=encoders.JSONEncoder,
            default=str,
        )[:MAX_LOG_FIELD],
    }


class ApiLoggingMiddleware(MiddlewareMixin):
    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.enable = getattr(settings, "API_LOG_ENABLE", None) or False
        self.methods = getattr(settings, "API_LOG_METHODS", None) or set()
        self.ignores = getattr(settings, "API_LOG_IGNORE", None) or {}
        self.operation_log_id = "__operation_log_id"

    @classmethod
    def __handle_request(cls, request):
        request.request_ip = get_request_ip(request)
        request.request_data = get_request_data(request)
        request.request_start_time = time.time()
        logger.debug(f"request start. {request.method} {request.path} {getattr(request, 'request_data', {})}")

    def __handle_response(self, request, response):
        request_start_time = getattr(request, "request_start_time", time.time())
        exec_time = time.time() - request_start_time
        # 慢请求阈值走系统配置（默认 1.0s），与监控面板 slow 接口同口径
        threshold = SysConfig.SLOW_REQUEST_THRESHOLD
        if exec_time > threshold:
            logger.warning(
                f"exec time {exec_time} over {threshold}s. {request.method} {request.path} "
                f"{getattr(request, 'request_data', {})} request_id:{getattr(request, 'request_uuid', None)}"
            )
        # 判断有无log_id属性，使用All记录时，会出现此情况
        operation_log_id = getattr(request, self.operation_log_id, None)
        if operation_log_id is None:
            return None

        info = build_operation_log_info(request, response, request_start_time)

        def _after_commit():
            # Step3：移出请求事务，提交后再写日志；日志落库后做敏感操作告警判定
            write_operation_log(operation_log_id, info)
            try:
                from system.notifications import maybe_alert_sensitive_operation

                maybe_alert_sensitive_operation(info)
            except Exception:
                # 告警链路异常不影响响应，也不影响日志本身
                logger.warning("sensitive operation alert failed", exc_info=True)

        transaction.on_commit(_after_commit)
        logger.debug(f"request end. {request.method} {request.path} {getattr(request, 'request_data', {})} log:{info}")
        return True

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, "cls") and hasattr(view_func.cls, "queryset"):
            if self.enable:
                if self.methods == "ALL" or request.method in self.methods:
                    model, v = get_verbose_name(view_func.cls.queryset, view_func.cls)
                    if (model and request.method in self.ignores.get(model._meta.label, [])) or (
                        request.method in self.ignores.get(request.path, [])
                    ):
                        return
                    if not v:
                        v = settings.API_MODEL_MAP.get(request.path, v)
                        if not v and model:
                            v = model._meta.label
                    log = OperationLog(
                        module=str(v)[:OPERATION_LOG_MODULE_MAX],
                        # 行级变更历史：detail 路由从 URL kwargs 提取对象主键（pk 兜底 id），
                        # list/create 等无 pk 路由留空；转 str 兼容 UUID/整型主键
                        object_pk=str(object_pk)
                        if (object_pk := view_kwargs.get("pk") or view_kwargs.get("id"))
                        else None,
                    )
                    log.save()
                    setattr(request, self.operation_log_id, log.id)
                    setattr(request, "request_module", v)

        return

    def process_request(self, request):
        if request.path == HEALTH_CHECK_PATH:
            return
        self.__handle_request(request)

    def process_response(self, request, response):
        """
        :param request:
        :param response:
        :return:
        """
        if request.path == HEALTH_CHECK_PATH:
            return response
        show = False
        if self.enable:
            if self.methods == "ALL" or request.method in self.methods:
                show = self.__handle_response(request, response)
        if not show:
            logger.debug(f" request end. {request.method} {request.path} {getattr(response, 'data', {})}")
        return response


class MetricsMiddleware(MiddlewareMixin):
    """HTTP 指标采集（仅在 ``METRICS_ENABLED=true`` 时由 settings 挂载）。

    默认不挂载：关闭时零开销，也不会因指标依赖缺失影响请求链路。
    """

    def process_request(self, request):
        request._metrics_start_time = time.time()

    def process_response(self, request, response):
        start = getattr(request, "_metrics_start_time", None)
        if start is None:
            return response
        from common.metrics import record_http_request

        match = getattr(request, "resolver_match", None)
        view_name = getattr(match, "view_name", "") if match is not None else ""
        record_http_request(request.method, view_name, response.status_code, time.time() - start)
        return response
