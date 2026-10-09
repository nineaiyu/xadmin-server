#!/usr/bin/env python
# -*- coding:utf-8 -*-
"""任务中心：统一任务列表 + 协作式取消 + 白名单重跑。

只读聚合三类记录（``TaskExecution`` / ``ExportRecord`` / ``ImportRecord``），
**不建新表**；数据域与下载中心同口径（超管全量，其余仅本人创建的记录）。

- ``GET  /api/task/unified``：跨类型列表（类型/状态/关键字/时间范围 + 分页）；
- ``POST /api/task/unified/cancel``：取消（PENDING 立即终态；RUNNING 走协作点）；
- ``POST /api/task/unified/rerun``：重跑（白名单：导出 / 导入 / 报表）。

顶栏现有任务日志抽屉保留为快捷入口；本页是长任务的统一入口。
"""

from typing import Any

from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import action
from rest_framework.viewsets import GenericViewSet

from common.core.response import ApiResponse
from common.swagger.utils import get_default_response_schema
from task.utils import task_center


def _int_param(value: Any, default: int, minimum: int = 1, maximum: int = 100) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


class SystemTaskCenterViewSet(GenericViewSet):
    """任务中心：统一列表 / 取消 / 重跑（三类记录聚合，不建新表）。"""

    @extend_schema(responses=get_default_response_schema())
    def list(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """统一任务列表：``type`` 多选（task/export/import）、``status``/``keyword``/时间范围可过滤。"""
        types = [item.strip() for item in str(request.query_params.get("type") or "").split(",") if item.strip()]
        params = request.query_params
        # 时间范围同时接受列表页惯例的 created_time_after/before 与简写 start/end
        rows, total = task_center.unified_rows(
            request.user,
            types=types or None,
            status=str(params.get("status") or "").strip(),
            keyword=str(params.get("keyword") or "").strip(),
            creator=str(params.get("creator") or "").strip(),
            start=parse_datetime(str(params.get("created_time_after") or params.get("start") or "")),
            end=parse_datetime(str(params.get("created_time_before") or params.get("end") or "")),
            page=_int_param(params.get("page"), 1, minimum=1, maximum=10000),
            size=_int_param(params.get("size"), 15),
        )
        return ApiResponse(data={"results": rows, "total": total})

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="cancel")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def cancel(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """取消任务：PENDING 立即置 REVOKED；RUNNING 下 revoke 并在安全点收敛（协作式）。"""
        result = task_center.cancel_record(
            request.user,
            str(request.data.get("type") or "").strip(),
            str(request.data.get("pk") or "").strip(),
        )
        if not result.get("ok"):
            return ApiResponse(code=1001, detail=result.get("detail") or _("Action failed"))
        return ApiResponse(detail=result.get("detail"))

    @extend_schema(responses=get_default_response_schema())
    @action(methods=["post"], detail=False, url_path="rerun")  # type: ignore[untyped-decorator]  # 第三方装饰器（celery / django / DRF）无类型存根：函数自身标注完整，此处不因装饰器降级
    def rerun(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        """重跑任务（白名单：导出 / 导入 / 报表）：新记录 + 同链路重放，产物与审计同源。"""
        result = task_center.rerun_record(
            request.user,
            str(request.data.get("type") or "").strip(),
            str(request.data.get("pk") or "").strip(),
        )
        if not result.get("ok"):
            return ApiResponse(code=1001, detail=result.get("detail") or _("Action failed"))
        return ApiResponse(data=result.get("data") or {}, detail=result.get("detail"))
