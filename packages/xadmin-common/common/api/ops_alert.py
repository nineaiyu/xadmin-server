#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""运维告警上报端点（A1）。

- 调用方：宿主侧 `ops/oom_alert.sh`（监听 `docker events` 的 `oom` 事件，无登录态）；
- 鉴权：独立共享令牌 `X-Ops-Token`（对应 `SysConfig.OPS_ALERT_TOKEN`，
  默认空 = 未启用，此时端点恒 403）；
- 不暴露内部信息：令牌不对一律 403 + 通用文案；
- 频率由 `notify_ops_alert` 的 60s 节流兜底（防 watcher 重连重放刷告警）；
- 令牌比较与载荷整形收敛在 `AlertReportAPIView` 基类（与备份告警端点共用）。
"""

from typing import Any

from django.utils.translation import gettext_lazy as _
from drf_spectacular.plumbing import build_basic_type, build_object_type
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiRequest, extend_schema

from common.api.alert_report import AlertReportAPIView
from common.swagger.utils import get_default_response_schema


class OpsAlertAPIView(AlertReportAPIView):
    """运维告警上报（无登录态：独立令牌鉴权）"""

    token_setting = "OPS_ALERT_TOKEN"
    token_header = "X-Ops-Token"
    default_source = "ops"
    default_event = _("Unknown event")
    invalid_token_detail = _("Invalid ops alert token")
    received_detail = _("Ops alert received")

    def get_notify(self) -> Any:
        from common.ops_alert import notify_ops_alert

        return notify_ops_alert

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
    def post(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """上报运维事件：节流发布站内信/邮件告警（60s 同来源同事件去重）"""
        return super().post(request, *args, **kwargs)
