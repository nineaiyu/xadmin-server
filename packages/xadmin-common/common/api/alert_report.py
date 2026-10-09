#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""共享令牌鉴权的告警上报端点基类。

备份失败（backup.py）与运维事件（ops_alert.py）两个上报端点此前逐行同构：
无登录态 + 独立共享令牌 + 节流发布。此处收敛公共部分，子类只声明差异项
（令牌配置键 / 请求头 / 默认来源与事件 / 文案 / 通知函数）。

约定：

- 令牌为空 = 未启用，端点恒 403；令牌不匹配一律 403 + 通用文案（不暴露内部信息）；
- ``secrets.compare_digest`` 两侧先编码为 bytes：非 ASCII 令牌或请求头会让它抛
  ``TypeError``，把「令牌错误」混成「服务异常」（500）；
- 频率由各 notify 函数的 60s 节流兜底（防调用方循环重试刷告警）。
"""

import secrets
from typing import Any

from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from common.core.response import ApiResponse


class AlertReportAPIView(GenericAPIView):
    """告警上报端点基类（无登录态：独立令牌鉴权；子类声明差异属性）。"""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    #: 令牌配置键（``SysConfig`` 属性名）
    token_setting = ""
    #: 请求头名
    token_header = ""
    #: 默认来源标识（payload 未给 source 时）
    default_source = ""
    #: 默认事件文案（payload 未给 event 时）
    default_event: Any = ""
    #: 无效令牌文案
    invalid_token_detail: Any = ""
    #: 受理成功文案
    received_detail: Any = ""

    def get_notify(self) -> Any:
        """返回上报处理函数（payload -> 是否真正发布）；延迟 import 由子类承担。"""
        raise NotImplementedError

    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        from common.core.config import SysConfig

        expected = str(getattr(SysConfig, self.token_setting) or "")
        provided = str(request.headers.get(self.token_header) or "")
        if not expected or not provided or not secrets.compare_digest(provided.encode(), expected.encode()):
            return ApiResponse(code=403, status=403, detail=self.invalid_token_detail)

        payload = request.data if isinstance(request.data, dict) else {}
        published = self.get_notify()(
            {
                "source": payload.get("source") or self.default_source,
                "event": payload.get("event") or self.default_event,
                "host": payload.get("host") or "",
                "time": payload.get("time") or "",
                "detail": payload.get("detail") or "",
            }
        )
        return ApiResponse(data={"published": published}, detail=self.received_detail)
