#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""备份失败告警上报端点（S2）。

- 调用方：`utils/db_backup.sh`（db-backup 容器内的 bash 循环，无 JWT 登录态）；
- 鉴权：独立共享令牌 `X-Backup-Token`（对应 `SysConfig.BACKUP_ALERT_TOKEN`，
  默认空 = 未启用，此时端点恒 403），比较用 `secrets.compare_digest`；
- 不暴露内部信息：令牌不对一律 403 + 通用文案；
- 频率由 `notify_backup_failure` 的 60s 节流兜底（防脚本循环重试刷告警）。
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


class BackupAlertAPIView(GenericAPIView):
    """备份失败上报（无登录态：独立令牌鉴权）"""

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
                description="备份失败事件（source/event/host/time/detail）",
            )
        ),
        responses=get_default_response_schema(),
    )
    def post(self, request, *args, **kwargs):
        """上报备份失败事件：节流发布站内信/邮件告警（60s 同源去重）"""
        from common.backup_alert import notify_backup_failure
        from common.core.config import SysConfig

        expected = str(SysConfig.BACKUP_ALERT_TOKEN or "")
        provided = str(request.headers.get("X-Backup-Token") or "")
        if not expected or not provided or not secrets.compare_digest(provided, expected):
            return ApiResponse(code=403, status=403, detail=_("Invalid backup alert token"))

        payload = request.data if isinstance(request.data, dict) else {}
        published = notify_backup_failure(
            {
                "source": payload.get("source") or "db-backup",
                "event": payload.get("event") or _("Backup failure"),
                "host": payload.get("host") or "",
                "time": payload.get("time") or "",
                "detail": payload.get("detail") or "",
            }
        )
        return ApiResponse(data={"published": published}, detail=_("Backup alert received"))
