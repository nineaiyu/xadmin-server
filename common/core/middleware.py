#!/usr/bin/env python
# -*- coding:utf-8 -*-
# project : xadmin_server
# filename : middleware
# author : ly_13
# date : 6/27/2023

import logging
import time

from asgiref.sync import iscoroutinefunction, markcoroutinefunction, sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils.deprecation import MiddlewareMixin

from common.contracts import OperationLog, maybe_alert_sensitive_operation
from common.core.config import SysConfig
from common.utils import get_logger
from common.utils.request import (
    get_request_data,
    get_request_ip,
    get_verbose_name,
)

logger = get_logger(__name__)

#: CSP 响应头名（与 django-csp 4.x 一致）
CSP_HEADER = "Content-Security-Policy"
CSP_HEADER_REPORT_ONLY = "Content-Security-Policy-Report-Only"


class CSPModeMiddleware:
    """CSP 运行期模式开关（ADR 落地 S3）：disabled / report-only / enforce。

    django-csp 负责按 settings 生成策略（`CONTENT_SECURITY_POLICY[_REPORT_ONLY]`），
    本中间件只做「按系统配置决定最终下发哪个头 + 注入 report-uri」：

    - `CSP_MODE=disabled`：两个头都移除；
    - `CSP_MODE=report-only`（默认）：只保留 Report-Only（观察期，不拦截）；
    - `CSP_MODE=enforce`：把 Report-Only 改写为强制头。

    挂载顺序：必须排在 `csp.middleware.CSPMiddleware` **之前**——响应阶段自内向外，
    本中间件需要在其之后执行才能改写到已生成的策略头。
    """

    sync_capable = True
    async_capable = True  # ADR-078 D1：双模；SysConfig 读（L1→Redis→DB）经 sync_to_async 包裹

    def __init__(self, get_response):
        self.get_response = get_response
        self.async_mode = iscoroutinefunction(self.get_response)
        if self.async_mode:
            markcoroutinefunction(self)

    @staticmethod
    def _read_csp_config() -> tuple[str, str]:
        """响应期配置读合并为一次 sync→async 交接（两次属性访问可能触 Redis/DB）。"""
        mode = str(getattr(SysConfig, "CSP_MODE", None) or "report-only").strip().lower()
        report_uri = str(getattr(SysConfig, "CSP_REPORT_URI", None) or "").strip()
        return mode, report_uri

    def __call__(self, request):
        if self.async_mode:
            return self.__acall__(request)
        response = self.get_response(request)
        mode, report_uri = self._read_csp_config()
        return self._apply(response, mode, report_uri)

    async def __acall__(self, request):
        response = await self.get_response(request)
        mode, report_uri = await sync_to_async(self._read_csp_config, thread_sensitive=True)()
        return self._apply(response, mode, report_uri)

    @staticmethod
    def _apply(response, mode, report_uri):
        if mode == "disabled":
            response.headers.pop(CSP_HEADER, None)
            response.headers.pop(CSP_HEADER_REPORT_ONLY, None)
            return response

        if mode == "enforce":
            # 把观察策略提升为强制策略（django-csp 两套 settings 的指令一致）
            if CSP_HEADER_REPORT_ONLY in response.headers:
                response.headers[CSP_HEADER] = response.headers[CSP_HEADER_REPORT_ONLY]
                response.headers.pop(CSP_HEADER_REPORT_ONLY, None)
        else:
            # 观察期：只保留 Report-Only（django-csp 默认也会下发强制头，这里移除）
            response.headers.pop(CSP_HEADER, None)

        # report-uri 运行期可配（默认空 = 不下发）：观察期把违规上报到本服务端点
        header_name = CSP_HEADER if mode == "enforce" else CSP_HEADER_REPORT_ONLY
        policy = response.headers.get(header_name, "")
        if report_uri and policy and "report-uri" not in policy:
            response.headers[header_name] = f"{policy}; report-uri {report_uri}"
        return response


class ApiLoggingMiddleware(MiddlewareMixin):
    def __init__(self, get_response=None):
        super().__init__(get_response)
        self.enable = getattr(settings, "API_LOG_ENABLE", None) or False
        self.methods = getattr(settings, "API_LOG_METHODS", None) or set()
        self.ignores = getattr(settings, "API_LOG_IGNORE", None) or {}
        self.operation_log_id = "__operation_log_id"

    def _should_log(self, request, view_func) -> bool:
        """请求是否落操作日志：``API_LOG_METHODS`` 命中，或敏感 GET action 白名单命中。

        O8-5：``API_LOG_METHODS`` 默认不含 GET（列表/详情读请求全落库即日志洪水），
        导出/下载等敏感读取由视图侧 ``SENSITIVE_GET_ACTIONS`` 单列声明（经
        ``sensitive_get_actions`` 沿 MRO 取并集）；是否豁免仍由调用方的
        ``API_LOG_IGNORE``（模型 / 路径维度）与 ``API_LOG_ENABLE`` 决定。
        """
        if self.methods == "ALL" or request.method in self.methods:
            return True
        if request.method != "GET":
            return False
        actions = sensitive_get_actions(view_func.cls)
        # view.actions 为 DRF as_view 挂载的 {HTTP method(小写): action 方法名} 映射
        return bool(actions) and getattr(view_func, "actions", {}).get("get") in actions

    @classmethod
    def __handle_request(cls, request):
        request.request_ip = get_request_ip(request)
        request.request_data = get_request_data(request)
        request.request_start_time = time.time()
        # DEBUG 正文同样脱敏 + 截断（与操作日志 / 慢请求日志同口径）：明文
        # token / password 落日志文件等同泄露凭证（ADR-072）。
        # isEnabledFor 守卫：f-string 会先求值再过滤级别，正文预览要做一遍脱敏 +
        # json.dumps，DEBUG 关闭时白算（先例 common/cache/storage.py:23-29）
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"request start. {request.method} {request.path} {log_body_preview(request.request_data)}")

    def __handle_response(self, request, response):
        request_start_time = getattr(request, "request_start_time", time.time())
        exec_time = time.time() - request_start_time
        # 慢请求阈值走系统配置（默认 1.0s），与监控面板 slow 接口同口径
        threshold = SysConfig.SLOW_REQUEST_THRESHOLD
        if exec_time > threshold:
            # 请求体必须脱敏（与 OperationLog 同口径）：慢请求日志保留期长，
            # 明文 token/password/code 落日志文件等同泄露凭证
            logger.warning(
                f"exec time {exec_time} over {threshold}s. {request.method} {request.path} "
                f"{log_body_preview(request.request_data)} "
                f"request_id:{getattr(request, 'request_uuid', None)}"
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
                maybe_alert_sensitive_operation(info)
            except Exception:
                # 告警链路异常不影响响应，也不影响日志本身
                logger.warning("sensitive operation alert failed", exc_info=True)

        transaction.on_commit(_after_commit)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                f"request end. {request.method} {request.path} {log_body_preview(request.request_data)} log:{info}"
            )
        return True

    def process_view(self, request, view_func, view_args, view_kwargs):
        if hasattr(view_func, "cls") and hasattr(view_func.cls, "queryset"):
            if self.enable and self._should_log(request, view_func):
                if not (self.methods == "ALL" or request.method in self.methods):
                    # 敏感 GET 白名单路径（O8-5）：标记给 process_response 放行响应装配
                    request.operation_log_get_audit = True
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
                    object_pk=str(object_pk) if (object_pk := view_kwargs.get("pk") or view_kwargs.get("id")) else None,
                )
                log.save()
                setattr(request, self.operation_log_id, log.id)
                request.request_module = v

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
            if (
                self.methods == "ALL"
                or request.method in self.methods
                # 敏感 GET 白名单路径（O8-5）：process_view 已建占位行，响应装配照走
                or getattr(request, "operation_log_get_audit", False)
            ):
                show = self.__handle_response(request, response)
        # isEnabledFor 守卫（P1-2 口径）：f-string 会把整个 response.data（分页 100 行 ×
        # 20 列量级）先 repr 成字符串再被级别过滤丢弃，未开操作日志的请求每请求白付一次
        if not show and logger.isEnabledFor(logging.DEBUG):
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


from common.core.oplog_recorder import (  # noqa: F401  (日志辅助拆至 oplog_recorder，此处导入并再导出)
    HEALTH_CHECK_PATH,
    MAX_LOG_FIELD,
    OPERATION_LOG_MODULE_MAX,
    SENSITIVE_FIELDS,
    _log_field_limit,
    build_operation_log_info,
    desensitize_body,
    desensitize_payload,
    log_body_preview,
    resolve_auth_identity,
    sensitive_get_actions,
    write_operation_log,
)
