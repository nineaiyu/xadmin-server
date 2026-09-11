#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CSP 违规上报端点（S3 观察期用）。

浏览器按策略里的 `report-uri` 把违规 POST 过来（无登录态，鉴权类不适用）。
本端点只做两件事：
1. 解析（`application/csp-report` / `application/reports+json` 两种信封）并打 WARNING 日志，
   供观察期在 `data/logs/server.log` 里统计哪些指令会被真实触发；
2. 按「指令 + 文档路径」做 60s 日志节流，避免单个页面刷爆日志。

刻意不落库：观察期结束（切 enforce）后该端点可关（`CSP_REPORT_URI` 置空即不下发）。
"""

import hashlib
import json
import logging

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from common.core.response import ApiResponse

logger = logging.getLogger("xadmin")

CSP_REPORT_HEADER = "X-CSP-Report"
CSP_REPORT_LOG_THROTTLE_SECONDS = 60
#: 单次上报体上限（浏览器上报体很小，超限直接截断，防超大 body 攻击）
CSP_REPORT_MAX_BYTES = 8192


def _extract_violation(payload: dict) -> dict:
    """兼容两种上报信封：`{"csp-report": {...}}`（CSP2）与数组式 reports（CSP3）。"""
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get("csp-report"), dict):
        return payload["csp-report"]
    reports = payload.get("reports") or payload.get("body")
    if isinstance(reports, list) and reports and isinstance(reports[0], dict):
        return reports[0].get("body") or reports[0]
    return payload


class CSPReportAPIView(GenericAPIView):
    """CSP 违规上报（无鉴权：浏览器不带登录态）"""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def post(self, request, *args, **kwargs):
        raw = request.body[:CSP_REPORT_MAX_BYTES]
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        violation = _extract_violation(payload)

        blocked = str(violation.get("blocked-uri") or violation.get("blockedURL") or "-")[:200]
        directive = str(violation.get("violated-directive") or violation.get("effectiveDirective") or "-")[:80]
        document = str(violation.get("document-uri") or violation.get("documentURL") or "-")[:200]

        from django.core.cache import cache

        # 节流键用哈希：directive/document 含空格与引号，直接拼进缓存键对 memcached
        # 非法（CacheKeyWarning），且长度不可控
        ident = hashlib.md5(f"{directive}|{document}".encode("utf-8")).hexdigest()[:16]
        throttle_key = f"csp_report_{ident}"
        if cache.add(throttle_key, 1, CSP_REPORT_LOG_THROTTLE_SECONDS):
            logger.warning("CSP violation: directive=%s blocked=%s document=%s", directive, blocked, document)

        response = HttpResponse(status=204)
        response[CSP_REPORT_HEADER] = "logged"
        return response

    def get(self, request, *args, **kwargs):
        """GET 仅用于探测端点存在（浏览器不会用 GET 上报）。"""
        return ApiResponse(detail=_("CSP report endpoint"))
