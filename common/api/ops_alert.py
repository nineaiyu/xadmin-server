#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""运维告警上报端点（A1）。

- 调用方：宿主侧 `utils/oom_alert.sh`（监听 `docker events` 的 `oom` 事件，无登录态）；
- 鉴权：独立共享令牌 `X-Ops-Token`（对应 `SysConfig.OPS_ALERT_TOKEN`，
  默认空 = 未启用，此时端点恒 403），比较用 `secrets.compare_digest`；
- 不暴露内部信息：令牌不对一律 403 + 通用文案；
- 频率由 `notify_ops_alert` 的 60s 节流兜底（防 watcher 重连重放刷告警）。
"""

import secrets

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import AllowAny

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema


class OpsAlertAPIView(GenericAPIView):
    """运维告警上报（无登录态：独立令牌鉴权）"""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    @extend_schema(
        request=OpenApiRequest(
            build_object_type(
                properties={
                    "source": build_basic_type(OpenApiTypes.STR),
                    "event": build_basic_type(OpenApiTypes.STR),
                    "host": build_basic_type(OpenApiTypes.STR),
                    "time": build_basic_type(OpenApiTypes.STR),
                    "detail": build_basic_type(OpenApiTypes.STR),
                },
                required=["event"],
                description="运维事件（source/event/host/time/detail）",
            )
        ),
        responses=get_default_response_schema(),
    )
    def post(self, request, *args, **kwargs):
        """上报运维事件：节流发布站内信/邮件告警（60s 同来源同事件去重）"""
        from common.core.config import SysConfig
        from common.ops_alert import notify_ops_alert

        expected = str(SysConfig.OPS_ALERT_TOKEN or "")
        provided = str(request.headers.get("X-Ops-Token") or "")
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            return ApiResponse(code=403, status=403, detail=_("Invalid ops alert token"))

        payload = request.data if isinstance(request.data, dict) else {}
        published = notify_ops_alert(
            {
                "source": payload.get("source") or "ops",
                "event": payload.get("event") or _("Unknown event"),
                "host": payload.get("host") or "",
                "time": payload.get("time") or "",
                "detail": payload.get("detail") or "",
            }
        )
        return ApiResponse(data={"published": published}, detail=_("Ops alert received"))
