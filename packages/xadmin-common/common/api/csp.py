#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""CSP 违规上报端点（S3 观察期用）。

浏览器按策略里的 `report-uri` 把违规 POST 过来（无登录态，鉴权类不适用）。
本端点做三件事：
1. 解析（`application/csp-report` / `application/reports+json` 两种信封）并打 WARNING 日志，
   供观察期在 `data/logs/server.log` 里统计哪些指令会被真实触发；
2. **合成上报隔离**：非真实浏览器来源（缺 document-uri、脚本 UA、非本站文档域）只记 INFO，
   不进「CSP violation」统计——否则探测/测试流量会让「连续 7 天清零」的切 enforce 判据永远不成立；
3. 按「指令 + 文档路径」做 60s 日志节流，避免单个页面刷爆日志。

刻意不落库：观察期结束（切 enforce）后该端点可关（`CSP_REPORT_URI` 置空即不下发）。
"""

import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from common.core.response import ApiResponse
from common.settings_contract import kernel_setting

logger = logging.getLogger("xadmin")

CSP_REPORT_HEADER = "X-CSP-Report"
CSP_REPORT_LOG_THROTTLE_SECONDS = 60
#: 单次上报体上限（浏览器上报体很小，超限直接截断，防超大 body 攻击）
CSP_REPORT_MAX_BYTES = 8192

#: 真实浏览器上报必带 document-uri，这些取值即「没有文档上下文」
PLACEHOLDER_DOCUMENTS = frozenset({"", "-", "null", "unknown", "about:blank"})
#: 非浏览器 UA 特征（脚本 / 探测 / 测试客户端），命中即判为合成上报
NON_BROWSER_UA_PATTERN = re.compile(
    r"(python-requests|urllib|curl/|wget|httpie|guzzle|go-http-client|okhttp|axios|"
    r"node-fetch|libwww-perl|postmanruntime|django/test)",
    re.IGNORECASE,
)


def _normalize_host(value: str) -> str:
    """归一 host：小写、去端口、剥 IPv6 方括号（``[::1]:8000`` → ``::1``）。"""
    value = str(value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end]
    if value.count(":") == 1:  # host:port；IPv6 裸地址含多个冒号，不按端口切
        value = value.split(":")[0]
    return value


def _host_allowed(host: str, allowed: Any) -> bool:
    """按 ``ALLOWED_HOSTS`` 语义判定 host 归属（支持前导点子域通配）。"""
    host = _normalize_host(host)
    for item in allowed:
        item = _normalize_host(item)
        if item.startswith("."):
            # 前导点 = 该域及其子域（Django ALLOWED_HOSTS 语义）
            if host == item[1:] or host.endswith(item):
                return True
        elif host == item:
            return True
    return False


def _synthetic_reason(document: str, user_agent: str, request_host: str = "") -> str:
    """判定上报是否为「合成上报」（非真实浏览器）。

    返回空串 = 真实违规，计入切 enforce 的清零判据；非空 = 合成原因，只记 INFO。

    背景：2026-09-15 实测当日 69 条违规**全部**为合成上报（`document=https://example.com/#/...`、
    `https://x/`、`document=-`，与单测字面量一致），真实浏览器违规数为 0——判据在隔离完成前不可判。

    「本站」基准优先取上报请求自身的 Host（report-uri 是同源相对路径，故 document-uri 应同源），
    再并上 `ALLOWED_HOSTS`：后者常为空或通配（无法判定归属），单靠它会让域名判据整体失效。
    两侧都按 Django 的 host 语义归一比较（端口 / IPv6 字面量 / 前导点子域通配），
    避免把真实违规误判成合成上报而丢弃判据。
    """
    if document in PLACEHOLDER_DOCUMENTS:
        return "missing-document-uri"
    if not user_agent or NON_BROWSER_UA_PATTERN.search(user_agent):
        return "non-browser-agent"
    host = urlparse(document).hostname
    if not host:
        return "unparsable-document-uri"
    allowed = {request_host} if request_host else set()
    for item in kernel_setting("ALLOWED_HOSTS") or []:
        item = str(item)
        if item == "*":
            return ""  # 通配配置无法判定归属，宁可多记不漏记真实违规
        allowed.add(item)
    if allowed and not _host_allowed(host, allowed):
        return "foreign-document-host"
    return ""


def _extract_violation(payload: Any) -> dict[str, Any]:
    """兼容两种上报信封：`{"csp-report": {...}}`（CSP2）与数组式 reports（CSP3）。

    非 dict 的中间结果一律收敛回退为 dict：本端点是 AllowAny 公开入口，
    畸形 body（如 ``{"reports": [{"body": "str"}]}``）不能把非 dict 传给调用方。
    """
    if not isinstance(payload, dict):
        return {}
    report = payload.get("csp-report")
    if isinstance(report, dict):
        return report
    reports = payload.get("reports") or payload.get("body")
    if isinstance(reports, list) and reports and isinstance(reports[0], dict):
        body = reports[0].get("body")
        return body if isinstance(body, dict) else reports[0]
    return payload


class CSPReportAPIView(GenericAPIView):
    """CSP 违规上报（无鉴权：浏览器不带登录态）"""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        # 只取前 CSP_REPORT_MAX_BYTES 字节用于解析（超出部分丢弃）；整体读取上限由
        # DATA_UPLOAD_MAX_MEMORY_SIZE 兜底
        raw = request.body[:CSP_REPORT_MAX_BYTES]
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace") or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        violation = _extract_violation(payload)

        blocked = str(violation.get("blocked-uri") or violation.get("blockedURL") or "-")[:200]
        directive = str(violation.get("violated-directive") or violation.get("effectiveDirective") or "-")[:80]
        document = str(violation.get("document-uri") or violation.get("documentURL") or "-")[:200]
        user_agent = str(request.META.get("HTTP_USER_AGENT") or "")[:200]

        # 合成上报不计入违规统计：保留 INFO 留痕（原始字段不丢），但不污染切 enforce 的判据
        reason = _synthetic_reason(document, user_agent, request.get_host())
        if reason:
            logger.info(
                "CSP synthetic report ignored: reason=%s directive=%s blocked=%s document=%s",
                reason,
                directive,
                blocked,
                document,
            )
            response = HttpResponse(status=204)
            response[CSP_REPORT_HEADER] = "ignored"
            return response

        from django.core.cache import cache

        # 节流键用哈希：directive/document 含空格与引号，直接拼进缓存键对 memcached
        # 非法（CacheKeyWarning），且长度不可控
        ident = hashlib.md5(f"{directive}|{document}".encode(), usedforsecurity=False).hexdigest()[:16]
        throttle_key = f"csp_report_{ident}"
        if cache.add(throttle_key, 1, CSP_REPORT_LOG_THROTTLE_SECONDS):
            logger.warning("CSP violation: directive=%s blocked=%s document=%s", directive, blocked, document)

        response = HttpResponse(status=204)
        response[CSP_REPORT_HEADER] = "logged"
        return response

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """GET 仅用于探测端点存在（浏览器不会用 GET 上报）。"""
        return ApiResponse(detail=_("CSP report endpoint"))
